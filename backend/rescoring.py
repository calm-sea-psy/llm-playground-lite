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


def _variant_details(metric: str, value: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """그 지표에서 문항 하나하나의 답이 들어 있는 detail 묶음들."""
    if metric in ("instruction_following", "structured_output"):
        return [value[shot]["detail"] for shot in ("zero", "few") if isinstance(value.get(shot), dict)]
    if metric == "hallucination":
        return [value.get("detail") or [], value.get("capability_control_detail") or []]
    if metric in VARIANT_METRICS:
        return [value.get("detail") or []]
    return []


def _changed_set_metrics(entry: dict[str, Any]) -> set[str]:
    """실행이 남긴 세트 지문과 지금 세트 파일이 다른 지표들. 지문 기록이 없으면 빈 집합 — 모르는 것이다."""
    import baseline as bl  # 순환 참조 회피 — 세트 지문 계산만 빌린다
    import bench_config as cfg

    out: set[str] = set()
    for rule, scopes in ((entry.get("fingerprints") or {}).get("rules") or {}).items():
        recorded = (scopes or {}).get(bl.QUALITY) or {}
        if not recorded:
            continue
        current = bl.compute_map(bl.QUALITY, int(rule))
        for metric, filename in cfg.QUALITY_TESTSET_FILES.items():
            key = f"set:{filename}"
            if key in recorded and recorded[key] != current.get(key):
                out.add(metric)
    return out


def stale_set_metrics(entry: dict[str, Any]) -> dict[str, str]:
    """실행이 **물은 질문**이 지금 세트에 없는 지표 → 까닭. 그 지표는 다시 채점하지 않는다.

    세트는 판을 쌓아 가므로(`testsets/versions/`), 지난 실행의 답을 지금 세트로 다시 읽으면 문항 id는 같은데
    묻는 것이 다른 칸이 섞인다 — 없는 사실을 묻던 자리에 다른 질문이 들어와 있어도 id만 맞으면 채점이 그대로
    돌아, 아무 경고 없이 점수가 달라진다(실제로 교체한 환각 문항 두 개가 그렇게 옛 실행의 점수를 올렸다).

    판단은 두 단계다. 먼저 실행이 남긴 세트 지문으로 **파일이 바뀌었는지** 보고, 바뀐 파일에서만 저장된 질문
    문장이 지금 문항의 변형 목록에 있는지 본다. 지문만 보면 만든 시각(`provenance`)만 달라져도 다시 채점할 수
    없게 되고, 질문만 보면 지문 기록이 없는 결과까지 걸린다. 채점 데이터(정답 표기·지어냄 정규식)만 바뀐 것은
    낡음이 아니다 — 그것을 지금 것으로 다시 읽는 일이 재채점이다."""
    changed = _changed_set_metrics(entry)
    if not changed:
        return {}
    out: dict[str, str] = {}
    metrics = entry.get("metrics") or {}
    for metric in changed:
        value = metrics.get(metric)
        if not isinstance(value, dict):
            continue
        details = _variant_details(metric, value)
        if not details:
            continue
        try:
            items = {it["id"]: it for it in qt.load_quality_testset(metric)["items"]}
        except (OSError, KeyError, ValueError):
            continue
        for e in [cell for detail in details for cell in detail]:
            item = items.get(e.get("id"))
            if item is None:  # 문항이 사라진 것은 `_rescore_variant_detail`이 칸마다 `unscorable`로 남긴다
                continue
            variant, known = e.get("variant"), item.get("variants") or []
            if known and variant is not None and variant not in known:
                out[metric] = f"{e['id']}에 물은 질문이 지금 세트에 없다 — 같은 id로 다른 것을 묻는 판이다"
                break
    return out


def _score_of(metric: Any) -> float | None:
    """그 지표의 대표 점수 — 없는 지표(속도·자원)나 점수를 안 내는 모양이면 None."""
    return metric.get("score") if isinstance(metric, dict) else None


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
    before_scores = {m: _score_of(metrics.get(m)) for m in RESCORABLE_METRICS if isinstance(metrics.get(m), dict)}
    changes: dict[str, dict[str, Any]] = {}
    stale = stale_set_metrics(entry)
    if stale:
        entry["rescore_skipped"] = stale
    for metric in RESCORABLE_METRICS:
        if metric not in metrics or not isinstance(metrics[metric], dict):
            continue
        if metric in stale:
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
        # 어떤 거절 표현 목록으로 채점한 값인가 — 목록이 바뀌면 같은 답의 점수가 달라진다.
        # 버전 숫자만으로는 무엇이 달라졌는지 알 수 없어 목록의 지문을 함께 적는다.
        entry["refusal_expressions_sha"] = qt.refusal_expressions_sha()
        # 덮어쓰기 전 점수를 남긴다 — 재채점은 파일을 제자리에서 고치므로, 남기지 않으면
        # `무엇이 얼마나 달라졌나`를 나중에 되짚을 길이 없다(응답 원문은 그대로라 다시 셀 수는 있다)
        history = entry.setdefault("rescore_history", [])
        history.append({"at": at, "refusal_expressions_sha": entry["refusal_expressions_sha"],
                        "scores": {m: before_scores.get(m) for m in done if m in before_scores},
                        "after": {m: _score_of(metrics.get(m)) for m in done}})
        # 덮어쓰기 전 버전을 남긴다 — 남기지 않으면 다음 리포트가 `무엇이 바뀌었나`를 말할 수 없다(재채점 뒤에는
        # 기록과 코드가 일치해 버전 대조가 조용하다). 지표마다 **마지막으로 바뀐 때**만 둔다: 버전이 그대로인
        # 재채점은 그 기록을 지우지 않는다. 키가 **있으면** 이 기록을 남기기 시작한 뒤의 파일이라, 비어 있으면
        # `바뀐 것 없음`이고 키가 없으면 `기록 전이라 모른다`다.
        entry["scorer_version_changes"] = {**(entry.get("scorer_version_changes") or {}), **changes}
    return done
