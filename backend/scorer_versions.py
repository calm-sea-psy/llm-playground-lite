"""채점기 버전 대조 — 실행이 기록한 판정기 버전을 지금 판정기와 **지표(실행 항목)마다** 맞춰 본다.

상태는 다섯이다. 가르는 기준이 코드에 있어 사람이 판단할 일이 없다.

- `match` — 지금 버전과 같다. 아무것도 띄우지 않는다.
- `rescore` — 낡았고 **재채점 가능** 지표다. 저장된 응답을 다시 채점하면 풀린다.
- `remeasure` — 낡았고 **재채점 불가** 지표다(tool-calling 계열은 채점 중에 실제 API를 불러 정답을 계산한다).
  여기에 `재채점 필요`라고 쓰면 재채점으로 꺼지지 않는 경고가 된다.
- `ahead` — 결과 버전이 **지금 코드보다 높다**. 결과가 낡은 것이 아니라 코드가 되돌아간 것이라, 재채점하면
  최신 판정이 옛 판정기 결과로 덮인다 — 권하는 행동이 `rescore`와 정반대다.
- `unrecorded` — 버전 기록이 없다(그 기능 이전 실행). **낡았다고 찍지 않는다** — "믿으면 안 된다"가 아니라
  "믿을 수 있는지 모른다"라서 경고가 아니라 각주·세부로 간다.

판정기가 둘인 지표는 `ahead > 낡음 > 기록 없음 > 일치` 순으로 센다. 가장 비싼 실수(덮어쓰기)를 막는 쪽이 이긴다.
`ahead`가 이기면 진 쪽이 조치가 필요한 상태(낡음·기록 없음)일 때 `also`로 남긴다 — 코드를 되돌린 뒤에도
그쪽은 따로 풀어야 한다. 낡음이 기록 없음을 이길 때는 한 번의 재채점이 둘을 함께 풀어 남기지 않는다.

점수를 내지 않은 항목(능력 부재·실행 실패·값 없음)은 대조하지 않는다 — 채점기가 읽은 값이 없다.
한국어 순도 게이트도 대조하지 않는다 — 저장값이 아니라 읽을 때마다 지금 규칙으로 다시 계산된다.
"""

from collections.abc import Iterable
from typing import Any

import quality_scoring as qs
import rescoring
import tool_calling_runner

MATCH, RESCORE, REMEASURE, AHEAD, UNRECORDED = "match", "rescore", "remeasure", "ahead", "unrecorded"
STALE = "stale"  # `also`에만 쓴다 — 재채점 가능 여부와 무관하게 "낡은 판정기가 있다"

_UNSCORED_OUTCOMES = {"incapable", "failed", "confirmed_failure"}


def current(item_id: str) -> dict[str, int] | None:
    """그 항목의 점수를 지금 내는 판정기와 버전. 판정기가 없는 항목(속도·자원)은 None."""
    if item_id in qs.METRIC_JUDGES:
        return qs.judge_versions_for(item_id)
    if item_id in tool_calling_runner.JUDGE_VERSIONS:
        return {item_id: tool_calling_runner.JUDGE_VERSIONS[item_id]}
    return None


def reason(judge: str, version: int) -> str | None:
    """그 판정기를 그 버전으로 올린 이유. v1이거나 판정기를 모르면 None."""
    reasons = {**qs.JUDGE_VERSION_REASONS, **tool_calling_runner.JUDGE_VERSION_REASONS}
    return reasons.get(judge, {}).get(version)


def code_version(judge: str) -> int | None:
    return {**qs.JUDGE_VERSIONS, **tool_calling_runner.JUDGE_VERSIONS}.get(judge)


def rescorable(item_id: str) -> bool:
    """저장된 응답만으로 다시 채점할 수 있는가 — `rescore`/`remeasure`와 기록 없음이 풀리는 길을 가른다."""
    return item_id in rescoring.RESCORABLE_METRICS


def states(recorded: dict[str, Any] | None, items: Iterable[dict[str, Any]],
           scored_metrics: Iterable[str]) -> dict[str, dict[str, Any]]:
    """항목 id → `{state, also, recorded, current}`. `recorded`는 결과의 `scorer_versions`(재실행을 합친 뷰면
    항목마다 그 값을 낸 실행의 기록), `items`는 `{id, outcome}`, `scored_metrics`는 값이 있는 지표 키다.
    `also`는 `ahead`일 때만 — 다른 판정기가 낡았으면 `stale`, 기록이 없으면 `unrecorded`(둘 다면 낡음)."""
    recorded = recorded or {}
    scored = set(scored_metrics)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item["id"]
        now = current(item_id)
        if now is None or item_id not in scored or item.get("outcome") in _UNSCORED_OUTCOMES:
            continue
        record = recorded.get(item_id) or {}
        ahead = any(judge in record and record[judge] > version for judge, version in now.items())
        stale = any(judge in record and record[judge] < version for judge, version in now.items())
        missing = any(judge not in record for judge in now)
        also = None
        if ahead:
            state = AHEAD
            also = STALE if stale else (UNRECORDED if missing else None)
        elif stale:
            state = RESCORE if rescorable(item_id) else REMEASURE
        elif missing:
            state = UNRECORDED
        else:
            state = MATCH
        out[item_id] = {"state": state, "also": also, "recorded": record or None, "current": now}
    return out


def result_states(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """저장된 결과(재실행을 합친 뷰 포함)를 그대로 대조한다."""
    metrics = result.get("metrics") or {}
    return states(result.get("scorer_versions"), result.get("items") or [],
                  [k for k, v in metrics.items() if v is not None])


def rescore_blockers(result: dict[str, Any]) -> list[str]:
    """재채점하면 안 되는 지표 — 결과 버전이 지금 코드보다 높은 **재채점 가능** 지표. 다시 채점하면 앞선 판정이
    옛 판정기 결과로 덮인다. 재채점은 파일 하나의 재채점 가능 지표를 한꺼번에 다시 쓰므로 하나라도 있으면
    그 파일을 통째로 건너뛴다. 화면 버튼과 CLI가 이 판단 하나를 같이 쓴다."""
    return [k for k, e in result_states(result).items() if e["state"] == AHEAD and rescorable(k)]


def describe(item_id: str, entry: dict[str, Any]) -> str:
    """`tool_calling v1 → v2`처럼 판정기마다 기록과 지금을 적는다. 기록이 없으면 지금 버전만.
    결과가 더 높으면 화살표로 쓰지 않는다 — `v3 → v2`는 "올려야 할 곳"으로 읽힌다."""
    record = entry.get("recorded") or {}
    parts = []
    for judge, version in entry["current"].items():
        before = record.get(judge)
        if before is None:
            parts.append(f"{judge} 기록 없음 (지금 v{version})")
        elif before > version:
            parts.append(f"{judge} 결과 v{before} · 지금 코드 v{version}")
        elif before != version:
            parts.append(f"{judge} v{before} → v{version}")
    return ", ".join(parts)
