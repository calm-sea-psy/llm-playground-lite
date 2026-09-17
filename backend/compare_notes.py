"""비교 노트.

선택된 실행들의 비교 표 전체(+베이스라인)를 후보로 선택된 각 로컬 모델에게
그대로 주고, 그 모델이 3~5개 불릿으로 특이사항을 쓰게 한다. 표 텍스트는
프론트엔드가 만든다 — 25개 지표의 라벨·단위 정의가 `frontend/src/metrics.js`
하나에만 있고, 백엔드에 같은 정의를 또 만들면 소스가 둘로 갈라진다. 여기서는
그 텍스트를 프롬프트에 그대로 끼워 넣고 모델을 부르는 일만 한다.

노트는 지표가 아니라 표시 정보라 결과 파일에 넣지 않고 별도로 캐시한다 —
"선택된 실행 조합"의 산물이지 실행 하나의 속성이 아니기 때문이다. 캐시 키는
정렬된 run_id 목록 + 표 텍스트의 해시다: 표 텍스트가 이미 지표 값의 결정론적
렌더링이라 이것만 해시해도 "조합"과 "지표 값 지문" 둘 다 잡힌다.
"""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bench_config as cfg
import providers
import model_aliases
import note_verify
import quality_runner
import test_runner

NOTES_DIR = Path(__file__).parent / "test-results" / "compare-notes"

# 노트 프롬프트 버전 — **캐시 키에 넣는다**. 기록만 남기면 같은 조합의 옛 노트가
# `status`에서 "있음"으로 잡혀 그대로 재사용되고, 예시를 준 노트와 안 준 노트가 섞여 별점 비교도
# 공정하지 않다. v1: zero-shot. v2: 1-shot 형식 예시(내용 없음).
NOTE_PROMPT_VERSION = 2


def _now() -> str:
    return datetime.now(UTC).isoformat()


def cache_key(run_ids: list[str], table_text: str) -> str:
    """run_id 정렬 순서와 무관하게 같은 조합이면 같은 키가 나오게 정렬해서
    해시한다. 조합이 바뀌면(모델 추가/제거) 반드시 새 키가 된다."""
    h = hashlib.sha256()
    h.update(f"note-prompt-v{NOTE_PROMPT_VERSION}".encode("utf-8"))
    h.update(b"\x00")
    for rid in sorted(run_ids):
        h.update(rid.encode("utf-8"))
        h.update(b"\x00")
    h.update(table_text.encode("utf-8"))
    return h.hexdigest()


def _path_for(key: str) -> Path:
    return NOTES_DIR / f"{key}.json"


def load(key: str) -> dict[str, Any] | None:
    path = _path_for(key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save(data: dict[str, Any]) -> None:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(data["key"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# 1-shot 형식 예시 — **모양만** 보여준다(불릿 개수, 표의 값을 그대로 인용하는 문장 꼴). 내용까지 주면
# "노트를 쓴 솜씨 자체가 선정 근거"라는 기능이 죽는다. 자리표시자라 어떤 표의 사실도 담지 않는다.
_FORMAT_EXAMPLE = (
    "- <지표 이름>: <모델 A> <표의 값> vs <모델 B> <표의 값> — <이 차이가 뜻하는 것 한 줄>\n"
    "- <지표 이름>: <모델 C> <표의 값>, 기준선 <표의 값> — <한 줄 해석>\n"
    "- <지표 이름>: <모델 A>·<모델 B> 모두 <표의 값> — <한 줄 해석>"
)


def _build_prompt(table_text: str) -> list[dict[str, str]]:
    system = (
        "당신은 여러 로컬 LLM의 벤치마크 비교 표를 분석하는 역할입니다. "
        "표에 있는 값만 근거로 삼으세요 — 표에 없는 수치를 언급하면 안 됩니다."
    )
    user = (
        "다음은 여러 모델의 성능 테스트 결과 비교 표입니다(지표와 방향 ↑/↓, 각 모델의 값, "
        "클라우드 기준선 값). 두드러진 차이나 특이사항을 3~5개의 불릿으로 "
        "정리하세요. 각 불릿은 표의 구체적인 값을 근거로 들어야 합니다.\n"
        "모델 이름은 표 머리글의 이름을, 값은 표의 칸을 단위까지 그대로 옮기세요.\n\n"
        "형식 예시(꺾쇠는 자리표시자 — 내용은 표에서 읽어 채웁니다):\n"
        f"{_FORMAT_EXAMPLE}\n\n"
        f"{table_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def aliases_for(run_ids: list[str]) -> dict[str, str]:
    """run_id → 별칭. 노트 입력 표의 머리글과 리포트가 같은 규칙(`model_aliases`)을 쓴다 —
    `POST /api/compare/aliases`도 이 함수를 그대로 부른다."""
    models = []
    for rid in run_ids:
        result = test_runner.load_result(rid)
        if result is not None:
            models.append({"id": rid, "label": result["model"], "started_at": result.get("started_at")})
    return model_aliases.aliases(models)


def name_variants(run_ids: list[str]) -> dict[str, list[str]]:
    """표 머리글(별칭) → 같은 모델의 전체 이름. 노트는 모델을 전체 이름이나 변형으로도 부른다."""
    out: dict[str, list[str]] = {}
    for rid, alias in aliases_for(run_ids).items():
        result = test_runner.load_result(rid)
        if result is not None:
            out.setdefault(alias, []).append(result["model"])
    return out


def _empty(key: str, run_ids: list[str]) -> dict[str, Any]:
    return {"key": key, "run_ids": sorted(run_ids), "generated_at": _now(), "notes": {}}


def generate_one(run_ids: list[str], table_text: str, run_id: str) -> dict[str, Any]:
    """조합 캐시 안의 **모델 하나**만 생성한다(노트 생성을 모델별 호출로 쪼갠다).

    캐시는 조합 키 아래 모델별 항목이다 — 노트의 입력이 비교 표 전체라 모델 이름만으로 키를
    잡으면 다른 조합의 노트가 재사용된다. 성공한 노트만 저장하므로 실패한 모델은 다음번에
    다시 시도된다("성공한 노트는 다시 만들지 않는다"). 생성 소요를 함께 남겨 다음부터
    확인 패널이 실측 평균을 보여줄 수 있게 한다. 실패하면 예외를 그대로 던진다 — 부분 실패를
    어떻게 다룰지는 호출자(내보내기 흐름)가 정한다."""
    key = cache_key(run_ids, table_text)
    data = load(key) or _empty(key, run_ids)
    if run_id in data["notes"]:
        return data
    result = test_runner.load_result(run_id)
    if result is None:
        raise ValueError(f"실행을 찾을 수 없습니다: {run_id}")
    started = time.monotonic()
    reply = quality_runner.ask_model(
        result["model"],
        _build_prompt(table_text),
        provider=providers.get_provider("ollama"),  # 노트는 항상 로컬 후보 모델만 쓴다
        sampling=cfg.SAMPLING,
        num_predict=cfg.COMPARE_NOTE_NUM_PREDICT,
        timeout=cfg.QUALITY_TIMEOUT,
    )
    text = quality_runner.as_reply(reply).text
    # 긴 호출 사이에 다른 요청(예: 별점)이 같은 파일을 고쳤을 수 있으니 다시 읽고 합친다
    data = load(key) or data
    data["notes"][run_id] = {
        "model": result["model"],
        "note": text,
        "prompt_version": NOTE_PROMPT_VERSION,
        # 인용 검증 — 노트가 받은 표와 대조한다. 실패한 불릿은 빼지 않고 표시한다
        "verification": note_verify.verify(text, table_text, name_variants(run_ids)),
        "rating": None,
        "rating_note": None,
        "rated_at": None,
        "generated_at": _now(),
        "duration_sec": round(time.monotonic() - started, 2),
    }
    _save(data)
    return data


def _refresh_verification(data: dict[str, Any], run_ids: list[str], table_text: str) -> dict[str, Any]:
    """검증 규칙 버전이 바뀐 노트는 **읽을 때 다시 검증**하고 캐시에 적는다. 노트 본문과 입력 표는 그대로라
    다시 생성할 이유가 없고, 옛 표식을 그대로 두면 옳은 불릿이 계속 불일치로 찍힌다. 캐시에 적는 이유는
    별점 저장 응답(표 텍스트를 받지 않는다)이 옛 표식으로 화면을 되돌리지 않게 하려는 것이다."""
    stale = [e for e in data["notes"].values() if (e.get("verification") or {}).get("version") != note_verify.VERSION]
    if not stale:
        return data
    variants = name_variants(run_ids)
    for entry in stale:
        entry["verification"] = note_verify.verify(entry["note"], table_text, variants)
    _save(data)
    return data


def generate(run_ids: list[str], table_text: str) -> dict[str, Any]:
    """화면의 "노트 생성" 버튼 — 모델마다 `generate_one`을 부르고, 한 모델이 실패해도 나머지는
    계속한다. 실패한 모델은 `failed`에 사유와 함께 담아 돌려준다(캐시에는 넣지 않는다)."""
    key = cache_key(run_ids, table_text)
    data = load(key) or _empty(key, run_ids)
    failed: dict[str, str] = {}
    for run_id in run_ids:
        try:
            data = generate_one(run_ids, table_text, run_id)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — 노트는 표시 정보다. 한 모델 실패가 전체를 막지 않는다
            failed[run_id] = str(exc)
    return {**_refresh_verification(data, run_ids, table_text), "failed": failed}


def status(run_ids: list[str], table_text: str) -> dict[str, Any]:
    """내보내기 확인 패널용 — 이 조합에서 이미 있는 노트, 빠진 run_id, 그리고 **실측 평균 소요**.
    소요 기록이 하나도 없으면 None이다 — 기록한 적 없는 시간을 "몇 분"으로 적으면 근거 없는 숫자다."""
    key = cache_key(run_ids, table_text)
    data = load(key)
    data = _refresh_verification(data, run_ids, table_text) if data else _empty(key, run_ids)
    durations = []
    if NOTES_DIR.exists():
        for path in NOTES_DIR.glob("*.json"):
            try:
                other = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            durations += [e["duration_sec"] for e in other.get("notes", {}).values() if e.get("duration_sec") is not None]
    return {
        **data,
        "missing": [rid for rid in run_ids if rid not in data["notes"]],
        "avg_duration_sec": (sum(durations) / len(durations)) if durations else None,
    }


def set_rating(key: str, run_id: str, rating: int | None, note: str | None) -> dict[str, Any] | None:
    data = load(key)
    if data is None or run_id not in data["notes"]:
        return None
    entry = data["notes"][run_id]
    entry["rating"] = rating
    entry["rating_note"] = note
    entry["rated_at"] = _now() if rating is not None else None
    _save(data)
    return data
