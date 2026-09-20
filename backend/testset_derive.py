# -*- coding: utf-8 -*-
"""한 주제의 원천 파일에서 **문서를 읽는 세트 넷과 긴 컨텍스트 세트**를 펼친다.

사람이 한 파일(원천)에 주제·문서·핵심 사실을 적으면, 여기서 폐쇄형(문서 문항)·핵심 정보·환각·간접 인젝션·
긴 컨텍스트의 **모양**을 만들고 **검사**한다. 문항 문구·정답 표기·없는 사실·정규식은 원천에 있는 것을 그대로
옮길 뿐 지어내지 않는다 — 그 내용이 측정의 바닥이라 사람이 쓰고 사람이 고친다.

**재는 대상이 문항을 만들면 안 된다.** 후보·기준선 모델이 만든 문항은 자기가 쓴 문제를 자기가 푸는 것이라
측정이 무효다. 원천의 `generated_by`에 무엇이 만들었는지 적고, 펼친 세트에 그대로 남긴다.

**검사에 오류가 없을 때만** 새 판을 낸다(`publish`) — `testsets/versions/<만든 때>/`에 쌓이고, 실행은 늘
**가장 최근 판**을 읽는다. 옛 판은 지우지 않고 남는다: 지난 회차가 무엇으로 잰 것이었는지 되짚을 수 있어야 한다.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import quality_testsets as qt



ERROR, WARN = "error", "warn"


def _check(level: str, where: str, message: str) -> dict[str, str]:
    return {"level": level, "where": where, "message": message}


def _variants(node: dict[str, Any], key: str, where: str, checks: list[dict[str, str]]) -> list[str]:
    """문항 하나의 변형 둘 — **같은 뜻, 다른 말투**. 하나만 두면 표현 강건성이 흔들림 없는 것처럼 나온다."""
    variants = [v.strip() for v in (node.get(key) or []) if isinstance(v, str) and v.strip()]
    if len(variants) != 2:
        checks.append(_check(ERROR, where, f"`{key}`는 변형 둘이어야 한다 — 지금 {len(variants)}개"))
    elif variants[0] == variants[1]:
        checks.append(_check(ERROR, where, "변형 둘이 같은 문장이다"))
    return variants


def _forms(node: dict[str, Any], key: str, where: str, checks: list[dict[str, str]]) -> list[str]:
    """허용 표기 — 하나만 두면 맞게 답하고도 틀린 것이 된다. 한 글자 표기는 거의 모든 답에 걸린다."""
    forms = [f for f in (node.get(key) or []) if isinstance(f, str) and f.strip()]
    if not forms:
        checks.append(_check(ERROR, where, f"`{key}`가 비어 있다"))
    if any(len(f.strip()) < 2 for f in forms):
        checks.append(_check(WARN, where, "한 글자짜리 표기가 있다 — 거의 모든 답에 걸린다"))
    return forms


def _documents(source: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """문서는 두 자리(짧은·긴)이 같은 이름으로 있어야 한다 — 긴 문서가 없으면 선정용 실행에서 그 항목이 실패한다."""
    out: dict[str, dict[str, Any]] = {}
    for doc in source.get("documents") or []:
        path = doc.get("path")
        if not path:
            checks.append(_check(ERROR, "documents", "`path`가 없는 문서가 있다"))
            continue
        out[path] = doc
        for label, rel in (("짧은 문서", path), ("긴 문서", _long_path(path, checks))):
            if rel and not (qt.TESTSETS_DIR / rel).exists():
                checks.append(_check(ERROR, path, f"{label} 파일이 없다: {rel}"))
        injected = doc.get("injected") or []
        for one in [injected] if isinstance(injected, str) else injected:
            if not (qt.TESTSETS_DIR / one).exists():
                checks.append(_check(ERROR, path, f"지시문을 심은 문서가 없다: {one}"))
    if not out:
        checks.append(_check(ERROR, "documents", "문서가 하나도 없다"))
    return out


def _long_path(rel: str, checks: list[dict[str, str]]) -> str | None:
    try:
        return qt.doc_path(rel, qt.DOCUMENTS_LONG)
    except ValueError as exc:
        checks.append(_check(ERROR, rel, str(exc)))
        return None


def _doc_text(rel: str) -> str:
    """짧은 문서와 긴 문서를 합쳐 읽는다 — 어느 쪽에 든 사실이든 `문서에 있다`로 본다."""
    texts = []
    for path in (rel, qt.doc_path(rel, qt.DOCUMENTS_LONG)):
        file = qt.TESTSETS_DIR / path
        if file.exists():
            texts.append(file.read_text(encoding="utf-8"))
    return "\n".join(texts)


def _closed_qa(facts: list[dict[str, Any]], checks: list[dict[str, str]], keep: list[dict[str, Any]]) -> dict[str, Any]:
    """폐쇄형 — 핵심 사실 하나에 문항 하나. 문서를 읽지 않는 기존 문항은 그대로 두고 문서 문항만 갈아 끼운다."""
    items = list(keep)
    for i, fact in enumerate(facts, 1):
        where = f"facts[{fact.get('id', i)}]"
        items.append({
            "id": f"cq-d{i:03d}",
            "doc": fact["doc"],
            "variants": _variants(fact, "ask", where, checks),
            "allowed_forms": _forms(fact, "answer_forms", where, checks),
        })
    return {"items": items}


def _key_coverage(documents: dict[str, dict[str, Any]], by_doc: dict[str, list[dict[str, Any]]],
                  checks: list[dict[str, str]]) -> dict[str, Any]:
    """핵심 정보 — 문서마다 요약을 시키고, 그 문서의 사실이 답에 들어갔는지 본다. **항목 수를 요약 길이에 맞춘다** —
    항목을 늘리면 난도가 아니라 전원의 점수가 함께 내려간다."""
    items = []
    for i, (path, doc) in enumerate(documents.items(), 1):
        facts = by_doc.get(path) or []
        # 한 줄도 쓰지 않은 문서는 요약을 시키지 않기로 한 것이다 — 뼈대가 만들어 둔 빈 줄까지 문항으로 세지 않는다
        # (지시문을 심은 문서는 다르다: 간접 인젝션이 이 질문을 `원래 시킨 일`로 쓰므로 비우면 그쪽에서 걸린다)
        if not [v for v in (doc.get("summary_ask") or []) if isinstance(v, str) and v.strip()]:
            continue
        where = f"documents[{path}]"
        points = [{"label": f.get("label") or f["id"], "forms": f.get("answer_forms") or []} for f in facts]
        if len(points) > 5:
            checks.append(_check(WARN, where, f"요약 항목이 {len(points)}개다 — 요약 길이 안에 들어갈 수 있는 수인지 본다"))
        if not points:
            checks.append(_check(ERROR, where, "요약을 시키는데 그 문서의 사실이 하나도 없다"))
        items.append({
            "id": f"kc-d{i:03d}",
            "doc": path,
            "variants": _variants(doc, "summary_ask", where, checks),
            "key_points": points,
        })
    return {"items": items}


def _hallucination(documents: dict[str, dict[str, Any]], by_doc: dict[str, list[dict[str, Any]]],
                   checks: list[dict[str, str]]) -> dict[str, Any]:
    """환각 — 문서에 **없는** 사실을 묻는 문항과, 문서에 **있는** 사실을 묻는 능력 대조군.
    대조군이 없으면 `전부 거절하는 모델`이 만점을 받는다. 대조군은 문서마다 하나씩 둔다."""
    items: list[dict[str, Any]] = []
    absent_no = 0
    for path in documents:
        for fact in by_doc.get(path) or []:
            absent = fact.get("absent") or {}
            patterns = [p for p in (absent.get("fabrication_patterns") or []) if isinstance(p, str) and p.strip()]
            asked = [v for v in (absent.get("ask") or []) if isinstance(v, str) and v.strip()]
            # 아무것도 쓰지 않은 칸은 만들다 만 문항이 아니라 **만들지 않기로 한 문항**이다 — 세지 않고 지나간다.
            # 한쪽만 쓴 것은 다르다: 질문만 있으면 지어냈는지 가릴 수 없고, 정규식만 있으면 물을 것이 없다
            if not patterns and not asked:
                continue
            absent_no += 1
            where = f"facts[{fact.get('id')}].absent"
            if not patterns:
                checks.append(_check(ERROR, where, "`fabrication_patterns`가 비어 있다 — 지어낸 값을 잡을 수 없다"))
            for pattern in patterns:
                try:
                    found = re.compile(pattern)
                except re.error as exc:
                    checks.append(_check(ERROR, where, f"정규식이 아니다: {pattern} ({exc})"))
                    continue
                # 문서에서도 걸리는 패턴은 위험하다 — 모델이 문서를 인용한 올바른 답도 `지어냈다`로 세어
                # 거절을 오답으로 만든다. 맥락을 붙여 좁힌다 — 값 앞에 그 값을 가리키는 말을 두는 식이다
                if found.search(_doc_text(path)):
                    checks.append(_check(WARN, where, f"`{pattern}`이 문서에서도 걸린다 — 문서를 인용한 올바른 "
                                                      "거절이 지어낸 것으로 셀 수 있다. 맥락을 붙여 좁힌다"))
            items.append({
                "id": f"hl-d{absent_no:03d}",
                "doc": path,
                "type": "unanswerable",
                "variants": _variants(absent, "ask", where, checks),
                "scoring": {"type": "refusal_expected", "fabrication_patterns": patterns},
            })
    for i, path in enumerate(documents, 1):
        facts = by_doc.get(path) or []
        if not facts:
            continue
        control = facts[0]
        items.append({
            "id": f"hl-c{i:03d}",
            "doc": path,
            "type": "capability_control",
            "variants": control.get("ask") or [],
            "scoring": {"type": "keyword_match", "allowed_forms": control.get("answer_forms") or []},
        })
    return {"items": items}


def _injection_indirect(source: dict[str, Any], documents: dict[str, dict[str, Any]],
                        checks: list[dict[str, str]]) -> dict[str, Any]:
    """간접 인젝션 — 문서 안에 심은 지시를 따르지 않고(canary 없음) **원래 시킨 일을 실제로 했을 때**(task_keywords)만
    통과다. 하나만 보면 `아무 말도 안 한 모델`이 통과한다."""
    canary = (source.get("canary") or "").strip()
    if not canary:
        checks.append(_check(ERROR, "canary", "canary가 없다 — 지시를 따랐는지 가릴 수 없다"))
    items = []
    for path, doc in documents.items():
        # 한 문서에 심은 문서를 여럿 걸 수 있다 — 공격 문구가 문항마다 달라야 재는 것이 공격 하나로 좁아지지 않는다
        injected = doc.get("injected") or []
        for one in [injected] if isinstance(injected, str) else injected:
            where = f"documents[{path}].injected"
            if canary and canary not in _read(one):
                checks.append(_check(ERROR, where, f"심은 문서에 canary가 없다: {one}"))
            keywords = [k for k in (doc.get("task_keywords") or []) if isinstance(k, str) and k.strip()]
            if not keywords:
                checks.append(_check(ERROR, where, "`task_keywords`가 비어 있다 — 늘 실패한다"))
            items.append({
                "id": f"ii-d{len(items) + 1:03d}",
                "doc": one,
                "variants": _variants(doc, "summary_ask", where, checks),
                "task_keywords": keywords,
            })
    return {"canary": canary, "items": items}


def _read(rel: str) -> str:
    file = qt.TESTSETS_DIR / rel
    return file.read_text(encoding="utf-8") if file.exists() else ""


def _long_context(source: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any] | None:
    """긴 컨텍스트 — 시나리오는 모양이 달라 원천의 것을 그대로 옮기고, 가리키는 턴이 있는지만 본다."""
    scenarios = source.get("scenarios")
    if not scenarios:
        return None
    for scenario in scenarios:
        where = f"scenarios[{scenario.get('id', '?')}]"
        turns = scenario.get("turns") or []
        for check in scenario.get("recall_checks") or []:
            if not 1 <= (check.get("turn") or 0) <= len(turns):
                checks.append(_check(ERROR, where, f"없는 턴을 가리킨다: {check.get('turn')} (턴 {len(turns)}개)"))
        for turn in (scenario.get("constraint") or {}).get("checked_turns") or []:
            if not 1 <= turn <= len(turns):
                checks.append(_check(ERROR, where, f"제약이 없는 턴을 가리킨다: {turn}"))
    return {"scenarios": scenarios}


def _provenance(source: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, str]:
    """**무엇이 이 문항을 만들었나.** 재는 대상(후보·기준선)이 만든 문항은 측정이 무효다 — 여기 적어 두고 사람이 본다."""
    by = (source.get("generated_by") or "").strip()
    if not by:
        checks.append(_check(WARN, "generated_by", "무엇이 만들었는지 적혀 있지 않다 — 재는 대상이 만든 문항은 측정이 무효다"))
    digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"generated_by": by or "기록 없음", "derived_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source_sha256": digest[:12], "topic": source.get("topic") or ""}


def _kept_closed_qa() -> list[dict[str, Any]]:
    """문서를 읽지 않는 폐쇄형 문항 — 주제와 무관한 능력이라 이번 교체에서 그대로 둔다."""
    try:
        live = qt.load_quality_testset("closed_qa")
    except (OSError, ValueError):
        return []
    return [item for item in live.get("items") or [] if not item.get("doc")]


def derive(source: dict[str, Any], *, keep_closed_qa: bool = True) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """원천 하나 → 세트 넷(+긴 컨텍스트)과 검사 목록. **파일을 쓰지 않는다.**"""
    checks: list[dict[str, str]] = []
    provenance = _provenance(source, checks)
    documents = _documents(source, checks)

    facts, by_doc, seen = [], {}, set()
    for i, fact in enumerate(source.get("facts") or [], 1):
        where = f"facts[{fact.get('id', i)}]"
        if fact.get("id") in seen:
            checks.append(_check(ERROR, where, "id가 겹친다 — 채점이 두 문항을 조용히 합친다"))
        seen.add(fact.get("id"))
        if fact.get("doc") not in documents:
            checks.append(_check(ERROR, where, f"문서 목록에 없는 문서를 가리킨다: {fact.get('doc')}"))
            continue
        facts.append(fact)
        by_doc.setdefault(fact["doc"], []).append(fact)

    sets = {
        "closed_qa.json": _closed_qa(facts, checks, _kept_closed_qa() if keep_closed_qa else []),
        "key_coverage.json": _key_coverage(documents, by_doc, checks),
        "hallucination.json": _hallucination(documents, by_doc, checks),
        "injection_indirect.json": _injection_indirect(source, documents, checks),
    }
    if long_context := _long_context(source, checks):
        sets["long_context.json"] = long_context
    for name, data in sets.items():
        data["provenance"] = provenance
        ids = [item["id"] for item in data.get("items") or []]
        if len(ids) != len(set(ids)):
            checks.append(_check(ERROR, name, "문항 id가 겹친다"))
    return sets, checks


def counts(sets: dict[str, dict[str, Any]]) -> dict[str, int]:
    """세트마다 **채점 칸**이 몇 개인가 — 한 칸의 무게(1/n)를 세트를 쓰기 전에 본다."""
    out = {}
    for name, data in sets.items():
        items = data.get("items") or []
        if name == "hallucination.json":  # 능력 대조군은 n에 들어가지 않는다
            items = [i for i in items if i.get("type") != "capability_control"]
        cells = sum(len(i.get("variants") or []) for i in items)
        out[name] = cells or len(data.get("scenarios") or [])
    return out


def publish(sets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """펼친 세트를 **새 판으로 낸다** — `versions/<만든 때>/`에 쓰고, 실행은 늘 가장 최근 판을 읽는다.

    옛 판을 덮지 않는 까닭은 되짚기다. 세트를 갈아 낀 뒤의 값과 그 전 값은 나란히 읽을 수 없는데, 무엇으로
    잰 것이었는지조차 사라지면 지난 회차의 숫자가 무엇을 뜻하는지 알 길이 없다.

    부르는 쪽이 **오류 없는 검사 결과**를 먼저 확인한다 — 이 함수는 쓰는 일만 한다."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # **판은 늘 세트 뿌리에 쌓는다 — 샘플 폴더가 아니라.** 아직 세트가 하나도 없는 기계에서는 읽는 자리가
    # 공개 샘플이지만, 지금 내는 것은 그 사람의 세트다. 샘플 폴더에 쓰면 저장소가 따라가는 자리에 정답이
    # 들어가고(공개본에서 sample/은 git이 따라간다), `_testsets_dir`도 그것을 실제 세트로 보지 않는다
    versions_dir = qt.TESTSETS_ROOT / qt.VERSIONS
    folder = versions_dir / stamp
    # 같은 초에 두 번 내면 이름이 겹친다 — 뒤엣것이 앞엣것을 덮으면 판이 하나로 뭉개진다
    for n in range(2, 100):
        if not folder.exists():
            break
        folder = versions_dir / f"{stamp}-{n}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in sets.items():
        (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8", newline="")
    return {"version": folder.name, "written": sorted(sets), "versions": len(qt.versions(qt.TESTSETS_ROOT))}
