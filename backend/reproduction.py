# -*- coding: utf-8 -*-
"""같은 호출을 두 번 돌았을 때 같은 답이 나왔나 — 선정용 실행의 2회차를 1회차와 짝짓는다.

**점수에는 들어가지 않는다.** 점수·`n`·게이트·재채점은 1회차(`metrics`)만 읽고, 2회차 호출은 실행 결과의 따로 된 자리
(`second_round.calls`)에 둔다. 같은 자리에 넣으면 `n`이 두 배가 되고 재채점 한 번에 2회차가 점수에 섞인다.

판정은 둘을 함께 본다:
- **같은 답인가** — 글자 단위 완전 일치. 점수가 같아도 글자가 다르면 갈렸다고 센다.
- **캐시 조건이 같은가** — 두 바퀴의 `cached_tokens`가 같은 쌍만 재현을 판정한다. 실측에서 고정 샘플링이어도 캐시 상태가 답을
  정했다(같은 입력이 캐시 0이면 늘 A, 17이면 늘 B, 캐시를 만든 앞 호출이 달라도 개수가 같으면 같은 답). 캐시가 다른 쌍은
  버리지 않고 `캐시 섞임`으로 따로 센다 — 한 모델 안에서 두 무리의 갈림 비율을 견주면 캐시의 영향만 보인다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

RULE = ("같은 답인가는 글자 단위 완전 일치로 본다(점수가 같아도 글자가 다르면 갈림). 두 바퀴의 cached_tokens가 같은 쌍만 "
        "재현을 판정하고, 다른 쌍은 캐시 섞임으로 따로 센다. Tool-calling은 문항(변형) 단위로 도구 호출 열(이름·인자 순서)과 "
        "최종 답이 모두 같아야 재현이고, 캐시 조건은 문항의 첫 호출로 본다")

_SHOT_METRICS = ("instruction_following", "structured_output")


def _slim(entry: dict[str, Any]) -> dict[str, Any]:
    out = {"response": entry.get("response") or ""}
    if entry.get("call") is not None:
        out["call"] = entry["call"]
    if entry.get("failure"):
        out["failure"] = entry["failure"]
    return out


def _tool_record(entry: dict[str, Any]) -> dict[str, Any]:
    """도구 호출 문항 하나 — 최종 답과 호출 열(이름·인자, 순서대로). 캐시 조건은 **첫 호출**의 메타다: 뒤 호출은 앞 hop의 입력이
    캐시를 만들어 두 바퀴가 같아도 개수가 흔들리지 않는다. 첫 호출 메타가 없는 옛 기록은 판정하지 않는다."""
    out = {"response": entry.get("response") or "",
           "calls": [[c.get("name"), c.get("arguments")] for c in entry.get("calls") or []]}
    if entry.get("first_call") is not None:
        out["call"] = entry["first_call"]
    if entry.get("failure"):
        out["failure"] = entry["failure"]
    return out


def records(metric: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """지표 결과 하나에서 호출 단위 기록을 꺼낸다 — 짝을 지을 열쇠(`path`·`id`·`n`, 긴 컨텍스트는 `scenario`·`turn`)와 답·호출
    기록. 1회차와 2회차가 같은 함수로 꺼내므로 열쇠가 같은 순서로 붙는다. 긴 컨텍스트는 **압축 끔 경로의 모든 턴**이다
    (켬 경로는 요약 모델이 섞여 후보 모델의 재현을 못 본다)."""
    if not isinstance(result, dict):
        return []
    if metric == "long_context":
        return [{"path": "off", "scenario": t["scenario"], "turn": t["turn"], **_slim(t)}
                for t in result.get("turns") or [] if not t.get("compress")]
    if metric == "tool_calling":
        out = []
        for path, detail in (("basic", result.get("basic_detail") or []), ("advanced", result.get("advanced_detail") or [])):
            seen: Counter[str] = Counter()
            for entry in detail:
                seen[entry["id"]] += 1
                out.append({"path": path, "id": entry["id"], "n": seen[entry["id"]], **_tool_record(entry)})
        return out
    if metric in _SHOT_METRICS:
        sources = [(shot, (result.get(shot) or {}).get("detail") or []) for shot in ("zero", "few") if shot in result]
    elif metric == "hallucination":
        sources = [(None, result.get("detail") or []), ("capability_control", result.get("capability_control_detail") or [])]
    else:
        sources = [(None, result.get("detail") or [])]
    out = []
    for path, detail in sources:
        seen: Counter[str] = Counter()
        for entry in detail:
            seen[entry["id"]] += 1
            out.append({"path": path, "id": entry["id"], "n": seen[entry["id"]], **_slim(entry)})
    return out


def _key(record: dict[str, Any]) -> tuple:
    if "scenario" in record:
        return (record["path"], record["scenario"], record["turn"])
    return (record["path"], record["id"], record["n"])


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _counts() -> dict[str, int]:
    return {"compared": 0, "diverged": 0, "cache_mixed": 0, "cache_mixed_diverged": 0}


def compare(metrics: dict[str, Any], second_calls: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """2회차 기록마다 짝(`pair`)을 달고 요약을 낸다. 짝이 없거나(1회차에 없는 호출), 어느 한쪽이 모델 쪽 원인으로 답을 못 냈거나
    호출 기록이 없으면 판정하지 않는다(`not_compared`). 출력 토큰 수는 두 답 중 긴 쪽이다 — 갈림이 긴 답에 몰리는지 볼 재료다."""
    summary: dict[str, Any] = {"rule": RULE, "pairs": 0, "unpaired": 0, "not_compared": 0, **_counts(), "by_metric": {},
                               "output_tokens_median": {"same": None, "diverged": None}, "long_context": {}}
    same_tokens: list[int] = []
    diverged_tokens: list[int] = []
    for metric, second in second_calls.items():
        first_by_key = {_key(r): r for r in records(metric, metrics.get(metric) or {})}
        counts = _counts()
        for record in second:
            first = first_by_key.get(_key(record))
            if first is None:
                summary["unpaired"] += 1
                record["pair"] = {"paired": False}
                continue
            summary["pairs"] += 1
            if first.get("failure") or record.get("failure") or not first.get("call") or not record.get("call"):
                summary["not_compared"] += 1
                record["pair"] = {"paired": True, "compared": False}
                continue
            # 도구 호출 기록은 호출 열까지 같아야 같은 답이다(다른 지표는 둘 다 없어 같다)
            same = first["response"] == record["response"] and first.get("calls") == record.get("calls")
            first_cached, second_cached = first["call"].get("cached_tokens"), record["call"].get("cached_tokens")
            cache_equal = first_cached is not None and first_cached == second_cached
            tokens = max(first["call"].get("completion_tokens") or 0, record["call"].get("completion_tokens") or 0)
            record["pair"] = {"paired": True, "compared": True, "same": same, "cache_equal": cache_equal,
                              "first_cached_tokens": first_cached, "first_output_tokens": first["call"].get("completion_tokens")}
            if cache_equal:
                counts["compared"] += 1
                if same:
                    same_tokens.append(tokens)
                else:
                    counts["diverged"] += 1
                    diverged_tokens.append(tokens)
            else:
                counts["cache_mixed"] += 1
                counts["cache_mixed_diverged"] += 0 if same else 1
        summary["by_metric"][metric] = counts
        for name in counts:
            summary[name] += counts[name]
    summary["output_tokens_median"] = {"same": _median(same_tokens), "diverged": _median(diverged_tokens)}
    summary["long_context"] = _first_diverged_turns(second_calls.get("long_context") or [])
    return summary


def _first_diverged_turns(records_: list[dict[str, Any]]) -> dict[str, Any]:
    """시나리오마다 두 바퀴의 답이 처음 갈린 턴 — 첫 턴부터 갈리면 생성 자체, 뒤 턴에서만 갈리면 앞선 대화의 누적이다.
    그 턴의 캐시 조건이 같았는지를 함께 적는다(다르면 캐시로 설명될 수 있다)."""
    out: dict[str, Any] = {}
    for record in sorted(records_, key=lambda r: (r["scenario"], r["turn"])):
        pair = record.get("pair") or {}
        entry = out.setdefault(record["scenario"], {"turns_compared": 0, "first_diverged_turn": None, "cache_equal_at_that_turn": None})
        if not pair.get("compared"):
            continue
        entry["turns_compared"] += 1
        if entry["first_diverged_turn"] is None and not pair["same"]:
            entry["first_diverged_turn"] = record["turn"]
            entry["cache_equal_at_that_turn"] = pair["cache_equal"]
    return out
