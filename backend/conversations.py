"""대화 이력 파일 저장소.

`backend/conversations/{model_slug}/{conversation_id}.json` 형태로 모델별 폴더에
저장한다. localStorage가 아니라 백엔드가 주인이라는 점이 핵심 — 요약 압축을
백엔드에서 하려면 백엔드가 전체 이력을 알고 있어야 한다.
"""

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONVERSATIONS_DIR = Path(__file__).parent / "conversations"

# Windows 파일/폴더명에는 못 쓰는 문자들. Ollama 모델명의 `:`가 가장 흔하지만
# 나머지도 이론상 나올 수 있어 전부 치환해둔다.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def slugify(model: str) -> str:
    """모델명을 폴더명으로 쓸 수 있는 슬러그로. `llama3.2:latest` -> `llama3.2_latest`."""
    return _UNSAFE_CHARS.sub("_", model)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(model: str) -> str:
    """`{model_slug}__{난수}` 형태. id만 보고 어느 폴더 파일인지 바로 안다."""
    return f"{slugify(model)}__{secrets.token_hex(4)}"


def _slug_from_id(conversation_id: str) -> str:
    slug, _, _ = conversation_id.partition("__")
    return slug


def _dir_for(model: str) -> Path:
    return CONVERSATIONS_DIR / slugify(model)


def _path_for_id(conversation_id: str) -> Path:
    return CONVERSATIONS_DIR / _slug_from_id(conversation_id) / f"{conversation_id}.json"


def new_message_id() -> str:
    return secrets.token_hex(4)


def new_message(role: str, content: str, *, message_id: str | None = None) -> dict[str, Any]:
    """대화에 들어가는 메시지 하나. `id`가 있어야 나중에 별점(rating)을 그 메시지에
    붙일 수 있다(벤치마크가 아니라 실사용 중 평가라 대화별·메시지별로 남긴다).

    `message_id`를 넘기면 그 id를 그대로 쓴다 — 스트리밍 응답의 id를 완료 전에
    헤더로 먼저 내보내고, 저장할 때 같은 id로 메시지를 만들어야 하기 때문
    (`main.py`의 `/api/chat`)."""
    return {
        "id": message_id or new_message_id(),
        "role": role,
        "content": content,
        "rating": None,  # 1~5, 아직 평가 안 했으면 None
        "rating_note": None,  # 선택적 메모
        "rated_at": None,
    }


def new_tool_call_message(
    tool_calls: list[dict[str, Any]], *, message_id: str | None = None, confirmation: str | None = None
) -> dict[str, Any]:
    """도구를 부른 assistant 턴(기존 대화 구조에 그대로 넣는다).
    `tool_calls`는 `[{"id", "name", "arguments"(dict)}]` — `arguments`를 문자열이
    아니라 dict로 저장한다. 화면에 그대로 노출하지 않고 요약해 보여주는 것도,
    나중에 다시 읽는 것도 dict 쪽이 쉽다(OpenAI 프로토콜이 요구하는 문자열
    형태로의 변환은 `summarizer.build_context`가 모델에 보낼 때만 한다).

    `confirmation`은 위험 도구일 때만 쓴다 — `"pending"`(확인 대기) ->
    `"approved"`/`"declined"`로 바뀐다. 위험하지 않은 도구는 `None`으로 둬서
    즉시 실행됐다는 걸 나타낸다."""
    return {
        "id": message_id or new_message_id(),
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls,
        "confirmation": confirmation,
        "rating": None,
        "rating_note": None,
        "rated_at": None,
    }


def new_tool_result_message(tool_call_id: str, content: str, *, message_id: str | None = None) -> dict[str, Any]:
    return {
        "id": message_id or new_message_id(),
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
        "rating": None,
        "rating_note": None,
        "rated_at": None,
    }


def set_pending_tool_call(conversation_id: str, message_id: str | None) -> dict[str, Any] | None:
    """확인이 필요한 위험 도구 호출이 생기면 그 assistant 메시지의 id를 여기
    걸어둔다. `POST /api/chat/confirm`이 이 값으로 어느 메시지를 재개할지
    찾는다. 해소되면 `None`으로 지운다."""
    conv = load(conversation_id)
    if conv is None:
        return None
    conv["pending_tool_call_message_id"] = message_id
    save(conv)
    return conv


def set_rating(
    conversation_id: str, message_id: str, rating: int | None, note: str | None
) -> dict[str, Any] | None:
    """메시지 하나에 별점을 매기거나(정수) 지운다(`rating=None`). 대화·메시지가
    없으면 None을 돌려준다 — 호출자가 404로 바꾼다."""
    conv = load(conversation_id)
    if conv is None:
        return None
    for msg in conv["messages"]:
        if msg.get("id") == message_id:
            msg["rating"] = rating
            msg["rating_note"] = note
            msg["rated_at"] = _now() if rating is not None else None
            save(conv)
            return conv
    return None


def new_conversation(model: str, system: str | None = None) -> dict[str, Any]:
    """저장하지 않은 새 대화 딕셔너리. 첫 턴을 성공적으로 마쳐야 실제로 저장된다."""
    now = _now()
    return {
        "id": new_id(model),
        "title": None,  # 첫 사용자 메시지로 나중에 채움
        "model": model,
        "system": system or None,
        "messages": [],  # [{role, content}, ...] — 원본 그대로, 압축 대상이 아님
        "summary": None,  # 압축된 이전 대화 요약 (없으면 아직 압축 전)
        "summarized_upto": 0,  # messages[:summarized_upto]가 summary에 반영된 구간
        "pending_tool_call_message_id": None,  # 확인 대기 중인 도구 호출 — 없으면 None
        "created_at": now,
        "updated_at": now,
    }


def save(conv: dict[str, Any]) -> None:
    """저장 시점으로 `updated_at`을 찍고 파일에 쓴다. 호출자가 따로 찍을 필요 없다."""
    conv["updated_at"] = _now()
    path = _path_for_id(conv["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 쓰는 도중 죽어도 기존 파일이 깨지지 않게 원자적으로 교체


def load(conversation_id: str) -> dict[str, Any] | None:
    path = _path_for_id(conversation_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete(conversation_id: str) -> bool:
    path = _path_for_id(conversation_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_summaries(model: str) -> list[dict[str, Any]]:
    """해당 모델의 저장된 대화 목록(제목/수정시각 요약), 최근 수정 순."""
    d = _dir_for(model)
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.json"):
        try:
            conv = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 손상된 파일은 목록에서 조용히 제외
        out.append(
            {
                "id": conv["id"],
                "title": conv.get("title") or "(제목 없음)",
                "updated_at": conv["updated_at"],
            }
        )
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    return out


def derive_title(text: str, limit: int = 40) -> str:
    """첫 사용자 메시지에서 제목을 뽑는다. 개행은 공백으로, 너무 길면 자른다."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
