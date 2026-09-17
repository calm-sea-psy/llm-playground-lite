"""저장된 응답으로 품질 지표를 다시 채점한다 (채점이 바뀌면 재채점으로 충분하다).

**측정은 불변, 판정은 갱신.** 모델이 낸 응답 원문은 건드리지 않고, 그 응답을
지금의 판정기(로직 + 채점 데이터)로 다시 읽어 점수와 `scorer_versions`만 고쳐
쓴다. 판정은 `quality_runner`의 것을 그대로 쓴다 — 여기서 판정을 한 벌 더
만들면 재채점 결과가 실행 결과와 갈라진다.

tool-calling은 대상이 아니다: 일부 문항(tc-104)은 채점 중에 실제 API를 불러
정답을 계산하므로 저장된 응답만으로 결정적으로 다시 채점할 수 없다.
"""

from datetime import UTC, datetime
from typing import Any

import quality_runner as qr
import quality_scoring as qs
import quality_testsets as qt

VARIANT_METRICS = ("closed_qa", "key_coverage", "injection_direct", "injection_indirect", "prompt_leak", "over_refusal")
GATED_BY_IF010 = ("injection_direct", "injection_indirect", "prompt_leak")
RESCORABLE_METRICS = (
    "instruction_following",
    "structured_output",
    "hallucination",
    *VARIANT_METRICS,
    "consistency",
    "long_context",
)


def _rescore_variant_detail(metric: str, d: dict[str, Any], detail: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """문항이 지금 세트에 없으면 다시 읽을 기준이 없으므로 원래 점수를 두고
    `unscorable` 표시만 남긴다 — 조용히 0점으로 바꾸지 않는다."""
    scorer = qr.variant_scorer(metric, d)
    items = {it["id"]: it for it in d["items"]}
    out = []
    for e in detail:
        e = dict(e)
        item = items.get(e["id"])
        if item is None:
            e["unscorable"] = "문항이 현재 세트에 없음"
        else:
            e["score"] = qr.score_response(scorer, item, e["response"], e.get("refused", False), e.get("call"))
            e.pop("unscorable", None)
        out.append(e)
    return out


def rescore_metric(metric: str, value: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """지표 dict 하나를 다시 채점한 새 dict. `metrics`는 같은 실행의 다른 지표(if-010 게이트용)."""
    if metric in ("instruction_following", "structured_output"):
        d = qt.load_quality_testset(metric)
        out = dict(value)
        for shot in [k for k in ("zero", "few") if k in value]:
            detail = _rescore_variant_detail(metric, d, value[shot]["detail"])
            out[shot] = {**value[shot], "score": qr.aggregate_variants(detail), "detail": detail}
        if metric == "instruction_following" and "zero" in out:
            if010 = [e["score"] for e in out["zero"]["detail"] if e["id"] == "if-010"]
            out["capability_control_passed"] = all(s >= 1.0 for s in if010) if if010 else None
        return out

    if metric == "hallucination":
        d = qt.load_quality_testset(metric)
        detail = _rescore_variant_detail(metric, d, value.get("detail") or [])
        control = _rescore_variant_detail(metric, d, value.get("capability_control_detail") or [])
        passed = all(e["score"] >= 1.0 for e in control) if control else None
        return {
            **value,
            "score": qr.aggregate_variants(detail) if passed is not False else None,
            "detail": detail,
            "capability_control_detail": control,
            "capability_control_passed": passed,
        }

    if metric in VARIANT_METRICS:
        d = qt.load_quality_testset(metric)
        detail = _rescore_variant_detail(metric, d, value.get("detail") or [])
        score = qr.aggregate_variants(detail)
        if metric in GATED_BY_IF010:
            if010_passed = (metrics.get("instruction_following") or {}).get("capability_control_passed", True)
            score = score if if010_passed else None
        return {**value, "score": score, "detail": detail}

    if metric == "consistency":
        detail = [
            {**e, "pairwise_similarity": qr.consistency_item_score(e["responses"])} for e in value.get("detail") or []
        ]
        return {**value, "score": qr.aggregate_consistency(detail), "detail": detail}

    if metric == "long_context":
        scenarios = {s["id"]: s for s in qt.load_quality_testset("long_context")["scenarios"]}
        detail = []
        for e in value.get("detail") or []:
            e = dict(e)
            scenario = scenarios.get(e["scenario"])
            if scenario is None:
                e["unscorable"] = "시나리오가 현재 세트에 없음"
            else:
                e["passed"] = qr.score_long_context_entry(scenario, e)
            detail.append(e)
        return {**value, **qr.aggregate_long_context(detail)}

    raise ValueError(f"재채점 대상이 아닌 지표: {metric}")


def rescore_result(entry: dict[str, Any]) -> list[str]:
    """결과 dict를 제자리에서 재채점한다. 판정 순서가 중요하다 — 인젝션·유출 지표의
    게이트가 지시 따르기의 if-010 결과를 보므로 그것부터 한다. 다시 채점한 지표 목록."""
    import test_runner  # 순환 참조 회피 — 표현 강건성 재계산만 빌린다

    metrics = entry.get("metrics") or {}
    # 지표 재실행 파일에는 지시 따르기가 없다 — 게이트는 부모에게서 물려받은 값을 본다
    gate = metrics
    if "instruction_following" not in metrics and "inherited" in entry:
        gate = {"instruction_following": {"capability_control_passed": entry["inherited"].get("capability_control_passed", True)}}
    done: list[str] = []
    at = datetime.now(UTC).isoformat()
    changes: dict[str, dict[str, Any]] = {}
    for metric in RESCORABLE_METRICS:
        if metric not in metrics or not isinstance(metrics[metric], dict):
            continue
        metrics[metric] = rescore_metric(metric, metrics[metric], gate if metric in GATED_BY_IF010 else metrics)
        before = (entry.get("scorer_versions") or {}).get(metric)
        after = qs.judge_versions_for(metric)
        entry.setdefault("scorer_versions", {})[metric] = after
        if before != after:
            changes[metric] = {"from": before, "to": after, "at": at}
        done.append(metric)
    if done:
        test_runner._compute_robustness(metrics, entry.get("run_type"))
        entry["rescored_at"] = at
        # 덮어쓰기 전 버전을 남긴다 — 남기지 않으면 다음 리포트가 `무엇이 바뀌었나`를 말할 수 없다(재채점 뒤에는
        # 기록과 코드가 일치해 버전 대조가 조용하다). 지표마다 **마지막으로 바뀐 때**만 둔다: 버전이 그대로인
        # 재채점은 그 기록을 지우지 않는다. 키가 **있으면** 이 기록을 남기기 시작한 뒤의 파일이라, 비어 있으면
        # `바뀐 것 없음`이고 키가 없으면 `기록 전이라 모른다`다.
        entry["scorer_version_changes"] = {**(entry.get("scorer_version_changes") or {}), **changes}
    return done
