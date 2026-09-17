# -*- coding: utf-8 -*-
"""GPU 소비전력과 응답 시간 — 로컬 실행의 **계측 구간**에서 호출당 추가 전력량과 응답 시간 중앙값을 낸다.

실측(RTX 5080 Laptop, `nvidia-smi power.draw`)에서 두 가지를 봤다:
- **값이 약 0.5초마다 갱신된다** — 1초 안팎인 생성에는 서로 다른 값이 한두 개뿐이다.
- **읽힌 값이 활동보다 늦다** — 생성 부하의 정점이 호출이 끝난 뒤에 읽혔다.
그래서 **호출마다 창을 자르지 않고** 구간을 적분하고, 조각을 닫을 때 꼬리를 붙여 늦게 읽히는 값까지 넣는다.
호출별 값은 믿을 수 없어 남기지 않는다. `power.draw.average`는 이 기계에서 `1.00W`로 나와 쓰지 않는다.

- **구간은 여러 조각일 수 있다** — 두 바퀴 사이의 모델 로드, 요약 모델이 끼는 호출처럼 후보 모델의 문항 호출이 아닌 활동은
  조각을 끊어 뺀다. 조각마다 따로 적분해 합친다(한 구간으로 적분하면 그 사이의 전력이 호출당 전력에 섞인다).
- **분모는 구간이 열려 있을 때 일어난 후보 호출이다** — 에너지를 뺀 조각의 호출을 분모에 남기면 호출당 전력이 낮게 나온다.
  응답 시간도 같은 호출에서 센다(두 참고 값이 같은 호출을 본다).
- **대기 전력**은 모델이 올라간 상태로 구간 앞뒤에서 잰다 — 그 선택이 `호출당 추가`를 **생성에 든 몫**으로 만든다
  (모델을 올려 둔 채 대기하는 전력은 호출이 없어도 나가는 고정 쪽이다).
- **GPU만 잰다** — CPU와 나머지 시스템이 빠져 실제보다 낮게 나온다. 그 방향을 결과에 함께 적는다.
- **와트시까지만** 적는다 — 돈으로 바꾸려면 전기요금 단가 가정이 필요하고, 그 가정은 두지 않았다.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

INTERVAL_SEC = 0.2
SETTLE_SEC = 2.0  # 앞선 활동의 전력이 가라앉기를 기다린다(실측: 호출 뒤 약 1.5초에 대기 수준)
IDLE_SEC = 3.0
TAIL_SEC = 3.0  # 늦게 읽히는 값까지 구간에 넣는다(실측: 지연 약 0.5초, 감쇠 약 1.5초)
BASIS = "GPU 기준 — CPU와 나머지 시스템이 빠져 실제보다 낮게 나온다"
NOT_CONVERTED = "와트시까지만 적었다 — 전기요금 단가 가정을 두지 않아 비용으로 바꾸지 않았다"

Sample = tuple[float, float]


def read_watts() -> float | None:
    """모든 NVIDIA GPU의 순간 소비전력 합(W). 도구가 없거나 값을 못 읽으면(`[N/A]`) None."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        values = [float(line.strip()) for line in out.strip().splitlines()]
    except ValueError:
        return None
    return sum(values) if values else None


class Sampler:
    """백그라운드에서 `interval`마다 전력을 읽어 `(시각, W)`를 모은다. 못 읽은 표본은 버린다."""

    def __init__(self, read: Callable[[], float | None] = read_watts, clock: Callable[[], float] = time.perf_counter,
                 interval: float = INTERVAL_SEC):
        self._read, self._clock, self._interval = read, clock, interval
        self._samples: list[Sample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = self._clock()
            watts = self._read()
            if watts is not None:
                self._samples.append((self._clock(), watts))
            self._stop.wait(max(0.0, self._interval - (self._clock() - began)))

    def stop(self) -> list[Sample]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        return list(self._samples)


def average(samples: list[Sample]) -> float | None:
    return sum(w for _, w in samples) / len(samples) if samples else None


def energy(samples: list[Sample]) -> tuple[float, float]:
    """사다리꼴로 적분한 (줄, 초). 표본이 둘보다 적으면 (0, 0) — 구간이 없다."""
    joules = sum((w0 + w1) / 2 * (t1 - t0) for (t0, w0), (t1, w1) in zip(samples, samples[1:]))
    span = samples[-1][0] - samples[0][0] if len(samples) > 1 else 0.0
    return joules, span


def summarize(segments: list[list[Sample]], idle_before: list[Sample], idle_after: list[Sample], calls: int) -> dict[str, Any]:
    """구간 전력량(대기 포함, 꼬리 포함)과 호출당 평균 추가 전력량. 조각마다 따로 적분해 합친다. 빼는 대기분은 **적분한 것과 같은
    구간 길이**(조각 길이의 합)로 곱한다 — 길이가 다르면 대기분이 덜 빠지거나 더 빠진다."""
    idle = average(idle_before + idle_after)
    parts = [energy(seg) for seg in segments]
    joules = sum(j for j, _ in parts)
    span = sum(t for _, t in parts)
    extra = None
    if idle is not None and span > 0 and calls > 0:
        extra = (joules - idle * span) / calls / 3600
    return {
        "segment_wh": joules / 3600 if span > 0 else None,  # 구간 전력량 — 대기 포함, 꼬리 포함
        "segment_sec": span,
        "segments": len(segments),
        "idle_watts": idle,
        "idle_watts_before": average(idle_before),
        "idle_watts_after": average(idle_after),
        "calls": calls,
        "extra_wh_per_call": extra,  # 호출당 평균 추가 전력량 — 한계 쪽은 이것뿐이다
        "samples": sum(len(seg) for seg in segments),
        "interval_sec": INTERVAL_SEC,
        "tail_sec": TAIL_SEC,
        "basis": BASIS,
        "unit_note": NOT_CONVERTED,
    }


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


class RunMeter:
    """한 실행의 계측 구간 — 전력과 응답 시간을 **같은 호출**에서 잰다.
    - `begin()`: 대기 전력을 재고 첫 조각을 연다
    - `pause()`: 꼬리를 붙이고 조각을 닫는다 · `resume()`: 앞선 활동이 가라앉기를 기다린 뒤 새 조각을 연다
    - `record_call(call)`: 후보 모델 호출 하나 — 조각이 열려 있을 때만 센다
    - `finish()`: 마지막 조각을 닫고 대기 전력을 다시 잰다 → `{"gpu_power": …, "call_timing": …}`
    GPU 전력을 못 읽는 기계에서도 응답 시간은 센다."""

    def __init__(self, read: Callable[[], float | None] = read_watts, sleep: Callable[[float], None] = time.sleep,
                 sampler: Callable[..., Sampler] = Sampler):
        self._read, self._sleep, self._sampler = read, sleep, sampler
        self._current: Sampler | None = None
        self._segments: list[list[Sample]] = []
        self._idle_before: list[Sample] = []
        self._elapsed: list[float] = []
        self._calls = 0
        self.unavailable = False
        self.started = False
        self.active = False

    def _idle(self) -> list[Sample]:
        self._sleep(SETTLE_SEC)
        sampler = self._sampler(read=self._read)
        sampler.start()
        self._sleep(IDLE_SEC)
        return sampler.stop()

    def _open(self) -> None:
        if not self.unavailable:
            self._current = self._sampler(read=self._read)
            self._current.start()
        self.active = True

    def begin(self) -> None:
        if self.started:
            return self.resume()
        self.started = True
        if self._read() is None:
            self.unavailable = True  # 이 기계에서 GPU 전력을 못 읽는다 — 재지 않은 이유를 결과에 적는다
        else:
            self._idle_before = self._idle()
        self._open()

    def pause(self) -> None:
        if not self.active:
            return
        self.active = False
        if self._current is not None:
            self._sleep(TAIL_SEC)
            self._segments.append(self._current.stop())
            self._current = None

    def resume(self) -> None:
        if self.active or not self.started:
            return
        if not self.unavailable:
            self._sleep(SETTLE_SEC)
        self._open()

    def record_call(self, call: dict[str, Any] | None) -> None:
        if not self.active or call is None:
            return
        self._calls += 1
        if (elapsed := call.get("elapsed_sec")) is not None:
            self._elapsed.append(elapsed)

    def finish(self) -> dict[str, Any]:
        self.pause()
        timing = {"calls": self._calls, "elapsed_median_sec": median(self._elapsed), "timed_calls": len(self._elapsed)}
        if self.unavailable or not self._segments:
            power = {"not_measured": "nvidia-smi로 GPU 소비전력을 읽지 못했다 — 전력 한계비용은 재지 않았다(0이 아니다)"}
        else:
            power = summarize(self._segments, self._idle_before, self._idle(), self._calls)
        return {"gpu_power": power, "call_timing": timing}
