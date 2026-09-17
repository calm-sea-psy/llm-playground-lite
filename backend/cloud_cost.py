# -*- coding: utf-8 -*-
"""클라우드 추정 비용 — 호출마다 남긴 토큰 수에 단가를 곱한다. **실제 사용 내역(OpenAI 대시보드)이 아니다.**

단가는 여기 한 곳에만 둔다. 가격은 바뀐다(2026-07-30에 Luna가 80% 인하됐다) — 그래서 값과 함께 **어느 페이지의 어떤 자리에서
언제 확인했는지**를 남긴다. 두 페이지가 다른 값을 적고 있어 출처를 가려 적는다:
- API 가격 문서(`developers.openai.com/api/docs/pricing`)의 Standard 표 행은 **인하된 값을 직접** 적는다 — 이 값을 쓴다.
- 발표 페이지(`openai.com/index/gpt-5-6/`)의 본문은 인하 전 값(입력 $1 / 출력 $6)이고 인하는 업데이트 주석에 따로 있다 —
  본문 표를 옮기면 5배 높은 값이 된다.

같은 표에 long context 단가가 따로 있는데 **어느 토큰 수부터 long인지는 페이지에 없다** — 과제용 호출은 짧아 short context로
셈한다(가정). 추론 토큰은 출력 요금으로 과금되고 출력 토큰 수에 이미 들어 있다(추론 가이드) — 따로 더하지 않는다.
"""

from __future__ import annotations

from typing import Any

# 100만 토큰당 USD — Standard · short context
PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "output": 1.20},
}
PRICE_SOURCE = {
    "url": "https://developers.openai.com/api/docs/pricing",
    "where": "Standard 표의 행(short context) — 발표 페이지 본문 표가 아니다",
    "checked": "2026-09-16",
    "assumption": "long context 경계 토큰 수가 페이지에 없어 short context 단가로 셈했다",
}


def _calls(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("turns"), list):
            # 긴 컨텍스트 — 모든 턴의 호출이 `turns`에 있고 `detail`은 그중 채점하는 턴이다. 둘 다 세면 같은 호출을 두 번 센다
            return _calls(value["turns"])
        own = [value["call"]] if isinstance(value.get("call"), dict) else []
        return own + [c for k, v in value.items() if k != "call" for c in _calls(v)]
    if isinstance(value, list):
        return [c for v in value for c in _calls(v)]
    return []


def estimate(model: str, metrics: dict[str, Any], item_ids) -> dict[str, Any]:
    """문항 호출의 추정 비용. 캐시 적용분을 모르는 호출은 입력 전부를 캐시 없는 단가로 센다 — 그 호출이 있으면 **실제보다 높게
    나온다**는 방향을 함께 적는다. 사전 점검 호출은 입력 토큰 기록이 없어 세지 않는다(그만큼 실제보다 낮다) — 그 사실도 적는다."""
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        return {"not_estimated": f"단가 기록이 없는 모델이다: {model}"}
    calls = [c for item_id in item_ids for c in _calls(metrics.get(item_id)) if c.get("prompt_tokens") is not None]
    input_tokens = sum(c["prompt_tokens"] for c in calls)
    cached = sum(c.get("cached_tokens") or 0 for c in calls)
    output_tokens = sum(c.get("completion_tokens") or 0 for c in calls)
    unknown_cache = sum(1 for c in calls if c.get("cached_tokens") is None)
    usd = ((input_tokens - cached) * prices["input"] + cached * prices["cached_input"] + output_tokens * prices["output"]) / 1_000_000
    notes = ["추정 비용이다 — 실제 사용 내역(OpenAI 대시보드)과 구분한다",
             "사전 점검 호출은 입력 토큰 기록이 없어 세지 않았다"]
    if unknown_cache:
        notes.append(f"캐시 적용분을 모르는 호출 {unknown_cache}건은 입력 전부를 캐시 없는 단가로 셌다 — 실제보다 높게 나온다")
    return {
        "usd": usd,
        "per_call_usd": usd / len(calls) if calls else None,
        "calls": len(calls),
        "input_tokens": input_tokens,
        "cached_tokens": cached,
        "output_tokens": output_tokens,  # 추론 토큰 포함
        "prices_per_mtok": prices,
        "source": PRICE_SOURCE,
        "notes": notes,
    }
