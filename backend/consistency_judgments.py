"""일관성 대표 쌍 판정 — 후보 쌍 파일, 가려진 판정 화면의 조회·저장.

일관성 점수가 낮은 칸(문항 × 모델)마다 **대표 쌍** 하나를 골라 사람이 읽고 `채점기 탓 / 모델 탓 / 섞임` 중 하나를
고른다. 판정은 결과 파일이 아니라 `test-results/consistency-judgments/`의 쌍 파일에 쌍과 같은 줄로 적는다
(측정은 불변). 리포트 상세 장도 이 파일의 쌍을 읽어 인쇄한다.

규칙:
- **칸의 열쇠는 답이 실제로 나온 실행 + 문항**이고, 쌍마다 두 답의 digest를 싣는다. 재실행으로 답이 바뀌면
  옛 판정을 새 답에 붙이지 않고 `무효_라벨`로 옮긴다.
- **판정이 있는 칸은 쌍을 다시 고르지 않는다** — 유사도 판정기가 바뀌어 가장 낮은 쌍이 달라져도 사람이 읽은
  쌍을 지킨다. 쌍마다 고를 때 쓴 유사도 버전을 적는다.
- **오염 표시는 기계가 채운다**(`길이 한도` = 한쪽이라도 출력 상한에 걸림, `언어 혼입` = 한글·ASCII 라틴 밖 글자).
  `길이 한도`가 붙은 대표 쌍은 증거를 읽을 수 없어 `판정 보류`가 자동으로 붙고 판정 대상에서 빠진다.
- **판정하는 동안 모델을 가린다.** 조회 응답에는 모델·별칭·실행 id·쌍 id·점수·오염 표시를 싣지 않고,
  칸은 서버만 아는 난수 토큰으로 주고받는다. 순서는 서버 프로세스마다 한 번 섞는다. 판정 대상 칸이 모두
  끝나면 드러내고, 그 뒤에 고친 판정에는 `가림_해제_후_수정`을 남긴다.
- **가림 해제는 지우지 않는다.** 다시 뽑아 칸이 전부 새로 바뀌어 화면은 다시 가려도, 한 번 모델을 본 사람의
  기억은 가려지지 않는다 — 옛 해제 시각을 `이전_판본_가림_해제`로 옮겨 적고, 그 뒤 판정에 `이전_판본_해제됨`을 남긴다.
"""

import hashlib
import json
import logging
import random
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

import model_aliases
import quality_scoring as qs
import quality_testsets as qt
import response_health as rh

JUDGMENTS_DIR = Path(__file__).parent / "test-results" / "consistency-judgments"
PAIRS_FILE = "consistency_pairs.json"
EVAL_FILE = "consistency_judge_eval.json"

LOWEST = "within_lowest"
KINDS = (LOWEST, "within_highest", "cross")
HUMAN_VERDICTS = ("채점기 탓", "모델 탓", "섞임")
HOLD = "판정 보류"
LANGUAGE_MIX = "언어 혼입"
LENGTH_LIMIT = "길이 한도"
LABELS = ("positive", "negative")
# 사람이 한 일이라 다시 뽑아도 이어 받는 칸 — 판정 보류와 오염 표시는 기계가 매번 다시 채운다
_HUMAN_FIELDS = ("판정", "판정_시각", "가림_해제_후_수정", "이전_판본_해제됨", "수정_이력", "골든_제외", "label", "note")
# 판정에 딸린 기록일 뿐 그것만으로는 사람이 한 일로 치지 않는 칸
_ANNOTATIONS = {"판정_시각", "가림_해제_후_수정", "이전_판본_해제됨", "수정_이력", "골든_제외"}

_lock = threading.Lock()
_log = logging.getLogger("uvicorn.error")
_logged_errors: frozenset[str] = frozenset()


def pairs_path() -> Path:
    return JUDGMENTS_DIR / PAIRS_FILE


def eval_path() -> Path:
    return JUDGMENTS_DIR / EVAL_FILE


def load() -> dict[str, Any]:
    path = pairs_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write(data: dict[str, Any]) -> None:
    path = pairs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def answer_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def consistency_source(run: dict[str, Any]) -> str:
    """일관성 답이 실제로 나온 실행의 id. 지표 단위 재실행을 합친 뷰에서는 부모가 아니라 재실행이다."""
    return ((run.get("provenance") or {}).get("consistency") or {}).get("run_id") or run["id"]


def contamination(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    marks = []
    if qs.foreign_letters(a["text"]) or qs.foreign_letters(b["text"]):
        marks.append(LANGUAGE_MIX)
    if rh.LENGTH_LIMIT in (a["completion"], b["completion"]):
        marks.append(LENGTH_LIMIT)
    return marks


def derived_label(pair: dict[str, Any]) -> str | None:
    """판정에서 나오는 골든 라벨. 오염 표시가 있는 쌍은 어느 쪽으로도 쓰지 않는다."""
    if pair.get("오염_표시"):
        return None
    return {"채점기 탓": "positive", "모델 탓": "negative"}.get(pair.get("판정"))


def _call_at(calls: list[dict[str, Any] | None], i: int) -> dict[str, Any] | None:
    return calls[i] if i < len(calls) else None


def _side(source: str, run: dict[str, Any], alias: str, index: int, text: str, call: dict[str, Any] | None) -> dict[str, Any]:
    return {"run_id": source, "model": run["model"], "alias": alias, "index": index, "text": text,
            "digest": answer_digest(text), "completion": rh.completion(call)}


def _candidate(pair_id: str, item_id: str, question: str, kind: str, a: dict[str, Any], b: dict[str, Any], sim: float) -> dict[str, Any]:
    marks = contamination(a, b)
    return {"id": pair_id, "item_id": item_id, "question": question, "kind": kind, "a": a, "b": b,
            "char_similarity": round(sim, 4), "similarity_version": qs.JUDGE_VERSIONS["similarity"],
            "오염_표시": marks, "판정": HOLD if kind == LOWEST and LENGTH_LIMIT in marks else None, "label": None}


def candidates(runs: list[dict[str, Any]], questions: dict[str, str], aliases: dict[str, str],
               similarity: Callable[[str, str], float] = qs.similarity) -> list[dict[str, Any]]:
    """판정 후보 쌍. 판정은 사람이 하므로 여기서는 고르고 기계가 채울 칸만 채운다.

    - `within_lowest` — 칸(문항 × 모델)의 **대표 쌍**. 모델 안에서 유사도가 가장 낮은 쌍이고 전수 판정의 대상이다.
    - `within_highest` — 모델 안에서 가장 높은 쌍. 대개 쉬운 양성이다.
    - `cross` — 같은 질문에 대한 두 모델의 첫 답. 주제는 같고 주장이 다를 수 있는 음성 후보다.

    세 종류 모두 **`완료`인 답을 먼저** 쓴다 — 모델 안의 두 종류는 두 답이 모두 완료인 쌍에서 먼저 고르고
    (`response_health.pick_pair`), `cross`는 모델마다 첫 완료 답을 쓴다. 빈 답은 후보에서 뺀다."""
    entries = {r["id"]: {e["id"]: e for e in r["metrics"]["consistency"]["detail"]} for r in runs}
    pairs: list[dict[str, Any]] = []
    for run in runs:
        alias, source = aliases[run["id"]], consistency_source(run)
        for item_id, entry in entries[run["id"]].items():
            responses, calls = entry["responses"], entry.get("calls") or []
            for kind, highest in ((LOWEST, False), ("within_highest", True)):
                picked = rh.pick_pair(responses, calls, similarity, highest=highest)
                if picked is None:
                    continue
                i, j, sim, _ = picked
                pairs.append(_candidate(
                    f"{item_id}:{kind}:{source}#{i + 1}~#{j + 1}", item_id, questions.get(item_id, item_id), kind,
                    _side(source, run, alias, i, responses[i], _call_at(calls, i)),
                    _side(source, run, alias, j, responses[j], _call_at(calls, j)), sim))
    for item_id in questions:
        firsts = []
        for run in runs:
            entry = entries[run["id"]].get(item_id) or {}
            responses, calls = entry.get("responses") or [], entry.get("calls") or []
            nonempty = [i for i, t in enumerate(responses) if (t or "").strip()]
            complete = [i for i in nonempty if rh.completion(_call_at(calls, i)) == rh.COMPLETE]
            first = (complete or nonempty or [None])[0]
            if first is not None:
                firsts.append((run, first, responses[first], _call_at(calls, first)))
        for (ra, ia, ta, ca), (rb, ib, tb, cb) in combinations(firsts, 2):
            sa, sb = consistency_source(ra), consistency_source(rb)
            pairs.append(_candidate(
                f"{item_id}:cross:{sa}#{ia + 1}~{sb}#{ib + 1}", item_id, questions[item_id], "cross",
                _side(sa, ra, aliases[ra["id"]], ia, ta, ca), _side(sb, rb, aliases[rb["id"]], ib, tb, cb),
                similarity(ta, tb)))
    return pairs


def human_fields(pair: dict[str, Any]) -> dict[str, Any]:
    """사람이 한 일. 자동으로 붙는 `판정 보류`는 여기 들지 않는다."""
    out = {k: pair[k] for k in _HUMAN_FIELDS if pair.get(k) not in (None, "", [], False)}
    if out.get("판정") == HOLD:
        del out["판정"]
    return out if (set(out) - _ANNOTATIONS) else {}


def _responses_of(runs: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    return {(consistency_source(r), e["id"]): e["responses"] for r in runs for e in r["metrics"]["consistency"]["detail"]}


def merge(pairs: list[dict[str, Any]], old_pairs: list[dict[str, Any]], runs: list[dict[str, Any]],
          aliases: dict[str, str]) -> list[dict[str, Any]]:
    """새로 뽑은 후보에 옛 파일에서 사람이 한 일을 옮긴다. 무효가 된 것의 목록을 돌려준다.

    - **판정이 있는 대표 쌍은 그 쌍을 그대로 지킨다.** 같은 칸(답을 낸 실행 + 문항)이 이번 후보에 있고 두 답의
      digest가 그 실행의 같은 자리 답과 같으면, 새로 고른 쌍 대신 옛 쌍을 둔다.
    - 나머지는 쌍 id와 두 답 digest가 모두 같을 때만 이어 받는다.
    - 그 밖에는 붙이지도 버리지도 않고 `사유`와 함께 돌려준다. 사람이 읽고 적은 값은 다시 만들 수 없다."""
    responses = _responses_of(runs)
    by_id = {p["id"]: p for p in pairs}
    lowest_by_cell = {(p["a"]["run_id"], p["item_id"]): p for p in pairs if p["kind"] == LOWEST}
    invalid = []
    for old in old_pairs:
        human = human_fields(old)
        if not human:
            continue
        a, b = old.get("a") or {}, old.get("b") or {}
        if None in (a.get("digest"), b.get("digest")):
            invalid.append(_invalid(old, human, "옛 형식이라 답 digest가 없어 같은 답인지 확인할 수 없다"))
            continue
        if old.get("kind") == LOWEST:
            cell = (a.get("run_id"), old.get("item_id"))
            current = lowest_by_cell.get(cell)
            texts = responses.get(cell)
            if current is None or texts is None:
                invalid.append(_invalid(old, human, "이번 후보에 같은 칸(답을 낸 실행·문항)이 없다(재실행으로 답이 바뀌었을 수 있다)"))
                continue
            if not _same_answers(a, b, texts):
                invalid.append(_invalid(old, human, "같은 칸이지만 저장된 답이 실행의 답과 다르다"))
                continue
            kept = {**old, "a": {**a, "alias": aliases.get(_parent_of(runs, cell[0]), a.get("alias"))},
                    "b": {**b, "alias": aliases.get(_parent_of(runs, cell[0]), b.get("alias"))}}
            kept["오염_표시"] = contamination(kept["a"], kept["b"])
            pairs[pairs.index(current)] = kept
            lowest_by_cell[cell] = kept
            continue
        new = by_id.get(old["id"])
        if new is None:
            invalid.append(_invalid(old, human, "이번 후보에 같은 id의 쌍이 없다(재실행으로 답이 바뀌었을 수 있다)"))
        elif (a["digest"], b["digest"]) != (new["a"]["digest"], new["b"]["digest"]):
            invalid.append(_invalid(old, human, "같은 id지만 답 digest가 다르다(같은 자리의 답이 바뀌었다)"))
        else:
            new.update(human)
    return invalid


def _parent_of(runs: list[dict[str, Any]], source: str) -> str:
    return next((r["id"] for r in runs if consistency_source(r) == source), source)


def _same_answers(a: dict[str, Any], b: dict[str, Any], texts: list[str]) -> bool:
    return all(side["index"] < len(texts) and answer_digest(texts[side["index"]]) == side["digest"] for side in (a, b))


def _invalid(old: dict[str, Any], human: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"id": old.get("id"), "사유": reason, **human, "a": old.get("a"), "b": old.get("b")}


def run_aliases(runs: list[dict[str, Any]]) -> dict[str, str]:
    return model_aliases.aliases([{"id": r["id"], "label": r["model"], "started_at": r.get("started_at")} for r in runs])


def set_questions() -> dict[str, str]:
    return {it["id"]: it["prompt"] for it in qt.load_quality_testset("consistency")["items"]}


def extract(runs: list[dict[str, Any]], questions: dict[str, str], aliases: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """후보를 뽑아 옛 파일과 합친 새 파일 내용과, 이번에 새로 무효가 된 것을 돌려준다(쓰지는 않는다)."""
    old = load()
    pairs = candidates(runs, questions, aliases)
    invalid = merge(pairs, old.get("pairs", []), runs, aliases)
    data = {
        "extracted_at": datetime.now(UTC).isoformat(),
        "runs": [{"run_id": r["id"], "consistency_run_id": consistency_source(r), "model": r["model"],
                  "started_at": r.get("started_at")} for r in runs],
        "pairs": pairs,
        "무효_라벨": (old.get("무효_라벨") or []) + invalid,
    }
    # 이어 받은 판정이 남으면 해제 상태를 잇고, 전부 새 칸이면 화면은 다시 가리되 옛 해제는 기록으로 옮긴다
    previous = list(old.get("이전_판본_가림_해제") or [])
    if old.get("가림_해제_시각"):
        if any(human_fields(p).get("판정") for p in pairs):
            data["가림_해제_시각"] = old["가림_해제_시각"]
        else:
            previous.append(old["가림_해제_시각"])
    if previous:
        data["이전_판본_가림_해제"] = previous
    for key in ("골든_배정", "이전_골든_배정"):
        if old.get(key):
            data[key] = old[key]
    return data, invalid


GOLDEN_PER_CLASS = 4


def golden_pools(pairs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """골든 후보 — 오염 없는 대표 쌍 가운데 판정이 라벨을 정하는 것(채점기 탓 = 양성, 모델 탓 = 음성). id 순."""
    lowest = [p for p in pairs if p.get("kind") == LOWEST]
    return {label: sorted(p["id"] for p in lowest if derived_label(p) == label) for label in LABELS}


def validation_capacity(pairs: list[dict[str, Any]], golden_ids: set[str]) -> int:
    """거짓 양성을 잴 수 있는 칸 — 골든에 안 든 깨끗한 음성 + 오염 없는 `섞임`. 언어 혼입 칸은 어느 쪽 증거로도 쓰지 않는다."""
    lowest = [p for p in pairs if p.get("kind") == LOWEST and not p.get("오염_표시")]
    return sum(1 for p in lowest if p["id"] not in golden_ids and p.get("판정") in ("모델 탓", "섞임"))


def assign_golden(data: dict[str, Any], *, seed: int | None = None, reassign: bool = False) -> dict[str, Any]:
    """골든 4+4를 한 번 배정해 `label` 칸에 고정한다(파일 내용을 고치고 배정 기록을 돌려준다).

    - 대표 쌍 판정이 모두 끝나야 한다 — 판정 하나만 바뀌어도 후보 모음이 달라져 같은 씨앗으로 다른 넷이 뽑힌다.
    - 양성·음성 모두 **같은 씨앗으로 무작위** 넷을 뽑는다. 음성을 "어려운 순"으로 고르면 판정기가 자기 시험지를
      고르게 된다. 씨앗은 확인용으로 남기고, 재현은 씨앗이 아니라 고정된 `label`이 맡는다.
    - 이미 배정돼 있으면 거절한다. 다시 배정은 `reassign`으로만 하고 옛 배정은 `이전_골든_배정`에 남는다
      (골든이 바뀌면 경계가 바뀐다 — 그 비용이 드러나야 한다)."""
    pairs = data.get("pairs", [])
    lowest = [p for p in pairs if p.get("kind") == LOWEST]
    unjudged = [p["id"] for p in lowest if not p.get("판정")]
    if unjudged:
        raise ValueError(f"판정하지 않은 대표 쌍이 {len(unjudged)}칸 있다 — 전수 판정을 먼저 끝낸다")
    if data.get("골든_배정") and not reassign:
        raise ValueError("골든이 이미 배정돼 있다 — 다시 배정하려면 reassign 명령을 쓴다")
    pools = golden_pools(pairs)
    short = {label: len(ids) for label, ids in pools.items() if len(ids) < GOLDEN_PER_CLASS}
    if short:
        raise ValueError(f"골든 후보가 넷 미만이다 {short} — cross 보충 갈래는 아직 만들지 않았다")
    seed = secrets.randbits(32) if seed is None else seed
    rng = random.Random(seed)
    chosen = {label: sorted(rng.sample(pools[label], GOLDEN_PER_CLASS)) for label in LABELS}
    if data.get("골든_배정"):
        data["이전_골든_배정"] = [*(data.get("이전_골든_배정") or []), data["골든_배정"]]
    for p in pairs:
        p["label"] = None
        p.pop("골든_제외", None)  # 옛 제외 기록은 이전_골든_배정에 남아 있다
    by_id = {p["id"]: p for p in pairs}
    for label, ids in chosen.items():
        for pid in ids:
            by_id[pid]["label"] = label
    golden_ids = {pid for ids in chosen.values() for pid in ids}
    record = {
        "seed": seed,
        "assigned_at": datetime.now(UTC).isoformat(),
        **chosen,
        "candidates": {label: len(ids) for label, ids in pools.items()},
        "validation_capacity": validation_capacity(pairs, golden_ids),
    }
    data["골든_배정"] = record
    return record


def validate(pairs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(오류, 알림). 오류는 파일을 믿을 수 없는 경우이고, 알림은 고정된 라벨과 지금 판정이 어긋난 경우다."""
    errors, warnings = [], []
    for p in pairs:
        pid = p.get("id", "?")
        a, b = p.get("a") or {}, p.get("b") or {}
        if any(answer_digest(side.get("text") or "") != side.get("digest") for side in (a, b)):
            errors.append(f"{pid}: 판정 무효 — 저장된 답이 digest와 다르다")
            continue
        marks = contamination(a, b)
        if p.get("오염_표시") != marks:
            errors.append(f"{pid}: 오염 표시는 기계가 채운다 — 저장 {p.get('오염_표시')!r} · 계산 {marks!r}")
        verdict = p.get("판정")
        if verdict is not None and p.get("kind") != LOWEST:
            errors.append(f"{pid}: 판정은 대표 쌍({LOWEST})에만 붙는다")
        elif verdict == HOLD and LENGTH_LIMIT not in marks:
            errors.append(f"{pid}: {HOLD}는 길이 한도가 붙은 쌍에 자동으로만 붙는다")
        elif verdict in HUMAN_VERDICTS and LENGTH_LIMIT in marks:
            errors.append(f"{pid}: 길이 한도가 붙은 쌍은 {HOLD}다 — 사람 판정 {verdict!r}")
        elif verdict not in (None, HOLD, *HUMAN_VERDICTS):
            errors.append(f"{pid}: 판정은 {'/'.join(HUMAN_VERDICTS)} 중 하나 — {verdict!r}")
        if p.get("label") not in (None, *LABELS):
            errors.append(f"{pid}: label은 {'/'.join(LABELS)} 중 하나 — {p['label']!r}")
        elif p.get("label") and p.get("kind") == LOWEST and derived_label(p) != p["label"]:
            warnings.append(f"{pid}: 고정된 라벨 {p['label']}과 지금 판정({verdict}, 오염 {marks or '없음'})이 어긋난다")
    return errors, warnings


# ---------------------------------------------------------------------------
# 가려진 판정 — 화면 API
# ---------------------------------------------------------------------------

_session: dict[str, Any] = {"ids": frozenset(), "order": [], "token_of": {}, "id_of": {}}


def _ensure_session(ids: list[str]) -> None:
    """칸 토큰과 순서는 서버 프로세스마다 한 번 만든다. 칸 구성이 바뀌면(다시 뽑음) 새로 만든다.
    토큰은 쌍 id의 해시가 아니라 난수다 — 해시면 실행 id를 짐작한 사람이 맞는지 확인할 수 있다."""
    if frozenset(ids) == _session["ids"]:
        return
    order = list(ids)
    random.SystemRandom().shuffle(order)
    token_of = {pid: secrets.token_urlsafe(12) for pid in order}
    _session.update(ids=frozenset(ids), order=order, token_of=token_of, id_of={t: pid for pid, t in token_of.items()})


def _public_cell(p: dict[str, Any], revealed: bool) -> dict[str, Any]:
    cell = {
        "item_id": p["item_id"], "question": p["question"],
        "a": {"index": p["a"]["index"], "text": p["a"]["text"]},
        "b": {"index": p["b"]["index"], "text": p["b"]["text"]},
        "verdict": p.get("판정"), "judged_at": p.get("판정_시각"),
    }
    if revealed:
        cell.update(
            pair_id=p["id"], model=p["a"]["model"], alias=p["a"]["alias"], run_id=p["a"]["run_id"],
            contamination=p.get("오염_표시") or [], char_similarity=p.get("char_similarity"),
            similarity_version=p.get("similarity_version"), label=p.get("label"),
            completion=[p["a"]["completion"], p["b"]["completion"]],
            edited_after_reveal=bool(p.get("가림_해제_후_수정")),
            judged_after_previous_reveal=bool(p.get("이전_판본_해제됨")),
            edit_history=p.get("수정_이력") or [],
            golden_excluded=p.get("골든_제외"),
        )
    return cell


def state() -> dict[str, Any]:
    with _lock:
        return _state(load())


def _state(data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return {"available": False}
    lowest = [p for p in data.get("pairs", []) if p.get("kind") == LOWEST]
    held = [p for p in lowest if p.get("판정") == HOLD]
    targets = [p for p in lowest if p.get("판정") != HOLD]
    revealed = bool(data.get("가림_해제_시각"))
    errors, warnings = validate(data.get("pairs", []))
    if not revealed:
        _log_hidden(errors + warnings)
    _ensure_session([p["id"] for p in targets])
    by_id = {p["id"]: p for p in targets}
    cells = [{"token": _session["token_of"][pid], **_public_cell(by_id[pid], revealed)} for pid in _session["order"]]
    return {
        "available": True,
        "revealed": revealed,
        "revealed_at": data.get("가림_해제_시각"),
        "previously_revealed_at": data.get("이전_판본_가림_해제") or [],
        "progress": {"judged": sum(1 for p in targets if p.get("판정")), "targets": len(targets),
                     "held": len(held), "total": len(lowest)},
        "cells": cells,
        "held_cells": [_public_cell(p, True) for p in held] if revealed else [],
        "golden": ({"counts": golden_counts(data.get("pairs", [])),
                    "excluded": len((data.get("골든_배정") or {}).get("excluded") or [])}
                   if revealed and data.get("골든_배정") else None),
        # 비교 화면이 지금 고른 실행과 대조하는 용도 — 칸과 실행의 대응은 싣지 않는다
        "runs": [{"run_id": r["run_id"], "model": r["model"]} for r in data.get("runs", [])],
        "similarity_version": qs.JUDGE_VERSIONS["similarity"],
        "invalid_count": len(data.get("무효_라벨") or []),
        # 오류 문장에는 쌍 id(= 실행 id)가 들어 있어, 가림 중에는 개수만 싣는다 — 내용은 CLI 확인 명령으로 본다
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors if revealed else [],
        "warnings": warnings if revealed else [],
    }


# ---------------------------------------------------------------------------
# 판정 파일과 비교에 든 실행 — 어긋나면 그 실행 기준으로 다시 만든다
# ---------------------------------------------------------------------------


def _compared_runs(run_ids: list[str], load_result: Callable[[str], dict[str, Any] | None]) -> list[dict[str, Any]]:
    """비교에 든 실행 중 일관성 답이 있는 것(재실행 병합 뷰). 답이 없는 실행은 판정할 칸이 없어 뺀다."""
    runs = []
    for rid in run_ids:
        result = load_result(rid)
        if result and ((result.get("metrics") or {}).get("consistency") or {}).get("detail"):
            runs.append({**result, "id": rid})
    return runs


def _run_label(run: dict[str, Any]) -> dict[str, Any]:
    return {"model": run.get("model"), "started_at": run.get("started_at")}


def _missing(data: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    in_file = {r.get("consistency_run_id") or r.get("run_id") for r in data.get("runs") or []}
    return [r for r in runs if consistency_source(r) not in in_file]


def coverage(run_ids: list[str], load_result: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    """비교에 든 실행의 칸이 판정 파일에 다 있나. 리포트는 판정을 **답을 낸 실행 + 문항**으로 찾으므로, 파일에 없는
    실행이 하나라도 있으면 그 실행의 판정칸은 비어 나온다 — 화면에 판정이 차 있어도 다른 실행의 칸이다.
    - 대조는 실행 id가 아니라 **답을 낸 실행**으로 한다: 지표 단위 재실행은 부모 id가 같아도 답이 다르다.
    - 파일이 고른 실행보다 많은 것은 어긋남이 아니다 — 고른 실행의 칸은 다 있다.
    - 옛 실행과 새 실행은 모델 이름이 같아 이름만으로는 구분되지 않는다 — 시작 시각을 함께 싣는다.
      칸과 실행의 대응은 싣지 않는다(가림)."""
    runs = _compared_runs(run_ids, load_result)
    data = load()
    missing = _missing(data, runs) if data else runs
    return {
        "applicable": bool(runs),
        "covered": bool(runs) and bool(data) and not missing,
        "missing": [_run_label(r) for r in missing],
        "file_runs": [_run_label(r) for r in data.get("runs") or []],
    }


def rebuild(run_ids: list[str], load_result: Callable[[str], dict[str, Any] | None], *,
            dry_run: bool = False) -> dict[str, Any]:
    """비교에 든 실행 기준으로 판정 칸을 다시 만든다 — 명령줄 추출과 **같은 규칙**(`extract`)이다.
    - **같은 칸에서 같은 답을 읽은 판정만 이어 받는다.** 질문이 같아도 답이 다르면 잇지 않는다 — 판정은 두 답의 글을
      읽고 내린 것이라, 이으면 아무도 읽지 않은 답에 판정이 붙는다.
    - 잇지 못한 사람 판정·라벨은 **지우지 않고** `무효_라벨`로 옮긴다. 이어 받은 판정이 없으면 새 칸을 다시 가린다.
    - 고른 실행의 칸이 이미 다 있으면 거절한다(LookupError) — 다시 만들면 파일에만 있는 다른 실행의 판정이 무효로 옮겨진다.
    - `dry_run`이면 쓰지 않고 무엇이 바뀌는지만 센다 — 화면의 확인 단계가 그 수를 보여 준다."""
    runs = _compared_runs(run_ids, load_result)
    if not runs:
        raise ValueError("고른 실행에 일관성 답이 없다 — 판정할 칸이 없다")
    with _lock:
        old = load()
        if old and not _missing(old, runs):
            raise LookupError("고른 실행의 판정 칸이 이미 파일에 다 있다 — 다시 만들 까닭이 없다")
        data, invalid = extract(runs, set_questions(), run_aliases(runs))
        lowest = [p for p in data["pairs"] if p["kind"] == LOWEST]
        held = sum(1 for p in lowest if p.get("판정") == HOLD)
        carried = sum(1 for p in lowest if human_fields(p).get("판정"))
        summary = {"runs": [_run_label(r) for r in runs], "carried": carried, "to_judge": len(lowest) - held - carried,
                   "held": held, "moved_to_invalid": len(invalid), "blind": not data.get("가림_해제_시각")}
        if dry_run:
            return {"preview": summary}
        write(data)
        return {**_state(data), "rebuilt": summary}


def _log_hidden(messages: list[str]) -> None:
    """가림 중 화면에서 뺀 오류·알림 전문은 서버 로그에 남긴다 — 화면에서 지우는 것과 기록을 없애는 것은 다르다.
    조회할 때마다 같은 줄이 쌓이지 않게, 내용이 바뀔 때만 적는다."""
    global _logged_errors
    current = frozenset(messages)
    if current and current != _logged_errors:
        for message in messages:
            _log.warning("일관성 판정 파일: %s", message)
    _logged_errors = current


# ---------------------------------------------------------------------------
# 사람 판정 게이트 — 일관성 지표를 순위에서 뺄지
# ---------------------------------------------------------------------------

DEMOTED, KEPT, NO_JUDGMENT, UNDETERMINABLE, IN_PROGRESS, NOT_APPLICABLE = (
    "demoted", "kept", "none", "undeterminable", "in_progress", "not_applicable")


def demotion(run_ids: list[str], load_result: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    """비교에 들어간 실행들의 대표 쌍 판정으로 `채점기 탓 / 판정 대상`을 세어 3분의 1을 넘으면 강등한다.

    - **세트 단위**다 — 모델마다 따로 판단하면 종합 점수의 분모가 모델마다 갈린다. 다만 세트는 "항상 20칸"이 아니라
      **지금 비교에 든 실행들의 칸**이다(강등이 그 비교에 적용되므로 근거도 그 비교에서 나온다).
    - 판정 기록이 없는 실행이 섞이면 있는 칸으로 내고 `partial`로 알린다. 하나도 없으면 강등도 통과도 아니다.
    - `판정 보류`가 절반을 넘으면 비율을 내지 않는다(`판정 불가`).
    - **가림이 풀리기 전에는 비율을 내지 않는다.** 판정 도중에 실행을 하나씩 골라 비율을 보면
      어느 칸이 어느 모델인지 드러난다.
    - 칸을 판정이 전부 끝난 실행만 센다 — 판정 중인 실행의 일부 칸으로 낸 비율은 그 실행의 값이 아니다."""
    subjects = []
    for rid in run_ids:
        result = load_result(rid) or {}
        if ((result.get("metrics") or {}).get("consistency") or {}).get("detail"):
            subjects.append(consistency_source({"id": rid, **result}))
    out: dict[str, Any] = {"runs": len(subjects), "judged_runs": 0, "scorer_fault": 0, "targets": 0, "held": 0,
                           "partial": False, "boundary": False, "watcher": False}
    if not subjects:
        return {**out, "status": NOT_APPLICABLE}
    data = load()
    if not data:
        return {**out, "status": NO_JUDGMENT}
    if not data.get("가림_해제_시각"):
        return {**out, "status": IN_PROGRESS}
    by_source: dict[str, list[dict[str, Any]]] = {}
    for p in data.get("pairs", []):
        if p.get("kind") == LOWEST:
            by_source.setdefault(p["a"]["run_id"], []).append(p)
    judged = [by_source[s] for s in subjects if by_source.get(s) and all(c.get("판정") for c in by_source[s])]
    if not judged:
        return {**out, "status": NO_JUDGMENT}
    cells = [c for group in judged for c in group]
    held = sum(1 for c in cells if c.get("판정") == HOLD)
    targets = len(cells) - held
    scorer = sum(1 for c in cells if c.get("판정") == "채점기 탓")
    out.update(judged_runs=len(judged), scorer_fault=scorer, targets=targets, held=held, partial=len(judged) < len(subjects))
    if held * 2 > len(cells) or targets == 0:
        return {**out, "status": UNDETERMINABLE}
    threshold = targets / 3
    out.update(boundary=abs(scorer - threshold) <= 1, status=DEMOTED if scorer > threshold else KEPT)
    return out


GOLDEN_EXCLUDED = "재읽기로 라벨 변경 — 골든 제외"


def _exclude_from_golden(data: dict[str, Any], pair: dict[str, Any], previous: str | None, now: str, reason: str) -> None:
    """골든 쌍의 판정이 바뀌어 라벨이 더 맞지 않으면 **골든에서 빼기만 하고 자리를 채우지 않는다.**
    줄어든 개수 자체가 결과다 — 다시 뽑아 넷을 복구하면 그 발견을 덮고, 넷을 못 채우면 켜지 않는 게이트를 우회한다.
    같은 씨앗이어도 후보가 바뀌면 다른 넷이 뽑히므로 재배정은 결과를 본 뒤 다시 뽑는 것과 같다.
    판정을 되돌려도 저절로 골든에 돌아오지 않는다 — 되돌리려면 명시적 재배정이다."""
    entry = {"id": pair["id"], "시각": now, "라벨": pair["label"], "이전_판정": previous, "이후_판정": pair["판정"],
             "이유": reason, "사유": GOLDEN_EXCLUDED}
    pair["골든_제외"] = entry
    pair["label"] = None
    if data.get("골든_배정") is not None:
        data["골든_배정"].setdefault("excluded", []).append(entry)


def golden_counts(pairs: list[dict[str, Any]]) -> dict[str, int]:
    """지금 파일에 고정된 골든 라벨 수 — 배정 뒤 제외된 쌍은 세지 않는다."""
    return {label: sum(1 for p in pairs if p.get("label") == label) for label in LABELS}


def save_verdict(token: str, verdict: str, reason: str | None = None) -> dict[str, Any]:
    """판정 하나를 저장하고 새 상태를 돌려준다. 토큰을 모르면(서버가 다시 떠 순서가 새로 발급됨) LookupError —
    화면은 목록을 다시 받아 이어간다. 저장된 판정은 파일에 있어 잃지 않는다.
    가림이 풀린 뒤 판정을 **바꾸려면 이유를 한 줄 적어야 한다** — 모델과 점수를 본 뒤의 수정이라, 이유가 남아야
    나중에 그 수정이 결과를 끌어갔는지 검토할 수 있다. 같은 판정을 다시 누르면 아무것도 바꾸지 않는다."""
    if verdict not in HUMAN_VERDICTS:
        raise ValueError(f"판정은 {'/'.join(HUMAN_VERDICTS)} 중 하나다 — {verdict!r}")
    with _lock:
        data = load()
        if not data:
            raise LookupError("판정 후보 파일이 없다")
        pair_id = _session["id_of"].get(token)
        pair = next((p for p in data.get("pairs", []) if p.get("id") == pair_id), None) if pair_id else None
        if pair is None:
            raise LookupError("알 수 없는 칸 토큰이다 — 목록을 다시 받아야 한다")
        if pair.get("판정") == HOLD:
            raise ValueError(f"{HOLD} 칸은 판정하지 않는다(길이 한도)")
        errors, _ = validate([pair])
        if errors:
            raise ValueError("; ".join(errors))
        if pair.get("판정") == verdict:
            return _state(data)
        reason = (reason or "").strip()
        if data.get("가림_해제_시각") and not reason:
            raise ValueError("가림이 풀린 뒤 판정을 바꾸려면 이유를 한 줄 적는다")
        now = datetime.now(UTC).isoformat()
        previous = pair.get("판정")
        if data.get("가림_해제_시각"):
            pair["가림_해제_후_수정"] = True
            pair.setdefault("수정_이력", []).append({"시각": now, "이전": previous, "이후": verdict, "이유": reason})
        pair["판정"] = verdict
        pair["판정_시각"] = now
        if pair.get("label") and derived_label(pair) != pair["label"]:
            _exclude_from_golden(data, pair, previous, now, reason)
        if data.get("이전_판본_가림_해제"):
            pair["이전_판본_해제됨"] = True
        targets = [p for p in data["pairs"] if p.get("kind") == LOWEST and p.get("판정") != HOLD]
        if not data.get("가림_해제_시각") and all(p.get("판정") for p in targets):
            data["가림_해제_시각"] = now
        write(data)
        return _state(data)
