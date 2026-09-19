"""품질·보안 지표 질문 세트 로더.

세트 폴더의 지표별 JSON 파일 + 공용 거절 표현 목록 + 문서를 읽는다.
캐싱 없음 — 매번 새로 읽는다(`system_prompts.py`, `test_runner._load_probe()`와
같은 관례). 실제 세트는 정답·채점 기준이 그대로 들어있어 저장소에 넣지 않고,
저장소에는 돌아가는 모습을 보이기 위한 공개 샘플 세트(`testsets/sample/`)만 있다.
"""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import bench_config as cfg

TESTSETS_ROOT = Path(__file__).parent / "testsets"
SAMPLE_DIR = TESTSETS_ROOT / "sample"
# 속도 탐침은 세트 폴더와 상관없이 저장소에 든 한 벌을 모든 실행이 읽는다
PROBE_PATH = TESTSETS_ROOT / "probe.json"
SET_FILES = (*cfg.QUALITY_TESTSET_FILES.values(), "refusal_expressions.json", "tool_calling.json")


# 세트 준비가 만든 판이 쌓이는 자리 — 폴더 이름이 만든 때라 이름순 마지막이 최신 판이다
VERSIONS = "versions"


def _testsets_dir() -> Path:
    """실제 세트 파일이 하나라도 있으면 `testsets/`, 하나도 없으면 공개 샘플 세트(`testsets/sample/`).
    파일마다 따로 고르지 않는다 — 두 세트가 한 실행에 섞이면 어느 값이 무엇으로 잰 것인지 알 수 없다.
    판 폴더에만 있는 것도 실제 세트다 — 세트 준비로 처음 만들면 뿌리에는 파일이 없다."""
    folders = sorted((TESTSETS_ROOT / VERSIONS).glob("*"), reverse=True)
    real = any((TESTSETS_ROOT / name).exists() for name in SET_FILES) or any(
        (folder / name).exists() for folder in folders if folder.is_dir() for name in SET_FILES
    )
    if not real and SAMPLE_DIR.is_dir():
        return SAMPLE_DIR
    return TESTSETS_ROOT


TESTSETS_DIR = _testsets_dir()
# 실행 조건의 `문항 집합` — 샘플 세트로 잰 값은 모델을 가르는 값이 아니라는 것을 결과와 리포트가 스스로 말한다
QUESTION_SET = "공개 샘플 세트(값으로 모델을 가르지 않는다)" if TESTSETS_DIR == SAMPLE_DIR else "전체"


def versions(base: Path | None = None) -> list[Path]:
    """만든 판 폴더 — 최신이 앞이다(폴더 이름이 만든 때다)."""
    return sorted((p for p in ((base or TESTSETS_DIR) / VERSIONS).glob("*") if p.is_dir()), reverse=True)


def set_path(name: str, base: Path | None = None) -> Path:
    """그 세트 파일을 어디서 읽는가 — **가장 최근 판**이 있으면 그것이고, 없으면 뿌리의 파일이다.
    세트 준비가 만든 것(문서를 읽는 넷과 긴 컨텍스트)만 판으로 쌓이고, 만들지 않은 세트는 뿌리 것을 그대로 쓴다.

    `base`는 세트 폴더를 달리 볼 때만 준다(지문을 세는 쪽이 자기 폴더를 들고 있다)."""
    base = base or TESTSETS_DIR
    for folder in versions(base):
        candidate = folder / name
        if candidate.exists():
            return candidate
    return base / name


def _load(name: str) -> dict[str, Any]:
    return json.loads(set_path(name).read_text(encoding="utf-8"))


def load_quality_testset(metric: str) -> dict[str, Any]:
    """`bench_config.QUALITY_TESTSET_FILES`의 키(예: "instruction_following")로 로드. 과제용 부분 실행 중이면
    그 실행이 고른 문항만 남긴 모양으로 준다(`question_subset`) — 지표별 실행 함수를 하나도 건드리지 않고 부분 실행이 된다."""
    fname = cfg.QUALITY_TESTSET_FILES[metric]
    return _apply_subset(metric, _load(fname))


# ---------------------------------------------------------------------------
# 과제용 고정 문항 — 세트마다 몇 문항을 뽑는가만 적는다. 문항 ID는 적지 않는다: 아래 규칙이 지금 세트에서 고른다
# ---------------------------------------------------------------------------

ASSIGNMENT_QUESTION_COUNTS: dict[str, int] = {
    "closed_qa": 3,
    "key_coverage": 2,
    "instruction_following": 2,
    "hallucination": 2,
    "over_refusal": 1,
}
ASSIGNMENT_REPEATS = 2  # 같은 질문을 그대로 두 번 — 원문+변형이 아니다
CLOUD_ASSIGNMENT_REPEATS = 1
# 결과를 보지 않고도 같은 답이 나오는 규칙 — 이미 잰 뒤에 고르므로 사전 선정은 만들 수 없고, 규칙으로 대신한다.
# 한 세트 안에서 ID 순이 Use Case(사내 문서 기반 질의응답)와 무관한 쪽을 뽑지 않게, 섞인 세트에서만 문서 문항으로 좁힌다
ASSIGNMENT_RULE = ("세트 안에 문서 문항과 비문서 문항이 섞여 있으면 문서 문항만, 섞이지 않았으면 전부를 두고 "
                   "ID 오름차순으로 필요한 수만큼. Cloud는 그 가운데 세트마다 첫 문항")


def assignment_questions(counts: dict[str, int] | None = None) -> dict[str, list[str]]:
    """과제용 10문항의 ID — `ASSIGNMENT_RULE`대로. 필요한 수보다 적으면 있는 만큼만 준다(채우려고 규칙을 바꾸지 않는다)."""
    out: dict[str, list[str]] = {}
    for metric, n in (counts or ASSIGNMENT_QUESTION_COUNTS).items():
        items = sorted(_load(cfg.QUALITY_TESTSET_FILES[metric]).get("items") or [], key=lambda it: it["id"])
        # 문서 문항이 있으면 그것만(전부 문서인 세트는 그대로 전부다), 없으면 전부
        pool = [it for it in items if it.get("doc")] or items
        out[metric] = [it["id"] for it in pool[:n]]
    return out


def cloud_questions(questions: dict[str, list[str]]) -> dict[str, list[str]]:
    return {metric: ids[:1] for metric, ids in questions.items() if ids}


_subset: ContextVar[dict[str, Any] | None] = ContextVar("question_subset", default=None)


def enter_question_subset(questions: dict[str, list[str]], repeats: int) -> Token:
    """여기부터 이 문맥에서 세트를 읽으면 고른 문항만, 변형 대신 **원문을 `repeats`번**, zero-shot만 남긴다.
    실행 스레드의 문맥에만 걸린다 — 같은 프로세스의 다른 요청(화면·리포트)은 전체 세트를 읽는다. `exit_question_subset`로 푼다."""
    return _subset.set({"questions": questions, "repeats": repeats})


def exit_question_subset(token: Token) -> None:
    _subset.reset(token)


@contextmanager
def question_subset(questions: dict[str, list[str]], repeats: int):
    token = enter_question_subset(questions, repeats)
    try:
        yield
    finally:
        exit_question_subset(token)


def _apply_subset(metric: str, d: dict[str, Any]) -> dict[str, Any]:
    subset = _subset.get()
    if subset is None or metric not in subset["questions"]:
        return d
    wanted = set(subset["questions"][metric])
    items = [{**it, "variants": [it["variants"][0]] * subset["repeats"], "repeat_same_prompt": True}
             for it in d.get("items") or [] if it["id"] in wanted]
    out = {**d, "items": items}
    if "shot_modes" in d:
        out["shot_modes"] = ["zero"]  # few-shot은 입력이 다른 조건이다 — 같은 질문 2회에 섞지 않는다
    return out


def load_refusal_expressions() -> list[str]:
    return _load("refusal_expressions.json")["expressions"]


def refusal_expressions_sha() -> str:
    """지금 쓰는 거절 표현 목록의 지문 — 결과에 적어 두면 어떤 목록으로 채점한 값인지 되짚을 수 있다.
    목록이 바뀌면 같은 답의 점수가 달라지는데, 버전 숫자만으로는 무엇이 달라졌는지 알 수 없다."""
    payload = json.dumps(sorted(load_refusal_expressions()), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 문서 길이 — 실행 조건. 세트의 `doc`은 짧은 판을 가리키고, 긴 판은 같은 이름으로 `documents/long/` 아래에 있다.
# **세트마다가 아니라 실행 하나에 하나다** — 문서를 읽는 세트가 함께 옮겨 가야 한 실행 안에서 지표마다 문서 길이가
# 갈리지 않고, 조건을 한 줄로 적을 수 있다.
# ---------------------------------------------------------------------------

DOCUMENTS_SHORT = "짧은 문서"
DOCUMENTS_LONG = "긴 문서"
DOCUMENT_DIRS = {DOCUMENTS_SHORT: "documents", DOCUMENTS_LONG: "documents/long"}
# 점수·n·게이트·선정이 읽는 판. **짧은 판은 새로 돌지 않는다** — 한 실행에 끼우면 두 바퀴의 호출 순서가 갈리거나 문서 세트가
# 네 번 돌고, 짧은 판 실행은 이미 있다. 짧은 판과의 비교는 같은 모델의 짧은 판 실행과 나란히 한다(과제용 고정 문항은 짧은 판으로만 잰다)
REPRESENTATIVE_DOCUMENT_LENGTH = DOCUMENTS_LONG
# 긴 판의 길이가 어디서 온 값인지 — 조건과 함께 적는다(수에서 끌어낸 값과 구분되게)
LONG_DOCUMENTS_BASIS = "긴 문서를 2천~5천 자로 잡은 것은 실제 사내 문서 표본이 아니라 어림이다"

_document_length: ContextVar[str] = ContextVar("document_length", default=DOCUMENTS_SHORT)


def check_document_length(length: str) -> str:
    if length not in DOCUMENT_DIRS:
        raise ValueError(f"문서 길이는 {' / '.join(DOCUMENT_DIRS)} 중 하나다 — {length!r}")
    return length


def enter_document_length(length: str) -> Token:
    """여기부터 이 문맥에서 문서를 읽으면 그 길이의 판을 읽는다. 실행 스레드의 문맥에만 걸린다 — 같은 프로세스의 다른
    요청(화면·리포트)은 짧은 판을 읽는다. `exit_document_length`로 푼다."""
    return _document_length.set(check_document_length(length))


def exit_document_length(token: Token) -> None:
    _document_length.reset(token)


def doc_path(rel_path: str, length: str | None = None) -> str:
    """세트의 `doc` 경로를 문서 길이에 맞춘 경로로. 긴 판이 없으면 읽을 때 그대로 실패한다 — 조용히 짧은 판으로
    돌면 조건 줄이 거짓이 된다."""
    length = check_document_length(length or _document_length.get())
    short = DOCUMENT_DIRS[DOCUMENTS_SHORT] + "/"
    if length == DOCUMENTS_SHORT:
        return rel_path
    if not rel_path.startswith(short):
        raise ValueError(f"문서 경로가 {short} 아래가 아니라 {length} 판을 찾을 수 없다: {rel_path}")
    return f"{DOCUMENT_DIRS[length]}/{rel_path[len(short):]}"


def document_item_ids(metric: str) -> set[str]:
    """그 세트에서 문서를 읽는 문항 — 두 판을 견줄 때 같은 문항끼리만 세려고 쓴다."""
    return {it["id"] for it in _load(cfg.QUALITY_TESTSET_FILES[metric]).get("items") or [] if it.get("doc")}


def document_metrics() -> list[str]:
    """문서를 읽는 문항이 있는 세트(`QUALITY_TESTSET_FILES`의 키, 그 순서) — 목록을 따로 두지 않고 세트에서 읽는다."""
    return [m for m in cfg.QUALITY_TESTSET_FILES if document_item_ids(m)]


def load_doc(rel_path: str) -> str:
    """`item["doc"]`에 적힌 상대 경로(예: "documents/example.md")를 **지금 문서 길이의 판으로** 읽는다."""
    return (TESTSETS_DIR / doc_path(rel_path)).read_text(encoding="utf-8")


def longest_document(length: str | None = None) -> tuple[str, str] | None:
    """그 판에서 **가장 긴 문서**의 (이름, 본문) — 심은 판까지 센다(지시문이 한 줄 더 들어가 그쪽이 길다).
    실행 전에 `가장 긴 입력이 컨텍스트에 드는가`를 재는 데 쓴다. 문서가 없으면 None."""
    folder = TESTSETS_DIR / DOCUMENT_DIRS[check_document_length(length or REPRESENTATIVE_DOCUMENT_LENGTH)]
    if not folder.exists():
        return None
    best: tuple[str, str] | None = None
    for path in sorted(folder.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if best is None or len(text) > len(best[1]):
            best = (path.relative_to(TESTSETS_DIR).as_posix(), text)
    return best


def document_lengths(length: str = DOCUMENTS_SHORT) -> dict[str, dict[str, Any]]:
    """세트 문항이 읽는 문서마다 **파일 그대로의 글자 수**(공백·줄바꿈 포함)와 그 문서를 읽는 세트(`QUALITY_TESTSET_FILES`의
    키, 그 순서). 문서를 주고 묻는 문항의 입력이 얼마나 긴지는 `컨텍스트 단계`(속도를 재는 채움 글)의 길이와 다른 사실이라
    따로 센다. 글자 수는 `length` 판에서 센다. `{세트의 문서 경로: {"chars": 글자 수, "sets": [세트]}}`"""
    out: dict[str, dict[str, Any]] = {}
    for metric in cfg.QUALITY_TESTSET_FILES:
        for item in load_quality_testset(metric).get("items") or []:
            doc = item.get("doc")
            if not doc:
                continue
            chars = len((TESTSETS_DIR / doc_path(doc, length)).read_text(encoding="utf-8")) if doc not in out else 0
            entry = out.setdefault(doc, {"chars": chars, "sets": []})
            if metric not in entry["sets"]:
                entry["sets"].append(metric)
    return out
