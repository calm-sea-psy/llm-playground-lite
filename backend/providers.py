"""채팅 모델 프로바이더 한 겹.

Ollama(OpenAI 호환 `/v1`)와 클라우드 베이스라인(`gpt-5.6-luna`, 역시
OpenAI Chat Completions 형태)을 같은 인터페이스로 감싼다. 성능 테스트
실행·채점 경로(`test_runner.py`, `quality_runner.py`)가 어느 쪽이든 이
모듈을 통해서만 모델을 부르게 해두면, 실행·채점 로직을 프로바이더별로
다시 짤 필요가 없다.

일반 대화(`/api/chat`)는 지금처럼 `main.py`의 `client`를 직접 쓴다 — 거기는
Ollama 전용으로 남는다. 이 모듈은 "어느 프로바이더든 상관없이 돌아야
하는" 성능 테스트 쪽 전용이다.

**클라우드 쪽은 실측으로 확인한 두 가지 제약이 있다** (`quality_runner.ask_model`
참고). ① `gpt-5.6-luna`는 reasoning 계열이라 `temperature`/`top_p` 커스텀
값을 거부한다("Only the default (1) value is supported") — 그래서 클라우드
경로는 샘플링 파라미터를 아예 보내지 않는다. ② `max_tokens` 대신
`max_completion_tokens`를 써야 한다("Unsupported parameter"). 둘 다 실제
호출로 확인했다.
"""

import os
from dataclasses import dataclass

from openai import OpenAI


# 프로바이더마다 네이티브 경로(고정 샘플링·num_ctx·think)를 타는가. `quality_runner.ask_model`의 분기와 리포트의
# `기준선은 샘플링을 고정하지 않는다`가 같은 사실을 본다 — 결과에 기록된 `sampling`은 설정이지 적용이 아니다
# (클라우드 경로는 그 값을 보내지 않는다).
NATIVE_API: dict[str, bool] = {"ollama": True, "cloud": False}


def applies_fixed_sampling(name: str | None) -> bool | None:
    """그 프로바이더로 잰 실행이 고정 샘플링을 받았는가. 프로바이더를 모르면 None."""
    return NATIVE_API.get(name) if name else None


@dataclass(frozen=True)
class Provider:
    name: str
    client: OpenAI
    supports_native_api: bool  # /api/ps, /api/show 등 Ollama 네이티브 호출 가능 여부


def _ollama_provider() -> Provider:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    client = OpenAI(base_url=base_url, api_key=os.getenv("OLLAMA_API_KEY", "ollama"))
    return Provider(name="ollama", client=client, supports_native_api=NATIVE_API["ollama"])


def _cloud_provider() -> Provider:
    """베이스라인용(`gpt-5.6-luna`). base URL은 OpenAI
    기본값을 그대로 쓴다 — 이 모델은 OpenAI Chat Completions 표준 엔드포인트에
    있다."""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return Provider(name="cloud", client=client, supports_native_api=NATIVE_API["cloud"])


_REGISTRY = {"ollama": _ollama_provider, "cloud": _cloud_provider}


def get_provider(name: str = "ollama") -> Provider:
    try:
        factory = _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 프로바이더: {name}") from exc
    return factory()
