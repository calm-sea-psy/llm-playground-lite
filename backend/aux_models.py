"""측정 항목이 **채점 대상이 아닌 보조 모델**(임베딩 모델 등)을 실제로 불렀는지 기록한다.

같은 지표라도 보조 모델을 썼는지에 따라 값의 뜻이 달라진다(예: 일관성 유사도를 임베딩으로 쟀는가).
그래서 "쓰기로 했다"는 설정이 아니라 **호출이 실제로 일어났는가**를 항목에 남긴다 — 호출 경로의 한 곳
(`ollama_client.embed`)에서 `record`하고, 실행기가 항목마다 `collect`로 모은다. 모으는 중이 아닐 때
(평가 명령·단위 테스트)의 호출은 아무것도 남기지 않는다.

`contextvars`라 항목을 실행하는 스레드 안에서만 모인다. 항목 코드가 스레드를 새로 띄워 그 안에서
부르면 잡히지 않는다 — 지금 그런 항목은 없다.
"""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_collecting: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("aux_models", default=None)


def record(role: str, model: str) -> None:
    used = _collecting.get()
    if used is not None and not any(u["role"] == role and u["model"] == model for u in used):
        used.append({"role": role, "model": model})


@contextmanager
def collect() -> Iterator[list[dict[str, Any]]]:
    used: list[dict[str, Any]] = []
    token = _collecting.set(used)
    try:
        yield used
    finally:
        _collecting.reset(token)
