"""Ollama 네이티브 API(`/api/*`) 클라이언트.

모델 상세(`/api/show`), 로드된 모델과 메모리(`/api/ps`), 모델 설치·삭제(`/api/pull`, `/api/delete`),
임베딩(`/api/embed`, 일관성 판정기 후보 평가), 그리고 **채팅 스트리밍**(`/api/chat`)을 맡는다.

채팅은 측정도 앱도 이 네이티브 경로 하나로 부른다. OpenAI 호환 `/v1`은 `num_ctx`를 받지 않아(보내도 무시한다) 앱이
Ollama 기본 컨텍스트로 돌고, 같은 모델을 측정(16384)과 다른 컨텍스트로 올린다 — 요약처럼 같은 모델을 두 경로가 번갈아
부르면 그때마다 다시 올라간다. 그래서 앱 채팅·요약·도구 채팅도 여기로 보낸다(`app_chat`).

base URL과 에러 처리를 여기 한곳에 모은다. `httpx`는 이 환경에서 `httpx2`(httpx 2.x)라는
배포명으로 설치돼 있다. API는 동일하다.
"""

import json
import os
from collections.abc import Generator
from typing import Any

import httpx2 as httpx

import aux_models
import bench_config as cfg

# `localhost`는 Windows에서 IPv6(::1)를 먼저 시도하고 실패한 뒤 IPv4로 넘어가
# 요청마다 ~2초를 버린다. Ollama는 127.0.0.1에 바인딩하므로 직접 지정한다.
# 이 지연은 TTFT 측정을 통째로 오염시킨다.
_RAW = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
BASE_URL = _RAW.split("/v1")[0].rstrip("/").replace("://localhost", "://127.0.0.1")

# connect는 짧게(붙거나 말거나), read는 넉넉하게(모델 로드가 오래 걸릴 수 있음).
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class OllamaHTTPError(Exception):
    """오류 응답의 **본문까지** 담은 예외. httpx의 `raise_for_status()` 메시지에는
    상태 코드와 URL만 있고 서버가 보낸 본문(`"... does not support tools"` 같은)이
    빠진다 — 그러면 실행 실패의 원인을 나중에 가를 단서가 사라진다.
    본문은 단서일 뿐 판정 근거가 아니다 —
    판정은 `run_errors.classify()`가 정해진 규칙으로 한다."""

    def __init__(self, status_code: int, body: str, url: str):
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status_code} ({url}): {body[:300]}")


def list_models() -> list[dict]:
    """설치된 모델과 태그 메타데이터. `GET /api/tags`."""
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT) as c:
        r = c.get("/api/tags")
    r.raise_for_status()
    return r.json().get("models", [])


def show_model(name: str) -> dict:
    """모델 상세. `POST /api/show`. `license`, `model_info`, `capabilities`, `details` 포함.

    `capabilities`는 `/api/tags`가 주는 것보다 완전하다(예: gemma3의 `vision`,
    gemma-4의 `tools`/`audio`가 tags에서는 누락된다). tool-calling 지원 판별은
    반드시 이쪽 값을 쓴다.
    """
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT) as c:
        r = c.post("/api/show", json={"model": name})
    r.raise_for_status()
    return r.json()


def list_loaded() -> list[dict]:
    """지금 메모리에 올라간 모델들. `GET /api/ps`. `size`/`size_vram`/`context_length` 포함."""
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT) as c:
        r = c.get("/api/ps")
    r.raise_for_status()
    return r.json().get("models", [])


def is_loaded(name: str) -> bool:
    return any(m.get("name") == name for m in list_loaded())


def server_version() -> str | None:
    """Ollama 서버 버전. `GET /api/version`. 같은 모델도 서버 버전에 따라 다르게 돌 수 있어 측정 조건으로 남긴다."""
    with httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT) as c:
        r = c.get("/api/version")
    r.raise_for_status()
    return r.json().get("version")


def model_digest(name: str) -> str | None:
    """설치된 모델의 digest(`/api/tags`). 태그 없이 부르면(`bge-m3`) Ollama가 붙이는 `:latest`로도 찾는다.
    없으면 None — 판정기 버전에 digest를 넣는 쪽(`consistency_embedding`)이 실패로 다룬다."""
    wanted = {name, f"{name}:latest"}
    for m in list_models():
        if wanted & {m.get("name"), m.get("model")}:
            return m.get("digest")
    return None


def embed(model: str, inputs: list[str], timeout: float = 120.0) -> list[list[float]]:
    """임베딩. `POST /api/embed` — 입력 여러 개를 한 번에 보내고 같은 순서로 받는다.
    일관성 판정기 후보 평가용이다. 첫 호출은 모델 로드가 끼어 느리므로 read를 넉넉히 준다.
    측정 항목 안에서 불리면 그 항목에 "이 임베딩 모델을 썼다"가 남는다(`aux_models`)."""
    aux_models.record("embedding", model)
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(timeout, connect=5.0)) as c:
        r = c.post("/api/embed", json={"model": model, "input": inputs})
    if r.status_code >= 400:
        raise OllamaHTTPError(r.status_code, r.text, str(r.url))
    return r.json()["embeddings"]


def unload(name: str, timeout: float = 30.0) -> None:
    """`keep_alive: 0`으로 짧은 요청을 보내 모델을 즉시 내린다(모델 로드 시간 측정 준비).

    호출자가 `/api/ps`로 실제로 내려갔는지 확인해야 한다 — 이 함수는 요청만 보낸다.
    """
    with httpx.Client(base_url=BASE_URL, timeout=timeout) as c:
        r = c.post(
            "/api/chat",
            json={
                "model": name,
                "messages": [{"role": "user", "content": "."}],
                "stream": False,
                "keep_alive": 0,
                "options": {"num_predict": 1},
            },
        )
    r.raise_for_status()


def stream_chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    num_ctx: int,
    num_predict: int,
    sampling: dict[str, float | int],
    think: bool | None = False,
    timeout: float,
    tools: list[dict[str, Any]] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """네이티브 `POST /api/chat` 스트리밍. 각 청크(dict)를 그대로 yield한다.

    마지막 청크(`done: true`)에 `done_reason`(`"stop"`|`"length"`|그 외),
    `prompt_eval_count`·`prompt_eval_duration`(나노초)·`eval_count`·`eval_duration`
    (나노초)이 실려 온다 — 성능 테스트의 TTFT·tok/s·prefill이 전부 여기서 나온다.

    `tools`를 넘기면 실측 결과 `message.tool_calls[].function.arguments`가 OpenAI 호환 `/v1`처럼 JSON
    문자열이 아니라 **이미 파싱된 dict**로 온다(`id`/`index`도 함께). 청크 하나에 전부 담겨 오고 조각나지 않는다.

    `think=None`이면 `think`를 보내지 않는다 — 모델 기본값으로 돈다(앱 채팅).
    """
    options = {"num_ctx": num_ctx, "num_predict": num_predict, **sampling}
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
    }
    if think is not None:
        payload["think"] = think
    if tools:
        payload["tools"] = tools
    with httpx.Client(base_url=BASE_URL, timeout=timeout) as c:
        with c.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code >= 400:
                # 스트림 응답은 본문을 읽기 전에 raise하면 본문이 영영 사라진다
                r.read()
                raise OllamaHTTPError(r.status_code, r.text, str(r.url))
            for line in r.iter_lines():
                if not line:
                    continue
                yield json.loads(line)


# 앱 채팅·요약·도구 채팅이 보내는 값. OpenAI 호환 `/v1`에서 옮겨 오며 **그 경로가 실제로 쓰던 값을 그대로 적는다** — `/v1`은
# 보내지 않은 `top_p`를 1.0으로 채웠고(모델 파일의 값을 덮어쓴다) 출력 상한을 두지 않았고 `think`를 보내지 않았다. 바꾸는 것은
# 컨텍스트 하나다: 측정과 같은 `NUM_CTX`로 올린다(재는 조건이 쓸 조건이어야 한다). 샘플링의 나머지(`top_k`·`repeat_penalty`)는
# 모델 파일 값으로 두고, 측정의 고정 샘플링과 맞추지 않는다 — 그쪽은 모델끼리 같은 조건으로 비교하려고 고정한 값이다.
APP_NUM_PREDICT = -1  # 상한 없음
APP_TOP_P = 1.0
APP_TIMEOUT_SEC = 600.0  # openai SDK 기본값 — 옮기기 전 앱이 기다리던 시간


def app_chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    tools: list[dict[str, Any]] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """앱 경로의 채팅 스트리밍 — 청크를 그대로 yield한다(`stream_chat`과 같은 모양)."""
    return stream_chat(
        model,
        messages,
        num_ctx=cfg.NUM_CTX,
        num_predict=APP_NUM_PREDICT,
        sampling={"temperature": temperature, "top_p": APP_TOP_P},
        think=None,
        timeout=APP_TIMEOUT_SEC,
        tools=tools,
    )
