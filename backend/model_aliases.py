"""모델 별칭 — 12자 내외 고정 규칙 + 겹침 해소.

리포트와 비교 노트(입력 표의 머리글, 인용 검증의 모델 표기 매핑)가 같이 쓴다. 규칙이 두 벌이면
노트가 쓴 이름과 리포트가 찍은 이름이 갈라진다.
"""

import re
from typing import Any


def short_time(value: str | None) -> str:
    return value.replace("T", " ")[:16] if value else "—"


# ---------------------------------------------------------------------------
# 별칭 (8번 "모델 별칭은 12자 내외로") — 고정 규칙 + 겹침 해소
# ---------------------------------------------------------------------------

_QUANT_RE = re.compile(r"^(?:q|iq|f|fp|bf)\d+(?:_.*)?$", re.IGNORECASE)
_DROPPED_WORDS = {"it", "instruct", "chat", "gguf"}


def alias_parts(name: str) -> tuple[list[str], list[str], list[str]]:
    """(남길 토큰, 뗀 앞쪽 토큰, 뗀 뒤쪽 토큰). 뗀다: 학습 방식 표기·양자화·포맷·배포처 접두.
    남긴다: 계열명·크기·숫자 버전. `:`와 `/`는 `-`로 치환한다."""
    prefix, _, base = name.rpartition("/")
    tokens = [t for t in base.replace(":", "-").split("-") if t]
    kept = [t for t in tokens if t.lower() not in _DROPPED_WORDS and not _QUANT_RE.match(t)]
    dropped_tail = [t for t in tokens if t not in kept]
    dropped_head = [prefix.replace("/", "-")] if prefix else []
    return kept or tokens, dropped_head, dropped_tail


def short_alias(name: str) -> str:
    """기본 별칭 — 리포트 간 대조는 이 값을 기준으로 한다(겹침 해소 단계는 같이 뽑은
    모델에 따라 달라지므로 고정이 아니다)."""
    return "-".join(alias_parts(name)[0])


def aliases(models: list[dict[str, Any]]) -> dict[str, str]:
    """모델 id → 별칭. 겹치면 전체 이름으로 되돌리지 않는다(12자 규칙과 정면 충돌) — **그 겹침
    그룹 안에서 서로 다른 토큰만** 뒤에서부터 되붙이고, 그래도 겹치면(같은 모델을 두 번 실행)
    실행 시각을 붙인다."""
    aliases = {m["id"]: short_alias(m["label"]) for m in models}
    parts = {m["id"]: alias_parts(m["label"]) for m in models}
    by_id = {m["id"]: m for m in models}

    def groups() -> list[list[str]]:
        seen: dict[str, list[str]] = {}
        for mid, alias in aliases.items():
            seen.setdefault(alias, []).append(mid)
        return [ids for ids in seen.values() if len(ids) > 1]

    for group in groups():
        tails = {mid: list(parts[mid][2]) for mid in group}
        heads = {mid: list(parts[mid][1]) for mid in group}
        # 뒤쪽 토큰부터 하나씩 — 그룹 안에서 값이 갈리는 자리만 붙인다
        depth = max((len(t) for t in tails.values()), default=0)
        for k in range(1, depth + 1):
            column = {mid: (tails[mid][-k] if len(tails[mid]) >= k else "") for mid in group}
            if len(set(column.values())) > 1:
                for mid in group:
                    if column[mid]:
                        aliases[mid] = f"{aliases[mid]}-{column[mid]}"
            if len({aliases[mid] for mid in group}) == len(group):
                break
        if len({aliases[mid] for mid in group}) < len(group):
            column = {mid: (heads[mid][0] if heads[mid] else "") for mid in group}
            if len(set(column.values())) > 1:
                for mid in group:
                    if column[mid]:
                        aliases[mid] = f"{column[mid]}-{aliases[mid]}"
    for group in groups():
        for mid in group:
            started = by_id[mid].get("started_at")
            aliases[mid] = f"{aliases[mid]} ({short_time(started)[5:]})" if started else f"{aliases[mid]} ({mid[:6]})"
    return aliases
