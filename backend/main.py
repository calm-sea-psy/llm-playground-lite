"""LLM Playground - 얇은 FastAPI 백엔드.

React 프론트엔드와 Ollama 사이의 프록시.
채팅·요약·도구 채팅은 측정과 같은 네이티브 `/api/chat`(`ollama_client.app_chat`).
대화 이력은 백엔드 파일이 주인이다(`conversations`) — `/api/chat`은 `conversation_id`를
받아 이력을 불러오고, 필요하면 압축(`summarizer`)하고, 도구 호출(`tools`)을 거쳐
최종 응답을 스트리밍한 뒤 파일에 반영한다.
나중에 RAG / 클라우드 모델을 붙일 자리도 여기.
"""

import importlib.util
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import agent_tools
import baseline
import compare_notes
import consistency_judgments
import conversations
import ollama_client
import quality_testsets
import report
import rescore_runs
import response_health
import summarizer
import system_prompts
import test_runner
import tools

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

# 공개본에 없는 기능(프롬프트 실험·과제용 실행·지표 다시 재기 등)은 `dev_routes.py`에 있다 — 파일이 없으면 그 라우트가
# 안 달리고, 화면은 `/api/features`를 보고 그 기능을 숨긴다. 유지보수 CLI(`maintenance.py`)도 같은 방식으로 가린다.
DEV_ROUTES = importlib.util.find_spec("dev_routes") is not None
MAINTENANCE_CLI = importlib.util.find_spec("maintenance") is not None

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 지문 규칙 버전이 올라갔으면 저장된 결과 전체를 **지금** 승급한다 — 승급 조건
    # ("현재 파일을 옛 규칙으로 해시한 값 = 저장값")은 세트가 옛 실행 때와 같을
    # 동안만 성립하는 한시적 창이라, 늦출수록 영구히 `비교 불가`로 굳는 실행이
    # 생긴다. 결과 파일이 git 밖이라 커밋의 일부로는 못 한다.
    baseline.upgrade_all()
    yield


app = FastAPI(title="LLM Playground", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    # 프론트가 스트리밍 응답의 커스텀 헤더(대화 id 등)를 읽으려면 명시적으로 열어줘야 한다.
    expose_headers=["X-Conversation-Id", "X-Compress-Applied", "X-Assistant-Message-Id"],
)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class Message(BaseModel):
    id: str | None = None  # 별점 기능 이전에 저장된 옛 대화는 id가 없을 수 있다
    role: str
    content: str | None = None  # 도구 호출 턴은 content가 None이다
    rating: int | None = None
    rating_note: str | None = None
    rated_at: str | None = None
    tool_calls: list[ToolCall] | None = None  # role="assistant"이고 도구를 부른 턴만
    confirmation: str | None = None  # None(위험 도구 아님) / pending / approved / declined
    tool_call_id: str | None = None  # role="tool"인 결과 메시지만


class ModelDetail(BaseModel):
    """프론트가 한 번 받아 localStorage에 캐시하는 모델 상세."""

    id: str
    family: str | None = None
    parameter_size: str | None = None
    parameter_count: int | None = None
    quantization: str | None = None
    context_length: int | None = None
    capabilities: list[str] = []
    license: str | None = None  # 전문이 아니라 식별 가능한 첫 줄
    size: int | None = None  # 디스크 바이트
    modified_at: str | None = None


class ChatTurnRequest(BaseModel):
    """`/api/chat`의 새 계약. 프론트는 전체 이력이 아니라 새 메시지 하나만 보낸다."""

    model: str
    message: str
    conversation_id: str | None = None
    system: str | None = None
    # 저장된 프롬프트를 골랐으면 그 이름 — 본문이 파일과 같을 때만 대화에 이름·해시를 남긴다
    system_prompt_name: str | None = None
    temperature: float = 0.7
    compress: bool = True  # False면 요약 없이 원본 전체 전송 (품질 지표 대조군 경로)
    tools_enabled: bool = False  # 도구 호출 토글. 모델마다 편차가 커 기본 꺼짐


class ToolConfirmRequest(BaseModel):
    """위험 도구(현재는 `delete_model`) 확인 응답."""

    conversation_id: str
    approved: bool


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: str


class ConversationOut(BaseModel):
    id: str
    title: str | None = None
    model: str
    system: str | None = None
    system_prompt: dict | None = None  # {name, title, sha256} — 저장된 프롬프트를 고른 대화만
    messages: list[Message]
    summary: str | None = None
    summarized_upto: int = 0
    created_at: str
    updated_at: str


class RatingRequest(BaseModel):
    """1~5, 또는 `None`으로 별점 취소. 벤치마크 지표가 아니라
    일반 대화 모드에서의 실사용 평가라 메시지 하나하나에 붙는다."""

    rating: int | None = None
    note: str | None = None


class SystemPromptOut(BaseModel):
    name: str
    title: str
    content: str
    sha256: str


class SaveSystemPromptRequest(BaseModel):
    title: str
    content: str


class SuiteOut(BaseModel):
    id: str
    label: str


class RunItemOut(BaseModel):
    id: str
    label: str
    status: str
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    # 지표 저장 상태 — measured | incapable | failed | confirmed_failure
    outcome: str | None = None
    failure: dict | None = None  # {cause, message, response_body, traceback, memory}
    aux_models: list[dict] | None = None  # 이 항목이 실제로 부른 보조 모델 [{role, model, digest}]


class RunOut(BaseModel):
    id: str
    model: str
    system_prompt: str | None = None
    status: str
    total: int
    completed: int
    current_item: str | None = None
    items: list[RunItemOut]
    started_at: str
    finished_at: str | None = None
    config: dict = {}
    metrics: dict = {}
    scope: str = "full"  # "full" | "baseline"
    provider_name: str = "ollama"
    fingerprints: dict = {}  # 규칙 버전별 항목 지문 맵(baseline.py)
    scorer_versions: dict = {}  # 지표별 판정기 버전
    # 재채점 — 언제 다시 채점했고 그때 판정기 버전이 무엇에서 무엇으로 바뀌었나(rescoring.py). 여기 적지 않으면 응답에서
    # 조용히 빠진다. `scorer_version_changes`는 **없음(None)과 빈 기록({})을 가른다** — 없으면 기록을 남기기 전 파일이다
    rescored_at: str | None = None
    scorer_version_changes: dict | None = None
    kind: str = "run"  # "run" | "metric_rerun"
    parent_run_id: str | None = None
    # 지표 재실행이 붙은 실행을 합친 뷰에서만 채워진다(test_runner.merge_with_reruns)
    provenance: dict = {}
    mixed: bool = False
    rerun_ids: list[str] = []
    # 실행 종류 — 선정용 실행은 시스템 프롬프트가 없다
    run_type: str = "selection"
    system_prompt_meta: dict | None = None
    system_prompt_application: dict | None = None
    precheck: dict | None = None  # 기준선 사전 점검
    # 파생 값(response_health.py) — 읽을 때 계산된다
    response_health: dict = {}
    korean_purity: dict | None = None
    call_meta_recorded: bool | None = None
    # 2회차(같은 호출을 한 번 더 돈 기록)와 두 바퀴의 재현 요약 — 여기 적지 않으면 응답에서 조용히 빠진다
    second_round: dict | None = None
    reproduction: dict | None = None


class RunSummary(BaseModel):
    id: str
    model: str
    status: str
    started_at: str
    finished_at: str | None = None
    system_prompt: str | None = None
    run_type: str = "selection"
    system_prompt_meta: dict | None = None
    config: dict = {}
    fingerprints: dict = {}
    rerun_ids: list[str] = []
    mixed: bool = False


class EstimateOut(BaseModel):
    """실행 전 확인 패널의 예상 소요 시간 — 과거 실측 평균 기반이라
    데이터가 없는 항목이 하나라도 있으면 `total_sec`은 `None`(예상 불가)."""

    by_item: dict[str, float | None]
    total_sec: float | None
    sample_count: int


class StartRunRequest(BaseModel):
    model: str
    run_type: str = "selection"  # "selection" | "prompt_experiment" | "assignment"(과제용 고정 10문항)
    system_prompt_name: str | None = None  # 실험일 때만 — 본문은 백엔드가 파일에서 읽는다
    # 옛 계약(본문 직접 전송)은 받지 않는다 — 조용히 무시하면 조건이 사라진 채 실행된다
    system_prompt: str | None = None


class BaselineRunOut(RunOut):
    """`GET /api/tests/baseline`이 돌려주는 베이스라인 실행 — RunOut에 베이스라인
    전용 필드(지문·측정 조건·측정 시각)를 얹는다."""

    is_baseline: bool = True
    conditions: dict = {}
    measured_at: str | None = None


class BaselineStalenessOut(BaseModel):
    exists: bool
    stale: bool
    model: str | None = None
    measured_at: str | None = None
    # 범위별 판정 — `일치`/`불일치`/`기록 없음`/`비교 불가(규칙 다름)`. `stale`은
    # 하나라도 `불일치`일 때만 True다(나머지는 경고가 아니라 "확인 불가").
    ranges: dict[str, str] = {}
    # 무엇이 달라졌는지 — 키 종류별 뜻 + 조치(지표 추가, 스펙 문구 변경 등)
    reasons: list[str] = []


class BaselineOut(BaseModel):
    """베이스라인 실행 결과 + 낡음 판정을 한 번에 — 화면이 참고선을 그릴지,
    흐리게 표시할지를 이 한 응답으로 정한다."""

    run: BaselineRunOut | None
    staleness: BaselineStalenessOut
    # 기준선을 고른 조건과, 맞는 기준선이 없을 때 그 까닭(없는 것을 `기준선 없음`과 뭉뚱그리지 않는다)
    document_length: str | None = None
    unmatched: str | None = None


class ToolDefOut(BaseModel):
    """`GET /api/tools` 응답 하나. 모델에게 실제로 주는
    정의(`name`/`description`/`parameters`)에 화면·정책용 필드를 얹은 것으로,
    화면용 목록을 따로 하드코딩하지 않는다(같은 정보를 두 벌 두지 않는다)."""

    name: str
    description: str
    parameters: dict
    scope: str
    destructive: bool
    requires_key: str | None
    status: str
    daily_limit: int | None
    returns: str


@app.get("/api/health")
def health():
    return {"status": "ok", "ollama": OLLAMA_BASE_URL}


def _license_label(text: str | None) -> str | None:
    """긴 라이선스 전문에서 식별 가능한 첫 줄만. 전문은 localStorage에 넣기엔 크다."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:80]
    return None


def _detail_from(name: str, tag: dict) -> ModelDetail:
    """`/api/tags` 항목 + `/api/show`를 합쳐 하나의 ModelDetail로."""
    td = tag.get("details", {})
    detail = ModelDetail(
        id=name,
        family=td.get("family"),
        parameter_size=td.get("parameter_size"),
        quantization=td.get("quantization_level"),
        context_length=td.get("context_length"),
        capabilities=tag.get("capabilities") or [],
        size=tag.get("size"),
        modified_at=tag.get("modified_at"),
    )
    try:
        info = ollama_client.show_model(name)
    except Exception:  # noqa: BLE001 - show 실패해도 tags 기반 정보는 준다
        return detail

    sd = info.get("details", {})
    mi = info.get("model_info", {})
    detail.family = sd.get("family") or detail.family
    detail.parameter_size = sd.get("parameter_size") or detail.parameter_size
    detail.quantization = sd.get("quantization_level") or detail.quantization
    detail.license = _license_label(info.get("license"))
    detail.capabilities = info.get("capabilities") or detail.capabilities
    detail.parameter_count = mi.get("general.parameter_count")
    # `model_info`의 `<arch>.context_length`가 정확한 값 (details 쪽은 반올림될 수 있음)
    for key, value in mi.items():
        if key.endswith(".context_length"):
            detail.context_length = value
            break
    return detail


@app.get("/api/models/details")
def model_details() -> list[ModelDetail]:
    """설치된 모든 모델의 상세 정보. `/api/tags` 한 번 + 모델당 `/api/show` 한 번."""
    try:
        tags = ollama_client.list_models()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama 연결 실패: {exc}") from exc
    return [_detail_from(t.get("name", ""), t) for t in tags if t.get("name")]


# ---------------------------------------------------------------------------
# 대화 저장/이어하기
# ---------------------------------------------------------------------------


@app.get("/api/conversations")
def get_conversations(model: str) -> list[ConversationSummary]:
    """해당 모델의 저장된 대화 목록(제목/수정시각 요약)."""
    return conversations.list_summaries(model)


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> ConversationOut:
    conv = conversations.load(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    return conv


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    if not conversations.delete(conversation_id):
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    return {"status": "ok"}


@app.put("/api/conversations/{conversation_id}/messages/{message_id}/rating")
def put_message_rating(conversation_id: str, message_id: str, req: RatingRequest) -> ConversationOut:
    """메시지 하나에 별점을 매기거나(`rating` 1~5) `rating: null`로 지운다."""
    if req.rating is not None and not (1 <= req.rating <= 5):
        raise HTTPException(status_code=422, detail="rating은 1~5 사이여야 합니다")
    conv = conversations.set_rating(conversation_id, message_id, req.rating, req.note)
    if conv is None:
        raise HTTPException(status_code=404, detail="대화 또는 메시지를 찾을 수 없습니다")
    return conv


# ---------------------------------------------------------------------------
# 채팅
# ---------------------------------------------------------------------------


def _plain_chat_events(model: str, messages: list[dict], temperature: float):
    """도구 없이 그냥 대화할 때도 `tools.py`와 같은 이벤트 모양으로 맞춘다 —
    `_run_turn`이 두 경로를 똑같이 처리할 수 있게."""
    for chunk in ollama_client.app_chat(model, messages, temperature=temperature):
        if content := (chunk.get("message") or {}).get("content"):
            yield {"type": "text", "text": content}


def _run_turn(conv: dict, event_gen):
    """이벤트 제너레이터 하나를 스트리밍 응답으로 바꾸고, 끝나면 `conv`에 반영해
    저장한다. `/api/chat`(새 턴)과 `/api/chat/confirm`(재개) 둘 다 이걸 쓴다 —
    도구 호출·결과 메시지 저장, 확인 대기 상태 갱신 로직이 완전히 같기 때문이다.
    """
    assistant_message_id = conversations.new_message_id()

    def generate():
        assistant_parts: list[str] = []
        extra_messages: list[dict] = []
        pending_id: str | None = None
        try:
            for event in event_gen:
                if event["type"] == "text":
                    assistant_parts.append(event["text"])
                    yield event["text"]
                elif event["type"] == "tool_messages":
                    extra_messages.extend(event["messages"])
                elif event["type"] == "confirm_required":
                    extra_messages.append(event["message"])
                    pending_id = event["message"]["id"]
        except Exception as exc:  # noqa: BLE001
            error_text = f"\n\n[에러] {exc}"
            assistant_parts.append(error_text)
            yield error_text
        finally:
            conv["messages"].extend(extra_messages)
            if pending_id is None:
                # 확인 대기로 끝난 턴은 아직 자연어 답이 없다 — 승인/거부 후에
                # 이어지는 턴에서 이 자리를 채운다.
                conv["messages"].append(
                    conversations.new_message(
                        "assistant", "".join(assistant_parts), message_id=assistant_message_id
                    )
                )
            conv["pending_tool_call_message_id"] = pending_id
            conversations.save(conv)

    resp = StreamingResponse(generate(), media_type="text/plain; charset=utf-8")
    resp.headers["X-Conversation-Id"] = conv["id"]
    resp.headers["X-Assistant-Message-Id"] = assistant_message_id
    return resp


@app.post("/api/chat")
def chat(req: ChatTurnRequest):
    """스트리밍 채팅. `conversation_id`가 있으면 이어서, 없으면 새로 시작한다.

    흐름: 이력 로드 → 사용자 메시지 추가 → 압축 판단(build_context) →
    Ollama 호출(도구 루프 켜졌으면 그쪽 경유) → 스트리밍 → 완료 시 파일 저장.
    """
    conv = conversations.load(req.conversation_id) if req.conversation_id else None
    if conv is None:
        conv = conversations.new_conversation(req.model, req.system)

    if conv.get("pending_tool_call_message_id"):
        raise HTTPException(
            status_code=409, detail="확인 대기 중인 도구 호출이 있습니다 — 먼저 승인/거부해주세요"
        )

    conv["messages"].append(conversations.new_message("user", req.message))
    if req.system is not None:
        conv["system"] = req.system or None
        conv["system_prompt"] = _prompt_identity(req.system, req.system_prompt_name)
    if not conv.get("title"):
        conv["title"] = conversations.derive_title(req.message)

    send_messages, compressed = summarizer.build_context(conv, conv.get("system"), req.compress)

    if req.tools_enabled:
        event_gen = tools.stream_chat_with_tools(req.model, send_messages, req.temperature, scope="chat")
    else:
        event_gen = _plain_chat_events(req.model, send_messages, req.temperature)

    resp = _run_turn(conv, event_gen)
    resp.headers["X-Compress-Applied"] = "1" if compressed else "0"
    return resp


@app.post("/api/chat/confirm")
def confirm_tool_call(req: ToolConfirmRequest):
    """위험 도구 호출을 승인/거부한다.

    확인 대기 중인 메시지가 없으면 409 — 프론트가 잘못된 타이밍에 불렀다는 뜻이다.
    """
    conv = conversations.load(req.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")

    pending_id = conv.get("pending_tool_call_message_id")
    if not pending_id:
        raise HTTPException(status_code=409, detail="확인 대기 중인 도구 호출이 없습니다")

    pending_msg = next((m for m in conv["messages"] if m.get("id") == pending_id), None)
    if pending_msg is None:
        raise HTTPException(status_code=404, detail="대기 중인 메시지를 찾을 수 없습니다")

    pending_msg["confirmation"] = "approved" if req.approved else "declined"
    send_messages, _compressed = summarizer.build_context(conv, conv.get("system"), True)

    event_gen = tools.resume_after_confirmation(
        conv["model"],
        send_messages,
        0.7,
        pending_calls=pending_msg["tool_calls"],
        approved=req.approved,
        scope="chat",
    )
    return _run_turn(conv, event_gen)


@app.get("/api/tools")
def get_tools() -> list[ToolDefOut]:
    """등록된 도구 정의 + 상태. 화면(`/tools`)과 tool-calling
    루프가 같은 데이터를 본다 — `agent_tools.py`가 유일한 출처다."""
    return agent_tools.api_tools_response()


@app.get("/api/tools/injection-probe")
def get_injection_probe_tool() -> ToolDefOut:
    """도구 결과 프롬프트 인젝션 테스트 전용 도구 —
    정확도 테스트용 일곱 개와 같이 두지 않고 화면에서 따로 그린다."""
    return agent_tools.injection_probe_response()


class ToolInvokeRequest(BaseModel):
    args: dict = {}


@app.post("/api/tools/{name}/invoke")
def invoke_tool(name: str, req: ToolInvokeRequest) -> Any:
    """도구 하나를 모델 없이 직접 실행한다 —
    도구 자체가 동작하는지와 모델이 잘 부르는지를 분리해서 본다. 반환값 모양이
    도구마다 다르므로(리스트/딕셔너리) 응답 타입을 고정하지 않는다.

    `destructive`(현재 `delete_model`)는 여기서 막는다 — 화면에서 실수로
    모델을 지우는 경로를 만들지 않기 위해서다(화면에 버튼 자체를 안 두는 것과
    같은 이유를 서버에서도 강제). `get_any_tool`을 써서 인젝션 프로브 도구도
    직접 호출해볼 수 있게 한다 — 정확도 테스트 목록(7개)에는 없지만 화면에는
    나오는 도구이기 때문이다.
    """
    tool = agent_tools.get_any_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="알 수 없는 도구입니다")
    if tool.destructive:
        raise HTTPException(status_code=403, detail="파괴적인 도구는 직접 호출할 수 없습니다")
    status = tool.status()
    if status != "ready":
        raise HTTPException(status_code=409, detail=f"도구를 쓸 수 없는 상태입니다: {status}")
    return json.loads(agent_tools.run_tool_def(tool, req.args))


# ---------------------------------------------------------------------------
# 성능 테스트 — 시스템 프롬프트, 실행
# ---------------------------------------------------------------------------


def _prompt_identity(system: str | None, name: str | None) -> dict | None:
    """대화에 남길 프롬프트 식별 정보. **본문이 그 이름의 파일과 같을 때만** 이름을 남긴다 —
    저장된 것을 고른 뒤 고쳐 쓰고 저장하지 않았다면 그건 직접 입력이라 이름을 붙이면 거짓이 된다.
    직접 입력은 본문만 남는다(`conv.system`)."""
    if not system or not name:
        return None
    try:
        prompt = system_prompts.get_prompt(name)
    except (FileNotFoundError, system_prompts.InvalidPromptName):
        return None
    if prompt["sha256"] != system_prompts.content_sha256(system.strip()):
        return None
    return {"name": prompt["name"], "title": prompt["title"], "sha256": prompt["sha256"]}


@app.get("/api/system-prompts")
def get_system_prompts() -> list[SystemPromptOut]:
    """`backend/prompts/`를 스캔한 목록. 캐싱 없음 — 매번 새로 읽는다."""
    return system_prompts.list_prompts()


@app.put("/api/system-prompts/{name}")
def put_system_prompt(name: str, req: SaveSystemPromptRequest) -> SystemPromptOut:
    """만들거나 고친다. 결과 파일은 본문을 스냅숏하므로 고쳐도 과거 결과의 조건 기록은 그대로다."""
    try:
        return system_prompts.save(name, req.title, req.content)
    except ValueError as exc:  # InvalidPromptName 포함
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/system-prompts/{name}")
def delete_system_prompt(name: str):
    try:
        system_prompts.delete(name)
    except system_prompts.InvalidPromptName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="시스템 프롬프트를 찾을 수 없습니다") from exc
    return {"status": "ok"}


@app.get("/api/tests/suites")
def get_test_suites() -> list[SuiteOut]:
    """실행 가능한 테스트 항목 목록 (모델 로드·워밍업·메모리·짧은 탐침·컨텍스트 부하 3단계)."""
    return test_runner.list_suites()


@app.get("/api/tests/config")
def get_test_config() -> dict:
    """이번 실행에 적용될 측정 조건 스냅샷(num_ctx·샘플링·요약 모델 등) — 실행 전
    확인 패널이 "이번 실행" 요약에 그대로 나열한다. 기계 사양·Ollama 버전도 싣는다 — 직전 실행과의 차이에
    서버 버전이 빠지면 업데이트 뒤 첫 실행이 `조건이 같다`로 보인다."""
    return {**test_runner.config_snapshot(), **test_runner.measurement_environment("ollama")}


@app.get("/api/tests/estimate")
def get_test_estimate(model: str, scope: str = "full") -> EstimateOut:
    """과거 실행들의 항목별 실측 소요시간으로 이번 실행의 예상 총 소요시간을
    낸다. 데이터가 없으면 `total_sec: null` — 화면은 "예상 불가"로 표시."""
    return test_runner.estimate_duration(model, scope)


@app.get("/api/tests/active")
def get_active_test() -> dict:
    """일반 대화 모드가 "테스트 진행 중" 경고를 띄우기 위한 신호."""
    return {"run_id": test_runner.get_active_run_id()}


@app.post("/api/tests/runs")
def start_test_run(req: StartRunRequest) -> RunOut:
    """테스트 실행 시작. 즉시 실행 id를 반환하고 실제 작업은 백그라운드에서 돈다.
    선정용 실행에 프롬프트가 오거나(422) 실험에 이름이 없으면(422) 시작하지 않는다."""
    if req.system_prompt:
        raise HTTPException(
            status_code=422,
            detail="시스템 프롬프트 본문은 받지 않습니다 — run_type='prompt_experiment'와 system_prompt_name을 보내세요",
        )
    if req.run_type not in run_types():
        raise HTTPException(status_code=422, detail=f"이 실행 종류는 지원하지 않습니다: {req.run_type}")
    try:
        return test_runner.start_run(req.model, req.run_type, req.system_prompt_name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run_types() -> tuple[str, ...]:
    """`POST /api/tests/runs`가 받는 실행 종류 — 선정은 언제나, 나머지는 개발용 라우트가 있을 때만."""
    if not DEV_ROUTES:
        return (test_runner.SELECTION,)
    import dev_routes

    return (test_runner.SELECTION, *dev_routes.RUN_TYPES)


@app.get("/api/features")
def get_features() -> dict:
    """공개본에 없을 수 있는 기능이 켜져 있는지 — 화면이 없는 기능의 버튼·안내를 숨기는 데 쓴다."""
    return {"prompt_experiment": test_runner.PROMPT_EXPERIMENT in run_types(), "rerun": DEV_ROUTES,
            "maintenance_cli": MAINTENANCE_CLI}


@app.get("/api/tests/runs/{run_id}")
def get_test_run(run_id: str) -> RunOut:
    """진행 상황 폴링. 2초 간격 권장."""
    run = test_runner.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="실행 정보를 찾을 수 없습니다")
    return run


@app.delete("/api/tests/runs/{run_id}")
def cancel_test_run(run_id: str):
    """진행 중인 실행 중단. 이미 완료된 항목까지는 '부분 완료'로 저장된다."""
    if not test_runner.cancel_run(run_id):
        raise HTTPException(status_code=404, detail="진행 중인 실행을 찾을 수 없습니다")
    return {"status": "ok"}


@app.get("/api/tests/results")
def get_test_results() -> list[RunSummary]:
    return test_runner.list_results()


@app.get("/api/tests/results/{run_id}")
def get_test_result(run_id: str) -> RunOut:
    result = test_runner.load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다")
    return result


class RescoreRequest(BaseModel):
    run_ids: list[str]


@app.post("/api/tests/rescore/plan")
def rescore_plan(req: RescoreRequest) -> dict:
    """고른 실행의 재채점 미리보기 — 출처 파일 집합과 파일마다 바뀌는 채점기 버전. 파일을 고치지 않는다."""
    return rescore_runs.plan(req.run_ids)


@app.post("/api/tests/rescore")
def rescore_selected_runs(req: RescoreRequest) -> dict:
    """고른 실행의 출처 파일을 지금 판정기로 다시 채점한다(응답 원문은 그대로). 고르지 않은 결과와 기준선은 건드리지 않는다."""
    if not req.run_ids:
        raise HTTPException(status_code=422, detail="재채점할 실행을 고르세요")
    return rescore_runs.apply(req.run_ids)


# ---------------------------------------------------------------------------
# 클라우드 베이스라인 (절대 기준점)
# ---------------------------------------------------------------------------


@app.post("/api/tests/baseline")
def start_baseline_run() -> RunOut:
    """베이스라인 실행 시작. 모델은 고정(`gpt-5.6-luna`)이라 요청 본문이 없다.
    일반 실행과 같은 동시 실행 잠금을 공유한다 — 둘 다 로컬 리소스든 API
    비용이든 동시에 두 개를 도는 게 맞지 않는다."""
    try:
        return test_runner.start_baseline_run()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/tests/baseline")
def get_baseline(document_length: str | None = None) -> BaselineOut:
    """**후보와 같은 문서 길이**로 잰 가장 최근 베이스라인 + 지금 세트 지문과 대조한 낡음 여부.
    `document_length`는 비교에 든 후보가 읽은 판이다 — 주지 않으면 다음 후보가 읽을 판(대표 판)이다.
    `run`이 `null`이면 그 조건으로 잰 기준선이 없는 것이고, 한 번도 안 돈 것과 구분하도록 `unmatched`에 까닭을 싣는다."""
    try:
        length = quality_testsets.check_document_length(document_length or quality_testsets.REPRESENTATIVE_DOCUMENT_LENGTH)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    entry = baseline.latest(length)
    staleness = baseline.staleness(entry)  # 파생 값을 붙이기 전의 원본으로 판정한다
    if entry is not None:
        response_health.attach_derived(entry)
    unmatched = baseline.unmatched_reason(length) if entry is None and baseline.latest() is not None else None
    return {"run": entry, "staleness": staleness, "document_length": length, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# 비교 노트
# ---------------------------------------------------------------------------


class GenerateNotesRequest(BaseModel):
    """`table_text`는 프론트가 이미 렌더링한 비교 표를 그대로 직렬화한
    것이다 — 25개 지표의 라벨·단위 정의를 백엔드에 또 두지 않기 위해서다."""

    run_ids: list[str]
    table_text: str


class NoteRatingRequest(BaseModel):
    rating: int | None = None
    note: str | None = None


@app.post("/api/compare/notes")
def generate_compare_notes(req: GenerateNotesRequest) -> dict:
    """선택된 실행마다 그 모델에게 비교 표 전체를 주고 노트를 쓰게 한다.
    같은 조합(+표 내용)으로 이미 생성한 적 있으면 캐시를 그대로 돌려준다 —
    재현성 원칙에 따라 다시 부르지 않는다. 성능 테스트 실행 중에는
    막는다 — 측정 오염 방지와 같은 이유(동시에 여러 로컬 모델이 상주하면 안 됨)."""
    if test_runner.get_active_run_id() is not None:
        raise HTTPException(status_code=409, detail="성능 테스트 실행 중에는 노트를 생성할 수 없습니다")
    if not req.run_ids:
        raise HTTPException(status_code=422, detail="선택된 실행이 없습니다")
    try:
        return compare_notes.generate(req.run_ids, req.table_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class NoteStatusRequest(BaseModel):
    run_ids: list[str]
    table_text: str


class GenerateOneNoteRequest(BaseModel):
    run_ids: list[str]
    table_text: str
    run_id: str


class AliasesRequest(BaseModel):
    run_ids: list[str]


@app.post("/api/compare/aliases")
def compare_aliases(req: AliasesRequest) -> dict[str, str]:
    """run_id → 별칭. 화면이 노트 입력 표 머리글을 만들 때 쓴다 — 별칭 규칙은 백엔드
    `model_aliases.py` 하나에만 있고, 리포트도 같은 함수로 이름을 찍는다."""
    return compare_notes.aliases_for(req.run_ids)


@app.post("/api/compare/notes/status")
def compare_notes_status(req: NoteStatusRequest) -> dict:
    """리포트 내보내기 확인 패널 — 이 조합에서 이미 있는 노트, 빠진 모델, 실측 평균 소요.
    모델을 부르지 않는다."""
    return compare_notes.status(req.run_ids, req.table_text)


@app.post("/api/compare/notes/one")
def generate_one_compare_note(req: GenerateOneNoteRequest) -> dict:
    """모델 하나의 노트만 생성한다 — 내보내기 흐름이 모델별로 불러 진행 단계(`2/4`)를 보여주고,
    한 모델이 실패해도 나머지로 넘어갈 수 있게 한다. 측정 중에는 409 — 프론트는 이것을 오류가
    아니라 "노트 없이 내보내기" 경로로 받는다."""
    if test_runner.get_active_run_id() is not None:
        raise HTTPException(status_code=409, detail="성능 테스트 실행 중에는 노트를 생성할 수 없습니다")
    if req.run_id not in req.run_ids:
        raise HTTPException(status_code=422, detail="run_id가 선택된 실행에 없습니다")
    try:
        return compare_notes.generate_one(req.run_ids, req.table_text, req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 모델 호출 실패. 호출자가 그 노트만 건너뛴다
        raise HTTPException(status_code=502, detail=f"노트 생성 실패: {exc}") from exc


@app.put("/api/compare/notes/{key}/{run_id}/rating")
def rate_compare_note(key: str, run_id: str, req: NoteRatingRequest) -> dict:
    """노트 하나에 별점을 매기거나(`rating` 1~5) `rating: null`로 지운다 —
    11번과 같은 메커니즘이지만 대화 메시지가 아니라 비교 노트 캐시에 붙는다."""
    if req.rating is not None and not (1 <= req.rating <= 5):
        raise HTTPException(status_code=422, detail="rating은 1~5 사이여야 합니다")
    data = compare_notes.set_rating(key, run_id, req.rating, req.note)
    if data is None:
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다")
    return data


# ---------------------------------------------------------------------------
# 일관성 대표 쌍 판정
# ---------------------------------------------------------------------------


class VerdictRequest(BaseModel):
    verdict: str
    reason: str | None = None  # 가림이 풀린 뒤 판정을 바꿀 때 필수


@app.get("/api/consistency-judgments")
def get_consistency_judgments() -> dict:
    """판정 패널 상태 — 칸은 난수 토큰으로만 가리키고, 판정 대상 칸이 모두 끝나기 전에는 모델·실행 id·점수·
    오염 표시를 싣지 않는다(가림은 화면이 아니라 여기서 막는다)."""
    return consistency_judgments.state()


class DemotionRequest(BaseModel):
    run_ids: list[str]


@app.post("/api/consistency-judgments/demotion")
def consistency_demotion(req: DemotionRequest) -> dict:
    """비교에 든 실행들의 사람 판정으로 일관성 지표를 종합 순위에서 뺄지(세트 단위). 가중치는 화면·리포트가 적용한다."""
    return consistency_judgments.demotion(req.run_ids, test_runner.load_result)


@app.post("/api/consistency-judgments/coverage")
def consistency_coverage(req: DemotionRequest) -> dict:
    """비교에 든 실행의 판정 칸이 판정 파일에 다 있나 — 없으면 리포트 판정칸이 비어 나오므로 화면이 다시 만들기를 권한다."""
    return consistency_judgments.coverage(req.run_ids, test_runner.load_result)


class RebuildRequest(BaseModel):
    run_ids: list[str]
    dry_run: bool = False  # 쓰지 않고 이어 받을 판정·무효로 옮길 판정·새로 판정할 칸만 센다


@app.post("/api/consistency-judgments/rebuild")
def rebuild_consistency_judgments(req: RebuildRequest) -> dict:
    """비교에 든 실행 기준으로 판정 칸을 다시 만든다. 같은 답의 판정은 이어 받고 나머지는 무효 기록으로 옮긴다(지우지 않는다).
    고른 실행의 칸이 이미 다 있으면 409 — 다시 만들면 파일에만 있는 실행의 판정이 무효로 옮겨진다."""
    try:
        return consistency_judgments.rebuild(req.run_ids, test_runner.load_result, dry_run=req.dry_run)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/consistency-judgments/{token}")
def put_consistency_verdict(token: str, req: VerdictRequest) -> dict:
    """판정 하나를 저장한다. 토큰을 모르면 409 — 서버가 다시 떠 토큰이 새로 발급된 것이라, 화면은 목록을 다시 받아
    이어간다(저장된 판정은 파일에 있다)."""
    try:
        return consistency_judgments.save_verdict(token, req.verdict, req.reason)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 결과 리포트 내보내기
# ---------------------------------------------------------------------------


# 내보낸 리포트를 브라우저 안에서 여는 주소 — 저장 응답이 이 주소를 돌려주고, 화면은 그대로 쓴다
REPORT_FILES_ROUTE = "/api/compare/report/files/"


@app.post("/api/compare/report")
def generate_compare_report(payload: dict) -> dict:
    """프론트(`frontend/src/report.js`)가 이미 계산해 보낸 수치를 PDF로 그려 저장소의 `report/` 폴더에
    저장하고, 저장한 위치를 돌려준다. 본문을 `dict`로 그대로 받는다 — payload 형태가 안정화되기
    전까지 Pydantic 모델로 못 박지 않는다(실제 소비자는 report.py의
    `_page_*` 함수들이라, 스키마가 바뀌면 거기 하나만 맞추면 된다)."""
    try:
        # 이름을 먼저 잡는다 — 리포트가 응답 전문 파일을 이름으로 가리킨다
        report_path, transcripts_path = report.reserve_paths()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"리포트를 저장하지 못했습니다: {exc}") from exc
    try:
        files = report.generate_report(payload, transcripts_name=transcripts_path.name)
    except RuntimeError as exc:  # 한글 폰트 없음 등 — 깨진 파일 대신 에러
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rel = lambda path: path.relative_to(report.REPORT_DIR.parent).as_posix()
    try:
        report.write_pdf(report_path, files.pdf)
        if files.transcripts:
            report.write_pdf(transcripts_path, files.transcripts)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"리포트를 저장하지 못했습니다: {exc}") from exc
    return {
        "saved_to": rel(report_path),
        "bytes": len(files.pdf),
        # 응답 전문은 판정하는 사람의 작업 자료라 따로 나온다. 없으면 그 까닭을 함께 돌려준다 —
        # 빠뜨린 것과 안 만든 것을 화면이 구분할 수 있어야 한다
        "transcripts_saved_to": rel(transcripts_path) if files.transcripts else None,
        "transcripts_note": files.note,
        # 화면의 `리포트 확인하기`가 여는 주소 — 방금 저장한 파일 그 자체다
        "view_url": REPORT_FILES_ROUTE + report_path.name,
        "transcripts_view_url": REPORT_FILES_ROUTE + transcripts_path.name if files.transcripts else None,
        # 표지 = 결론 면 — 넘쳤으면 화면이 저장 위치 옆에 그렇게 말한다
        "cover_pages": files.cover_pages,
    }


@app.get(REPORT_FILES_ROUTE + "{name}")
def get_saved_report(name: str) -> FileResponse:
    """내보낸 리포트를 브라우저 안에서 연다(`inline`). 화면용으로 따로 그리지 않고 저장한 파일을 그대로 돌려준다.
    저장 규칙의 이름만 받는다 — 폴더 밖이나 폴더 안의 다른 파일은 404."""
    path = report.saved_report(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"저장된 리포트가 없습니다: {name}")
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline")


if DEV_ROUTES:
    import dev_routes

    app.include_router(dev_routes.build_router(run_out=RunOut, conversation_out=ConversationOut,
                                               prompt_identity=_prompt_identity, ollama_base_url=OLLAMA_BASE_URL))
