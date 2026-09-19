"""성능 지표 측정 함수들.

`test_runner.py`가 순서대로 호출한다. Ollama 호출은 전부 `ollama_client`의
네이티브 스트리밍 경유(verify-ollama-client 규칙) — OpenAI 호환 `/v1`은
`prompt_eval_duration`/`eval_duration`을 안 줘서 tok/s·prefill을 정의대로
(토큰 수 ÷ 소요 시간) 계산할 수 없다.

각 함수는 그 항목에서 나온 값들을 dict로 돌려준다. 실패(타임아웃 등)하면
예외를 던지고, `test_runner.py`가 그 항목만 실패로 표시한 뒤 다음으로 넘어간다.
"""

import statistics
import uuid
import time
from typing import Any

import bench_config as cfg
import ollama_client

_NS_PER_SEC = 1_000_000_000


def _consume_stream(
    model: str, prompt: str, *, num_predict: int, timeout: float, user_system: str | None = None
) -> tuple[float, str, dict[str, Any] | None]:
    """스트림을 끝까지 받아 (TTFT초, 전체 텍스트, 마지막 청크)를 돌려준다.

    `user_system`은 `프롬프트 실험` 모드에서만 온다 — 속도 탐침에도 건다. 프롬프트를 붙이면
    TTFT·prefill이 나빠지는 것은 실사용에서 실제로 겪는 대가라 감추지 않는다."""
    start = time.monotonic()
    ttft: float | None = None
    parts: list[str] = []
    final: dict[str, Any] | None = None
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    if user_system:
        messages.insert(0, {"role": "system", "content": user_system})
    for chunk in ollama_client.stream_chat(
        model,
        messages,
        num_ctx=cfg.NUM_CTX,
        num_predict=num_predict,
        sampling=cfg.SAMPLING,
        think=cfg.THINK,
        timeout=timeout,
    ):
        content = chunk.get("message", {}).get("content", "")
        if content:
            if ttft is None:
                ttft = time.monotonic() - start
            parts.append(content)
        if chunk.get("done"):
            final = chunk
    if ttft is None:
        ttft = time.monotonic() - start  # 내용 없이 바로 done (극단적인 경우)
    return ttft, "".join(parts), final


def _is_complete(final: dict[str, Any] | None) -> bool:
    """정상 종료(`stop`)와 의도된 출력 상한 도달(`length`)은 완결로 친다 —
    `length`는 우리가 건 `num_predict` 통제이지 모델의 결함이 아니다."""
    return final is not None and final.get("done_reason") in ("stop", "length")


def measure_model_load(model: str, user_system: str | None = None) -> dict[str, Any]:
    """언로드 → 재로드하며 첫 응답까지 걸린 시간을 로드 시간으로 기록한다.

    `unload()`도 `TIMEOUT_LONG`을 쓴다 — 실측(gemma-4-E4B, 6GB) 결과 이 호출이
    이번 실행에서 처음 그 모델을 건드리는 순간이면 Ollama가 `keep_alive:0` 요청에
    응답하기 전에 먼저 콜드 로드부터 마쳐야 했다. `TIMEOUT_SHORT`(30초)로는 이
    콜드 로드가 다 끝나기 전에 타임아웃 나서 `model_load` 항목 자체가 실패로
    잡혔다 — 아이러니하게도 "로드 시간을 재려는" 항목이 "로드가 오래 걸려서" 실패한 것.
    """
    ollama_client.unload(model, timeout=cfg.TIMEOUT_LONG)
    for _ in range(10):  # /api/ps가 즉시 안 비워질 수 있어 짧게 폴링한다
        if not ollama_client.is_loaded(model):
            break
        time.sleep(0.3)
    ttft, _text, final = _consume_stream(model, "ok", num_predict=1, timeout=cfg.TIMEOUT_LONG, user_system=user_system)
    return {"load_time_sec": ttft, "load_complete": _is_complete(final)}


def measure_warmup(model: str, short_prompt: str, user_system: str | None = None) -> None:
    """결과를 버리는 워밍업. 실패하면 예외가 그대로 올라가 항목이 failed로 잡힌다."""
    _consume_stream(model, short_prompt, num_predict=cfg.NUM_PREDICT, timeout=cfg.TIMEOUT_SHORT, user_system=user_system)


def measure_memory(model: str) -> dict[str, Any]:
    """`/api/ps`에서 총 크기와 VRAM 오프로드 비율, **실제로 잡힌 컨텍스트 길이**를 읽는다. 컨텍스트는 요청한 `num_ctx`가
    아니라 Ollama가 올린 값이다 — 모델이 올라간 뒤에야 보여 실행 시작 때 남기는 식별값과 따로 둔다. 순위 지표가 아니라 기록이다.
    시스템 RAM에 올라간 몫은 `memory_bytes − vram_bytes`로 나온다(한 사실은 한 곳에서만 — 따로 저장하지 않는다)."""
    loaded = {m.get("name"): m for m in ollama_client.list_loaded()}
    info = loaded.get(model)
    if info is None:
        return {"memory_bytes": None, "vram_bytes": None, "vram_offload_ratio": None, "loaded_context_length": None}
    size = info.get("size") or 0
    vram = info.get("size_vram") or 0
    return {
        "memory_bytes": size,
        "vram_bytes": vram,
        "vram_offload_ratio": (vram / size) if size else None,
        "loaded_context_length": info.get("context_length"),
    }


def _uncached(prompt: str) -> str:
    """앞머리에 한 번만 쓰는 줄을 붙여 **프롬프트 캐시를 비켜 간다.**

    캐시는 앞머리가 같으면 맞는다. 짧은 탐침은 같은 글을 열 번 반복하고, 컨텍스트 단계는 2000자리가
    4000자리의 앞부분과 같아서(같은 채움 문단을 늘린 것이다) 두 번째 호출부터는 프리필을 거의 건너뛴다 —
    그렇게 잰 TTFT는 `처음 받아 본 입력`의 값이 아니다(실측: 4천 토큰 문서에서 0.52초 대 0.055초).

    뒤에 붙이지 않고 앞에 붙이는 까닭은 캐시가 **앞에서부터** 맞기 때문이다."""
    return f"[{uuid.uuid4().hex}]\n{prompt}"


def measure_short_probe(model: str, short_prompt: str, user_system: str | None = None) -> dict[str, Any]:
    """짧은 탐침을 `REPEAT_COUNT`회 반복 — TTFT·tok/s는 중앙값, tok/s 편차는
    성능 변동성으로 쓴다(이미 하는 반복 측정에서 공짜로 나온다)."""
    ttfts: list[float] = []
    tps_list: list[float] = []
    output_tokens: list[int] = []
    complete_count = 0
    last_prompt = None
    for _ in range(cfg.REPEAT_COUNT):
        last_prompt = _uncached(short_prompt)  # 반복마다 앞머리가 달라 열 번 다 찬 캐시다
        ttft, _text, final = _consume_stream(
            model, last_prompt, num_predict=cfg.NUM_PREDICT, timeout=cfg.TIMEOUT_SHORT, user_system=user_system
        )
        ttfts.append(ttft)
        if _is_complete(final):
            complete_count += 1
        if final and final.get("eval_count") and final.get("eval_duration"):
            tps_list.append(final["eval_count"] / (final["eval_duration"] / _NS_PER_SEC))
            output_tokens.append(final["eval_count"])
    # 캐시가 맞았을 때의 값도 하나 남긴다 — 같은 프롬프트를 한 번 더 보낸다. 둘을 나란히 둬야
    # `처음 받아 본 입력`과 `이어지는 대화`의 지연을 구분해 읽을 수 있다
    cached_ttft = None
    if last_prompt is not None:
        cached_ttft, _text, _final = _consume_stream(
            model, last_prompt, num_predict=cfg.NUM_PREDICT, timeout=cfg.TIMEOUT_SHORT, user_system=user_system
        )
    return {
        # `ttft_sec`는 이제 **찬 캐시** 값이다(반복마다 앞머리가 다르다). 옛 실행의 같은 이름은 캐시가 맞은 값이라
        # 나란히 읽으면 안 된다 — 실행 조건의 `ttft_method`가 그 둘을 가른다
        "ttft_sec": statistics.median(ttfts) if ttfts else None,
        "ttft_cached_sec": cached_ttft,
        "tok_per_sec": statistics.median(tps_list) if tps_list else None,
        # 표본 1개면 분산이 정의되지 않으므로 0 — REPEAT_COUNT가 1로 줄어들 때의 방어.
        "tok_per_sec_stdev": statistics.pstdev(tps_list) if len(tps_list) > 1 else 0.0,
        # 중앙값·표준편차로 뭉개기 전의 반복 원본값을 그대로 남긴다.
        # 이미 계산해둔 값을 버리지 않는 것뿐이라 비용이 없고, 분포를 어떻게
        # 그릴지(에러바냐 박스플롯이냐)는 표본 수를 보고 쓰는 쪽이 정한다.
        # 이 필드가 생기기 전에 저장된 실행에는 없으므로, 쓰는 쪽에서 없으면 평균±표준편차로 근사한다.
        "tok_per_sec_samples": tps_list,
        "output_tokens_median": statistics.median(output_tokens) if output_tokens else None,
        "short_probe_complete_count": complete_count,
        "short_probe_total_count": cfg.REPEAT_COUNT,
    }


def measure_context_stage(model: str, stage_text: str, user_system: str | None = None) -> dict[str, Any]:
    """긴 입력 하나를 보내 prefill 처리량과 그 길이에서의 생성 tok/s를 잰다.

    **단계마다 앞머리를 새로 단다**(`_uncached`) — 단계 텍스트가 서로의 앞부분이라 그대로 보내면 뒤 단계가
    앞 단계의 캐시를 물려받아, 입력이 길수록 프리필이 빨라지는 것처럼 보인다(실측: 9,145 → 15,776 tok/s).
    생성 tok/s는 캐시와 무관하다(`eval_count / eval_duration`) — 유지율은 그 값으로 낸다."""
    prompt = _uncached(stage_text)
    ttft, _text, final = _consume_stream(
        model, prompt, num_predict=cfg.NUM_PREDICT, timeout=cfg.TIMEOUT_LONG, user_system=user_system
    )
    if final is None:
        raise TimeoutError("응답을 받지 못했습니다(타임아웃 또는 빈 스트림)")
    prefill_tps = None
    if final.get("prompt_eval_count") and final.get("prompt_eval_duration"):
        prefill_tps = final["prompt_eval_count"] / (final["prompt_eval_duration"] / _NS_PER_SEC)
    gen_tps = None
    if final.get("eval_count") and final.get("eval_duration"):
        gen_tps = final["eval_count"] / (final["eval_duration"] / _NS_PER_SEC)
    cached_ttft, _t, _f = _consume_stream(
        model, prompt, num_predict=cfg.NUM_PREDICT, timeout=cfg.TIMEOUT_LONG, user_system=user_system
    )
    return {
        "ttft_sec": ttft,
        "ttft_cached_sec": cached_ttft,
        "prompt_tokens": final.get("prompt_eval_count"),
        "cached_tokens": final.get("prompt_eval_cached_count"),
        "prefill_tok_per_sec": prefill_tps,
        "tok_per_sec": gen_tps,
        "output_tokens": final.get("eval_count"),
        "complete": _is_complete(final),
    }
