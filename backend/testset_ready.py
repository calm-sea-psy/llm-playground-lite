# -*- coding: utf-8 -*-
"""측정을 시작할 수 있는 상태인가 — **준비 단계를 밟았는가가 아니라 세트가 실제로 돌 수 있는가**로 판정한다.

공개본은 받은 그대로 공개 샘플 세트로 한 사이클을 돌 수 있어야 한다. 그래서 잣대는 "준비 화면을 지나왔는가"가
아니라 다음 셋이다 — 세트 파일이 다 있는가 · 세트가 가리키는 문서가 두 자리 다 있는가 · 지시문을 심은 문서가
조건을 갖췄는가. 하나라도 어긋나면 실행은 도중에 그 항목에서 실패하고, 몇 시간을 버린다.

막을 때는 **무엇이 어긋났고 무엇을 해야 하는지**를 함께 돌려준다 — 잠긴 화면만 보여 주면 사람이 할 수 있는
일이 없다.
"""

from typing import Any

import bench_config as cfg
import quality_testsets as qt
import testset_documents as td
import test_runner as tr
import testset_facts as tf


def _sets() -> tuple[list[str], list[str]]:
    """(없는 세트 파일, 문서를 못 찾는 세트) — 문서는 짧은 문서와 긴 문서가 **둘 다** 있어야 한다."""
    missing_files, missing_docs = [], []
    for name in qt.SET_FILES:
        if not qt.set_path(name).exists():
            missing_files.append(name)
    for metric in cfg.QUALITY_TESTSET_FILES:
        try:
            data = qt.load_quality_testset(metric)
        except (OSError, ValueError):
            continue
        for item in data.get("items") or []:
            doc = item.get("doc")
            if not doc:
                continue
            for length in (qt.DOCUMENTS_SHORT, qt.DOCUMENTS_LONG):
                try:
                    path = qt.TESTSETS_DIR / qt.doc_path(doc, length)
                except ValueError:
                    missing_docs.append(f"{metric}/{item.get('id')} — 문서 경로가 이상하다: {doc}")
                    continue
                if not path.exists():
                    missing_docs.append(f"{metric}/{item.get('id')} — {length}가 없다: {doc}")
    return missing_files, sorted(set(missing_docs))


def _runs() -> int:
    """돌려 둔 회차 수 — 세트를 만드는 중이어도 볼 것이 있으면 결과 화면을 막지 않는다."""
    folder = tr.RESULTS_DIR
    return len(list(folder.glob("*.json"))) if folder.exists() else 0


def readiness() -> dict[str, Any]:
    """측정을 시작할 수 있는가와, 막혔다면 무엇 때문인가. 단계마다 `done`과 `problems`를 돌려준다.

    canary는 원천에 적힌 값을 쓰고, 아직 원천이 없으면 **심은 문서에서 찾는다** — 문서만 올린 단계에서도 심은
    문서가 조건을 갖췄는지 볼 수 있어야 한다."""
    listed = td.listing()
    source = tf.load_source()
    canary = (source.get("canary") or "").strip()
    unknown = ""
    if not canary:
        canary, unknown = td.find_canary()
    injection = [d for d in listed["documents"] if d["editions"].get("인젝션 · 짧은 문서")]
    missing_files, missing_docs = _sets()

    documents = {
        "id": "documents",
        "label": "문서 — 두 문서 폴더가 짝을 이룬다",
        # 문서가 하나도 없는 것은 `걸린 것 없음`이 아니라 아직 시작하지 않은 것이다
        "problems": (["문서가 하나도 없다 — 문서를 올린다"] if not listed["documents"] else [])
        + [f"{name}: 두 문서 폴더 가운데 한쪽이 비었다" for name in listed["unpaired"]],
        "detail": f"문서 {len(listed['documents']) - len(injection)}개 · 인젝션 문서 {len(injection)}개",
    }
    injected = {
        "id": "injection",
        "label": "인젝션 문서 — 두 폴더에 canary가 있고 지시문 뒤에 내용이 남았다",
        "problems": [f"{p['name']}: {p['why']}" for p in (td.check_injection(canary) if canary else [])]
        + ([] if canary else ([f"canary를 찾지 못했다 — {unknown}"] if injection else [])),
        "detail": f"canary {canary or '없음'}",
    }
    sets = {
        "id": "sets",
        "label": "세트 — 파일이 다 있고, 가리키는 문서도 다 있다",
        "problems": [f"세트 파일이 없다: {name}" for name in missing_files] + missing_docs,
        "detail": f"세트 파일 {len(qt.SET_FILES) - len(missing_files)}/{len(qt.SET_FILES)}개",
    }
    steps = [documents, injected, sets]
    for step in steps:
        step["done"] = not step["problems"]
    # 세트를 새로 만드는 중이면 지금 놓인 세트는 **갈아 끼우기 전의 것**이다 — 그대로 측정하면 무엇을 잰
    # 것인지 알 수 없다. 다만 이미 돌려 둔 회차가 있으면 볼 것이 있으니 막지 않는다.
    making = bool(source.get("facts")) and source.get("_step") != "derive"
    return {"ready": all(step["done"] for step in steps), "steps": steps,
            "questions_written": bool(source.get("facts")),
            "making": making, "runs": _runs()}
