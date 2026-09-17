"""실행 실패의 분류 (지표의 저장 상태는 넷으로 나눈다).

**모델이 답을 못 낸 것은 결과이고, 우리가 제대로 못 물어본 것은 결과가 아니다.**

| 원인 | 처리 |
|---|---|
| 타임아웃·빈 응답·형식이 깨진 답 — 모델 쪽 | 문항 0점 (이 모듈이 아니라 러너가 변형 단위로 처리) |
| 우리 코드 예외, 요청 구성 오류(4xx) | 지표 `failed` + `our_code` |
| Ollama 미기동·연결 끊김, 분류할 수 없는 모든 실패 | 지표 `failed` + `infra` |
| OOM처럼 원인이 분명한 것만 | 지표 `failed` + `model_machine` |

`model_machine`은 **재현되면 0점으로 확정**되는 유일한 원인이라, 붙이는 조건이
엄격하다 — 미리 정해둔 OOM 문구와 일치하고 **그리고** 항목 직전에 기록한
메모리 수치가 부족을 뒷받침할 때만(AND). 하나라도 없으면 `infra`다. 애매한
것을 `model_machine`으로 넣으면 그 애매함이 곧바로 0점이 된다.
"""

import re
import traceback
from typing import Any

import httpx2 as httpx

import ollama_client

OUR_CODE = "our_code"
INFRA = "infra"
MODEL_MACHINE = "model_machine"

# Ollama 0.34.0 기준 OOM 문구. 목록에 없는 문구는 아무리 OOM처럼 보여도 `infra`다
# — 버전이 바뀌면 문구도 바뀔 수 있으니 올릴 때 이 목록을 다시 확인한다.
OOM_PATTERNS_OLLAMA_VERSION = "0.34.0"
OOM_PATTERNS = [
    re.compile(r"out of memory", re.IGNORECASE),
    re.compile(r"requires more system memory", re.IGNORECASE),
    re.compile(r"unable to allocate \w+ buffer", re.IGNORECASE),
    re.compile(r"cudaMalloc failed", re.IGNORECASE),
]


class ModelFormatError(Exception):
    """모델 출력이 우리가 쓰기로 한 형식이 아니다(예: tool call의 `arguments`가
    dict가 아님). **모델 쪽 원인**이라 그 변형만 오답으로 처리하고 루프는 계속
    돈다 — 쓰기 전에 검증해서 이 예외로 바꿔두면, 검증을 **통과한 뒤** 난 예외는
    전부 우리 버그라고 저장 시점에 확정할 수 있다(traceback 없이도)."""


def _matches_oom(text: str) -> bool:
    return any(p.search(text) for p in OOM_PATTERNS)


def classify(exc: BaseException, memory: dict[str, Any] | None) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
        return INFRA
    if isinstance(exc, ollama_client.OllamaHTTPError):
        if 400 <= exc.status_code < 500:
            return OUR_CODE  # 우리가 요청을 잘못 만든 것(모델 능력 부재는 실행 전에 걸러낸다)
        if _matches_oom(exc.body) and (memory or {}).get("shortfall") is True:
            return MODEL_MACHINE
        return INFRA
    if isinstance(exc, httpx.HTTPError):
        return INFRA
    return OUR_CODE


def describe(exc: BaseException, memory: dict[str, Any] | None) -> dict[str, Any]:
    """결과 파일의 `item.failure`. 원문·본문·traceback은 단서일 뿐이고 판정은
    `cause` 하나다 — 나중에 사람이 원인을 좁혀볼 수 있게 원재료를 전부 남긴다."""
    return {
        "cause": classify(exc, memory),
        "message": str(exc),
        "response_body": exc.body if isinstance(exc, ollama_client.OllamaHTTPError) else None,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "memory": memory,
    }


def is_model_timeout(exc: BaseException) -> bool:
    """변형 단위 0점으로 넘길 모델 쪽 실패 — 응답이 제한 시간 안에 안 끝났다.
    **읽기** 타임아웃만이다: 연결 타임아웃은 서버에 붙지도 못한 것이라(Ollama
    미기동 등) 모델의 결과가 아니라 `infra`다."""
    return isinstance(exc, httpx.ReadTimeout)
