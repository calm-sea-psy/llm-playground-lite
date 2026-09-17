# -*- coding: utf-8 -*-
"""지표별 실패 사례 — 부록에 실을 칸을 **규칙으로** 고른다.

점수만 있고 근거가 없는 지표들에 근거를 붙이는 자리다. 일관성 상세와 달리 **문항 전문을 싣지 못한다** —
문항 자체가 공격 문구이고 `canary`·정답이 채점 열쇠라, 리포트가 세트의 새 배포 경로가 되면 안 된다.

고르는 규칙 둘:

1. **선정을 가른 것** — `실패한 응답이 있는 0점` 지표는 **전부 세되**(칸 수와 결과를 다 적는다)
   **발췌는 서로 다른 문항 최대 셋**. `능력 부재`는 0점으로 세지만 실을 응답이 없어 여기서 빠진다
   (모델이 시도해 실패한 것이 아니라 **호출하지 않은 것**이다 — 그 사실은 부르는 쪽이 한 줄로 적는다).
2. **모델별 대표 실패** — 규칙 1이 **이미 다룬 지표를 뺀 뒤**, 실패한 응답이 있는 지표(하위 행 포함) 중
   **실패 비율이 가장 높은** 것 하나, **서로 다른 두 문항**. 비율은 **정규화가 아니라 원래 값**으로 본다 —
   정규화는 `후보 중 최고 대비`라 모두가 못하는 지표에서 덜 못한 모델이 1.0이 되어 최저로 안 뽑힌다.
   겹침을 특례로 두지 않고 규칙 안에서 빼는 것은, 특례가 언젠가 다른 경우에서 새기 때문이다.

**발췌를 세는 단위는 칸이 아니라 문항이다.** 같은 문항의 다른 변형은 **같은 종류의 실패를 두 번**
보여 주므로 자리를 쓰지 않는다. 칸 수(`16칸 중 13칸`)는 그대로 칸으로 센다 — 그쪽은 규모를 말한다.

싣는 범위(무엇이 새면 무엇이 타는가로 가른다):

- **절대 안 싣는다** — `canary`·`fabrication_patterns`·도구 스키마·시스템 프롬프트 본문.
  하나가 새면 그 지표 전체가 탄다. **마스킹은 값에는 통하고 내용에는 안 통한다** — 모델이 제 말로 다시
  쓰면 찾을 수 없고, 유출은 본디 그 다시 쓰기라 **다시 쓰일 수 있는 것은 비게재 대상**이다.
- **싣되 상한 안에서** — 문항 id·변형 번호·분류 필드·도구 이름·모델이 보낸 인자. 그 문항 하나만 탄다.
- **모델이 낸 것** — 응답 발췌와 모델이 부른 호출. 다만 **값이 canary와 같으면 canary 규칙이 이긴다**
  (`if-010`의 기대 문자열이 실제로 canary와 같은 값이다 — 칸 이름이 아니라 값으로 판정한다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

MAX_EXCERPTS = 3  # 규칙 1 — 같은 결론이면 셋으로 충분하고, 다르면 셋 안에서 드러난다
REPRESENTATIVE_ITEMS = 2  # 규칙 2 — 모델마다 두 문항
EXCERPT_CHARS = 180  # 발췌 길이. 부록이 `요약 + 걸린 부분`이라 답 전체를 싣지 않는다
NO_EXCERPT = "발췌 없음 — 걸린 부분이 곧 세트의 비밀이라, 실으면 이 지표가 다음 측정부터 무효가 된다."

CANARY, TEXT, TOOL_CALL = "canary", "text", "tool_call"


@dataclass(frozen=True)
class Metric:
    """부록이 다룰 수 있는 지표 한 칸. `score`는 원래 값(1.0이 완전 통과), `entries`는 채점된 칸 전부다."""

    key: str
    label: str
    kind: str
    score: Callable[[dict[str, Any]], float | None]
    entries: Callable[[dict[str, Any]], list[dict[str, Any]]]
    testset: str | None = None  # 분류 필드·변형 번호·canary를 읽을 세트
    row: str = ""  # 측정값 표에서 이 지표가 앉은 행의 키 — 표시를 이름이 아니라 키로 찾는다


def _score(node: Any) -> float | None:
    if isinstance(node, dict):
        node = node.get("score")
    return node if isinstance(node, (int, float)) else None


def _cells(node: Any) -> list[dict[str, Any]]:
    return (node.get("detail") or []) if isinstance(node, dict) else []


def failed(entry: dict[str, Any]) -> bool:
    return isinstance(entry.get("score"), (int, float)) and entry["score"] < 1


def _showable(entry: dict[str, Any]) -> bool:
    """보여 줄 답이 있는 실패. 빈 응답은 실패로 세되 발췌 자리를 쓰지 않는다."""
    return failed(entry) and bool((entry.get("response") or "").strip())


def _simple(key: str, label: str, kind: str, testset: str | None = None, row: str = "") -> Metric:
    return Metric(key, label, kind, lambda m, k=key: _score(m.get(k)), lambda m, k=key: _cells(m.get(k)),
                  testset, row or key)


def _nested(parent: str, child: str, label: str, kind: str, row: str = "") -> Metric:
    return Metric(f"{parent}.{child}", label, kind,
                  lambda m, p=parent, c=child: _score((m.get(p) or {}).get(c)),
                  lambda m, p=parent, c=child: _cells((m.get(p) or {}).get(c)), None, row)


def _false_positive_cells(m: dict[str, Any]) -> list[dict[str, Any]]:
    """오탐 — 부르지 말았어야 할 곳에서 도구를 불렀다. 다른 지표와 같은 잣대로 세도록 `score`를 붙인다."""
    return [{**e, "score": 1 if e.get("trigger_correct") else 0}
            for e in (m.get("tool_calling") or {}).get("basic_detail") or []
            if e.get("category") == "false_positive"]


def _false_positive_score(m: dict[str, Any]) -> float | None:
    rate = (m.get("tool_calling") or {}).get("false_positive_rate")
    return None if rate is None else 1 - rate


# 부록이 다루는 지표. 하위 행(`ㄴ`)도 대상이다 — 상위 행의 평균이 0을 숨긴다(`인젝션 저항성 56%` = 직접 94 + 간접 19).
METRICS: tuple[Metric, ...] = (
    _simple("injection_direct", "ㄴ 인젝션 직접", CANARY, "injection_direct"),
    _simple("injection_indirect", "ㄴ 인젝션 간접", CANARY, "injection_indirect"),
    _simple("prompt_leak", "시스템 프롬프트 유출 저항", CANARY, "prompt_leak"),
    _simple("hallucination", "환각 저항", TEXT, "hallucination"),
    _simple("closed_qa", "폐쇄형 정답 정확도", TEXT, "closed_qa"),
    _simple("key_coverage", "핵심 정보 포함률", TEXT, "key_coverage"),
    _simple("structured_output", "구조적 출력 준수", TEXT, "structured_output"),
    _simple("over_refusal", "과잉 거절률 (정상 응답률)", TEXT, "over_refusal"),
    _nested("instruction_following", "zero", "지시 따르기 정확도 (zero)", TEXT, "instruction_following"),
    _nested("instruction_following", "few", "지시 따르기 정확도 (few)", TEXT, "instruction_following_few"),
    _nested("long_context", "recall", "긴 컨텍스트 기억력", TEXT, "long_context_recall"),
    _nested("long_context", "constraint", "다중 턴 제약 유지", TEXT, "long_context_constraint"),
    Metric("tool_calling.false_positive_rate", "ㄴ 1−오탐률", TOOL_CALL,
           _false_positive_score, _false_positive_cells, "tool_calling", "false_positive_rate"),
)


@dataclass(frozen=True)
class Case:
    """발췌가 있는 실패 한 칸. 요약 칸은 이미 있는 것으로만 채운다 — 문항 id·변형 번호·분류 필드."""

    item_id: str
    variant: int | None  # 색인이다 — 결과 파일의 `variant`는 문항 전문이라 그대로 쓰면 세트가 샌다
    tag: str | None
    excerpt: str

    @property
    def label(self) -> str:
        return f"{self.item_id}{f' 변형{self.variant}' if self.variant else ''}{f' · {self.tag}' if self.tag else ''}"


@dataclass(frozen=True)
class Group:
    """부록의 한 묶음 — 지표 하나에 대해 `몇 칸 중 몇 칸이 실패했나`와 실을 것들.

    **칸 목록은 짧은 쪽을 적는다**: 걸린 칸과 통과한 칸은 같은 것을 말하고, 여집합이 더 짧으면 더 짧게
    말한다(`16칸 중 13칸 실패`에 열세 줄을 적는 대신 통과한 셋을 적는다). 전부 실패면 통과한 칸이 0개라
    목록이 **저절로** 빈다 — `전부 실패면 적지 않는다`는 특례가 규칙에 흡수된다.
    **발췌가 없는 까닭은 묶음에 한 번** 적는다: 칸마다 글자 하나 다르지 않다."""

    metric: str
    row: str  # 측정값 표에서 표시를 달 행의 키
    rule: str  # `0점`(규칙 1) · `대표`(규칙 2)
    score: float
    scored: int
    failed: int
    cases: list[Case]
    listed: list[str]  # 짧은 쪽 칸 목록
    listed_kind: str  # `걸린 칸` · `통과한 칸`
    reason: str | None


def _items(testset: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """세트마다 문항 목록의 이름이 다르다 — 도구 세트는 `basic_items`·`advanced_items`로 나뉘어 있다."""
    merged: dict[str, dict[str, Any]] = {}
    for key in ("items", "basic_items", "advanced_items"):
        for item in (testset or {}).get(key) or []:
            merged[item["id"]] = item
    return merged


def _tag(item: dict[str, Any] | None) -> str | None:
    """세트에 이미 있고 노출이 안전한 분류 필드. `task_keywords`처럼 내용이 든 필드는 쓰지 않는다."""
    return (item or {}).get("type")


def _cut(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS].rstrip() + " …"


def has_canary(response: str, canary: str | None) -> bool:
    """이 칸의 응답이 열쇠를 담고 있는가. **고정 문자열이라 찾기만 하면 되고 채점기를 보지 않는다.**"""
    return bool(canary) and canary.lower() in (response or "").lower()


def tool_call_excerpt(entry: dict[str, Any]) -> str:
    """오탐의 증거는 **부른 호출**이다 — 응답 본문은 맞는 답일 수 있다(실측: `1년은 365일입니다.`).
    도구 이름과 모델이 보낸 인자는 그 문항만 태우므로 싣고, 픽스처가 정한 `result`는 증거에 기여하지
    않아 있음/없음으로만 적는다 — 노출을 따지기 전에 표면을 줄인다."""
    calls = entry.get("calls") or ([entry["call"]] if entry.get("call") else [])
    if not calls:
        return "도구를 부르지 않았다"
    parts = []
    for call in calls:
        args = call.get("arguments")
        shown = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items()) if isinstance(args, dict) else ""
        result = "결과 있음" if call.get("result") not in (None, "") else "결과 없음"
        parts.append(f"{call.get('name') or '이름 없음'}({shown}) · {result}")
    return _cut(" / ".join(parts))


def _variant_index(item: dict[str, Any] | None, variant: Any) -> int | None:
    """몇 번째 변형인가. **결과 파일의 `variant`는 문항 전문**이라 그대로 실으면 안 싣기로 한 것을 싣는다 —
    세트에서 자리만 찾아 번호로 바꾼다(찾지 못하면 적지 않는다)."""
    variants = (item or {}).get("variants") or []
    return variants.index(variant) + 1 if variant in variants else None


def _cell_label(entry: dict[str, Any], item: dict[str, Any] | None) -> str:
    index = _variant_index(item, entry.get("variant"))
    return f"{entry.get('id') or '?'}{f' 변형{index}' if index else ''}"


def _case(metric: Metric, entry: dict[str, Any], item: dict[str, Any] | None, canary: str | None) -> Case | None:
    """한 칸을 부록 줄로. **발췌를 싣는가는 지표가 아니라 그 칸이 어떻게 실패했는가로 정한다** —
    응답에 canary가 들어 있으면 걸린 부분이 곧 세트의 비밀이라 싣지 않는다(한 지표 안에 두 종류가
    섞이는 간접 인젝션도 특례 없이 갈린다)."""
    if has_canary(entry.get("response") or "", canary):
        return None
    excerpt = tool_call_excerpt(entry) if metric.kind == TOOL_CALL else _cut(entry.get("response") or "")
    return Case(entry.get("id") or "?", _variant_index(item, entry.get("variant")), _tag(item), excerpt)


def select(metrics: dict[str, Any]) -> tuple[list[Metric], Metric | None]:
    """(규칙 1이 고른 지표들, 규칙 2가 고른 지표). 실을 응답이 있는 것만 고른다 —
    `능력 부재`는 0점이지만 보여 줄 답이 없어 여기서 빠지고 호출하는 쪽이 한 줄로 적는다."""
    def showable(metric: Metric) -> bool:
        return any(_showable(e) for e in metric.entries(metrics))

    zero = [m for m in METRICS if m.score(metrics) == 0 and showable(m)]
    used = {m.key for m in zero}
    rest = [(1 - m.score(metrics), m) for m in METRICS
            if m.key not in used and m.score(metrics) is not None and m.score(metrics) < 1 and showable(m)]
    top = max(rest, key=lambda pair: pair[0], default=None)
    return zero, top[1] if top else None


def groups(metrics: dict[str, Any], load_testset: Callable[[str], dict[str, Any] | None]) -> list[Group]:
    """한 모델의 부록 묶음들. 규칙 1은 실패를 **전부 세되** 발췌는 상한까지, 규칙 2는 두 문항이다."""
    zero, representative = select(metrics)
    out: list[Group] = []
    for metric, rule, limit in ([(m, "0점", MAX_EXCERPTS) for m in zero]
                                + ([(representative, "대표", REPRESENTATIVE_ITEMS)] if representative else [])):
        testset = load_testset(metric.testset) if metric.testset else None
        items = _items(testset)
        canary = (testset or {}).get("canary")
        cells = metric.entries(metrics)
        failures = [e for e in cells if failed(e)]

        cases: list[Case] = []
        seen: set[str] = set()
        withheld = False
        for entry in failures:
            if not _showable(entry):
                continue
            case = _case(metric, entry, items.get(entry.get("id")), canary)
            if case is None:
                withheld = True
            elif len(cases) < limit and case.item_id not in seen:
                # 같은 문항의 다른 변형은 같은 종류를 두 번 보여 준다 — 자리를 쓰지 않는다
                cases.append(case)
                seen.add(case.item_id)

        hit = [_cell_label(e, items.get(e.get("id"))) for e in failures]
        passed = [_cell_label(e, items.get(e.get("id"))) for e in cells if not failed(e)]
        listed, kind = (hit, "걸린 칸") if len(hit) <= len(passed) else (passed, "통과한 칸")
        out.append(Group(metric.label, metric.row, rule, metric.score(metrics), len(cells), len(failures),
                         cases, listed, kind, NO_EXCERPT if withheld else None))
    return out
