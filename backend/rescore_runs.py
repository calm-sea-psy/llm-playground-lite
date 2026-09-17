"""선택한 실행의 재채점 — 비교 화면의 버튼이 부른다. 모든 결과 파일을 한꺼번에 다시 채점하는 일괄 작업은
`maintenance.py rescore`로 남긴다.

**재채점 대상은 고른 실행의 파일 하나가 아니라 그 실행이 표시하는 값들의 출처 파일 집합이다.** 지표 재실행이
합쳐진 실행은 지표마다 값을 낸 파일이 달라서, 부모 파일만 다시 채점하면 재실행에서 온 지표는 옛 채점기 그대로 남는다.
실행하기 전에 **무엇이 바뀌는지**(파일마다 지표의 버전 변화)를 먼저 돌려준다.

- 측정 중인 실행은 뺀다 — 같은 파일을 동시에 쓰면 깨진다.
- 결과 버전이 지금 코드보다 **높은** 재채점 가능 지표가 있는 파일은 다시 채점하지 않는다 — 결과가 낡은 것이 아니라
  코드가 되돌아간 것이라, 다시 채점하면 최신 판정이 옛 판정기 결과로 덮인다.
- 바뀌는 버전이 없는 파일은 건드리지 않는다.

응답 원문은 바뀌지 않고 점수와 버전만 다시 쓴다(측정은 불변, 판정은 갱신) — 그래서 되돌리기가 필요 없다.

기준선도 화면에 보이는 값(참고선)이라 채점기 버전 상태를 함께 돌려주지만 **재채점 대상이 아니다** — 별도 필드
(`baseline`)에 싣고 `apply`는 그 필드를 읽지 않는다. 기준선 재채점은 `maintenance.py rescore`로만 한다.
화면이 판정기 버전 상수의 사본을 갖지 않도록 상태 판정은 여기서 한다.
"""

import json
import threading
from pathlib import Path
from typing import Any

import baseline
import rescoring
import scorer_versions as sv
import test_runner

# 두 요청이 같은 파일을 동시에 다시 쓰지 않게 한다
_LOCK = threading.Lock()


def _file_plan(data: dict[str, Any], shown: set[str], active_run_id: str | None) -> dict[str, Any]:
    """결과 파일 하나의 재채점 미리보기. `shown`은 선택한 실행이 이 파일에서 가져와 표시하는 항목 id들이다."""
    labels = {it["id"]: it.get("label") or it["id"] for it in data.get("items") or []}
    metrics = []
    for item_id, entry in sv.result_states(data).items():
        rescorable = sv.rescorable(item_id)
        if entry["state"] == sv.MATCH or (not rescorable and entry["state"] != sv.REMEASURE):
            continue  # 일치는 보여줄 변화가 없고, 재채점 불가 지표의 기록 없음은 재채점과 무관하다
        metrics.append({
            "id": item_id,
            "label": labels.get(item_id, item_id),
            "state": entry["state"],
            "change": sv.describe(item_id, entry),
            "rescorable": rescorable,
            "shown": item_id in shown,
        })
    blocked = None
    if data.get("status") == "running" or data.get("id") == active_run_id:
        blocked = "측정 중인 실행 — 끝난 뒤에 재채점한다"
    elif sv.rescore_blockers(data):
        blocked = "결과의 채점기 버전이 지금 코드보다 높은 지표가 있다 — 코드가 되돌아갔을 수 있어 재채점하지 않는다"
    changes = [m for m in metrics if m["rescorable"] and m["state"] in (sv.RESCORE, sv.UNRECORDED)]
    return {
        "run_id": data["id"],
        "kind": data.get("kind", "run"),
        "started_at": data.get("started_at"),
        "metrics": metrics,
        "blocked": blocked,
        "will_rescore": blocked is None and bool(changes),
    }


def _baseline_versions(document_lengths: set[str]) -> dict[str, Any] | None:
    """화면 참고선과 같은 기준선(`baseline.latest` — 고른 실행과 같은 문서 길이)의 채점기 버전 상태 — 일치가 아닌 지표만.
    대상 표시(`will_rescore`)가 없다: 버튼으로 풀 수 없는 경고다. 고른 실행의 문서 길이가 하나가 아니면 화면도 기준선을
    고르지 않으므로 None이다."""
    if len(document_lengths) != 1:
        return None
    entry = baseline.latest(next(iter(document_lengths)))
    if entry is None:
        return None
    return {
        "run_id": entry.get("id"),
        "model": entry.get("model"),
        # 서버가 실제로 읽은 것의 신원 — 화면은 자기가 들고 있는 기준선과 같은지만 본다(다르면 경고가 화면에 없는 것을 설명한다)
        "scorer_versions": entry.get("scorer_versions") or {},
        "metrics": _file_plan(entry, {it["id"] for it in entry.get("items") or []}, None)["metrics"],
    }


def plan(run_ids: list[str]) -> dict[str, Any]:
    """고른 실행마다 출처 파일과 그 파일의 재채점 미리보기, 그리고 재채점 대상이 아닌 기준선의 버전 상태.
    파일을 고치지 않는다."""
    active = test_runner.get_active_run_id()
    runs = []
    for run_id in run_ids:
        merged = test_runner.load_result(run_id)
        if merged is None:
            runs.append({"run_id": run_id, "missing": True, "files": []})
            continue
        shown_by_file: dict[str, set[str]] = {}
        for item_id, prov in (merged.get("provenance") or {}).items():
            shown_by_file.setdefault(prov["run_id"], set()).add(item_id)
        # 원래 실행 파일이 먼저, 그다음 값을 내준 재실행 파일 — 값이 전부 가려진 옛 재실행은 표시에 쓰이지 않아 넣지 않는다
        file_ids = list(dict.fromkeys([merged["id"], *shown_by_file]))
        files = []
        for file_id in file_ids:
            data = test_runner._read_result(file_id)
            if data is not None:
                files.append(_file_plan(data, shown_by_file.get(file_id, set()), active))
        runs.append({
            "run_id": run_id,
            "model": merged.get("model"),
            "started_at": merged.get("started_at"),
            # 서버가 읽은 합친 뷰의 신원 — 붙은 재실행과 값마다의 버전 기록. 화면이 캐시한 상세와 대조한다
            "rerun_ids": merged.get("rerun_ids") or [],
            "scorer_versions": merged.get("scorer_versions") or {},
            "files": files,
        })
    lengths = {baseline.document_length_of(test_runner.load_result(r["run_id"]) or {}) for r in runs if not r.get("missing")}
    return {"runs": runs, "baseline": _baseline_versions(lengths)}


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def apply(run_ids: list[str]) -> dict[str, Any]:
    """미리보기를 다시 계산해(화면이 본 뒤로 상태가 바뀌었을 수 있다) 재채점할 파일만 다시 채점해 쓴다.
    대상은 `runs`의 파일뿐이다 — `baseline` 필드는 읽지 않는다."""
    with _LOCK:
        before = plan(run_ids)
        rescored = []
        for run in before["runs"]:
            for file in run["files"]:
                if not file["will_rescore"]:
                    continue
                path = test_runner.RESULTS_DIR / f"{file['run_id']}.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                done = rescoring.rescore_result(data)
                if done:
                    _write(path, data)
                # 다시 채점한 지표 전부와, 그중 버전이 바뀐 지표 — 일치하던 지표도 함께 다시 채점되지만 바뀐 것은 아니다
                changed = [m["id"] for m in file["metrics"] if m["rescorable"] and m["state"] in (sv.RESCORE, sv.UNRECORDED)]
                rescored.append({"run_id": file["run_id"], "metrics": done, "changed": changed})
        return {"rescored": rescored, "plan": plan(run_ids)}
