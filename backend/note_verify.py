"""비교 노트 인용 검증.

노트가 인용한 수치를 **노트가 받은 표**와 대조한다. 숫자만 보면 뒤집어 붙인 문장을 못 잡는다
(100%도 75%도 표 어딘가에 있는 값이다) — 그래서 문장에서 모델·지표·값을 함께 뽑아
`(모델, 지표, 값)`이 표에 있는지 본다. 실제로 실린 오류가 기준이다.

1. 표와 다른 값 — 삼중쌍 대조로 잡고(`표와 불일치`), 원인을 가른다: 다른 모델의 값(`대상 혼동`),
   다른 지표의 값(`단위·지표 착오`), 어디에도 없는 값(`표에 없는 값`).
2. 값은 둘 다 맞는데 대소 서술이 반대("100%인 A가 30%인 B보다 더 낮다") — 방향 검사로 잡는다(`방향 불일치`).
   최상급("A의 메모리 사용량이 가장 낮다")은 노트가 받은 표의 후보 전원과 견준다.
3. 차이·감소율 같은 수치 주장 — 표의 두 값으로 계산해 대조한다.

불릿의 표식은 **목록**이다 — 한 불릿에 여럿이 붙을 수 있고 우선순위를 매기지 않는다.
`확인됨`·`대조 불가`는 다른 표식이 하나도 없을 때만 붙는다.

**묶지 못한 인용은 "환각"이 아니라 `대조 불가`다.** 모르는 것을 틀린 것으로 표시하지 않는다(지문의
`기록 없음`과 같은 원칙). 실패한 불릿은 빼지 않고 표식을 달아 그대로 싣는다 — 빼면 왜 사라졌는지
알 수 없고 노트 길이가 달라져 모델 간 비교도 흔들린다.

규칙 기반 휴리스틱이다. 문장을 이해하는 것이 아니라 가까운 표기를 묶는 것이라, 확신이 없는 경우는
늘 `대조 불가` 쪽으로 기운다.
"""

import re
from typing import Any

# 2 — 실제 생성 노트(09-14, 표 형식 v2)에서 옳은 불릿 6개가 불일치로 찍혔다. 차이 표현(`25점 차이`·
# `0.007 높은`)을 칸 인용으로 읽음, `A(99%)가 100%인 B`의 값을 앞 모델에 묶음, 비교어를 다른 절의 값에
# 적용함, `두 모델 모두`를 후보 전원으로 읽음 — 넷을 고쳤다.
# 3 — 표식을 목록으로 바꾸고 `방향 불일치`(두 모델 값의 대소와 서술어)와 `표와 불일치`의 원인 셋을 더했다.
# 값의 주인을 "값 바로 앞 모델"에서 "절의 첫 모델"로(모델 이름 바로 뒤 괄호 값은 그 모델) 바꿨다.
# 4 — 실제 노트에서 샌 자리를 고쳤다: 나열 뒤 `모두`는 그 모델들, `A는 B와 함께`는 둘 다, `또는`은 값 집합에
# 드는지로 본다. `% 감소·증가`는 비율을 계산해 대조한다. 기준선 뒤 괄호 속 이름을 건너뛴다. 최상급(`가장`)을
# 판정한다. 확인하지 못한 수치 주장과 짝을 못 찾은 `더` 비교는 불릿을 `확인됨`으로 두지 않는다. 개수는 인용이 아니다.
# 5 — 최상급의 표시된 동점을 `대조 불가`로 바꿨다(칸이 반올림이라 진짜 동점인지 모른다). 주어가 여럿이면 맞음이 없다.
# 6 — 실제 노트에서 맞는 문장이 `방향 불일치`로 찍혔다("A가 가장 빠르며, B(…)보다 더 빠릅니다"). `X보다`의 X는
# 견주는 기준인데 절의 첫 이름이라 주어로 읽혔다. 기준 표기는 주어에서 빼고, 그래서 주어를 못 정하면 `대조 불가`다.
# 판정이 달라지면 이 수를 올리고 `tests/fixtures/note_verify/golden.json`의 기대값과 잠금 파일 항목을 함께 고친다.
VERSION = 6

VERIFIED = "확인됨"
MISMATCH = "표와 불일치"
DIRECTION = "방향 불일치"
UNVERIFIABLE = "대조 불가"
MARKERS = (VERIFIED, MISMATCH, DIRECTION, UNVERIFIABLE)

# `표와 불일치`의 원인 — 막는 방법이 달라 가른다. 앞의 둘은 출처가 있는 실수, 마지막만 지어낸 값이다.
CAUSE_TARGET = "대상 혼동"  # 같은 지표의 다른 모델 값
CAUSE_METRIC = "단위·지표 착오"  # 인용 단위가 그 칸의 단위와 다르고, 같은 모델의 다른 지표에 그 값이 있다
CAUSE_ABSENT = "표에 없는 값"

BASELINE_KEY = "__baseline__"
_ALL_CANDIDATES = "__all_candidates__"

# 한글 조사가 바로 붙는다("85%로") — 경계는 ASCII 영숫자로만 본다
_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z.])(\d+(?:\.\d+)?)\s*(%p|%|초|tok/s|GB|배)?(?![0-9A-Za-z.])")
# 개수는 표 칸을 인용한 것이 아니라 세어 본 것이라 인용으로 치지 않는다("25개 지표 중", "4가지") — `개선`은 개수가 아니다
_COUNT_AFTER = re.compile(r"^\s*(?:개(?![선발])|가지)")
_RELATIVE_AFTER = re.compile(r"^\s*(?:%p|포인트|배|이상|이하|미만|초과|차이|가량|정도)")
# 단위 없는 수 뒤에 비교어가 붙으면 두 값의 **차이**다("25점 차이로", "0.007 높은", "76.6 점 높은")
_DIFFERENCE_AFTER = re.compile(r"^\s*(?:점|포인트|%p)?\s*(?:차이|높|낮|앞|뒤|더 |많|적|빠르|빠른|느리|느린|감소|증가)")
# `%` 값 뒤의 감소·증가는 두 값의 **비율**이다("점수 편차에서 약 88% 감소")
_RATE_AFTER = re.compile(r"^\s*(감소|증가)")
# 두 값을 `또는`으로 이으면 한 주장이다("모두 100% 또는 99%")
_ALTERNATIVE_BETWEEN = re.compile(r"\s*(?:또는|혹은)\s*")
# 어림 표현은 차이를 대조하지 않는다("10% 이상 빠른")
_APPROX_AFTER = re.compile(r"^\s*(?:%|%p|점|포인트)?\s*(?:이상|이하|미만|초과|가량|정도|내외|안팎)")
_EXCEED = ("초과", "웃돌", "넘어", "넘는", "앞서")
_BELOW = ("못 미", "밑돌", "뒤처")
# 이름 붙은 두 모델을 가리키는 말 — 후보 전원이 아니다("A 0% vs B 0% — 두 모델 모두 0%")
_PAIR_WORDS = ("두 모델", "둘 다", "양쪽 모두", "양 모델")
_GROUP_WORDS = ("모두", "전원", "모든 모델", "모든 로컬", "네 모델", "다른 모델들은", "다른 모델들도", "나머지 모델")
# `X보다`의 X는 **견주는 기준**이지 주어가 아니다 — 이름 뒤에 (값 괄호가 있으면 그것까지 건너뛰고) `보다`가 붙는 표기.
# 앞 절에서 주어를 세우고 뒷 절에서 생략하면("A가 가장 빠르며, B(…)보다 더 빠릅니다") 그 절의 유일한 이름이 기준이다
_STANDARD_AFTER = re.compile(r"[^\s(]*\s*(?:\([^)]*\))?\s*보다")
# 비교어는 같은 **절** 안의 값에만 적용한다 — "A가 …를 달성하며, B는 1점 차이로 뒤처짐"의 "뒤처"는 앞 절 값의 대소가 아니다
_CLAUSE_SPLIT = re.compile(r"\s[—–]\s|(?<!\d),|,(?!\d)|;|\svs\.?\s|(?<=며)\s|(?<=지만)\s|반면")
# "기준선"은 바로 뒤에 붙은 값만 묶는다("기준선(80%)", "기준선 값은 19%") — 멀리 있는 값까지 가져가면
# 같은 문장 뒤쪽에서 후보 이야기로 돌아온 값("특히 간접은 38%")을 기준선 값으로 읽는다
_BASELINE_REACH = 15
_SYNONYMS = {"도구호출": "toolcalling", "툴콜링": "toolcalling", "툴호출": "toolcalling"}


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """소문자 + 한글·영숫자만 남긴 문자열과, 그 문자마다 원문 위치. 표기 차이(공백·괄호·`:`·`\\_`)를 흡수한다."""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.lower())
            index.append(i)
    return "".join(chars), index


def _norm(text: str) -> str:
    norm = _norm_with_map(text)[0]
    for a, b in _SYNONYMS.items():
        norm = norm.replace(a, b)
    return norm


# ---------------------------------------------------------------------------
# 표 파싱
# ---------------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _strip_direction(label: str) -> str:
    return re.sub(r"\s*[↑↓]\s*$", "", label).strip()


def parse_table(table_text: str) -> dict[str, Any]:
    """{"columns": [머리글…], "baseline_col": 인덱스|None, "rows": {라벨: [칸…]}}."""
    lines = [ln for ln in table_text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return {"columns": [], "baseline_col": None, "rows": {}}
    header = _cells(lines[0])[1:]
    baseline_col = next((i for i, h in enumerate(header) if h.startswith("기준선")), None)
    rows: dict[str, list[str]] = {}
    for ln in lines[2:]:
        cells = _cells(ln)
        if len(cells) >= 2:
            rows[_strip_direction(cells[0])] = cells[1:]
    return {"columns": header, "baseline_col": baseline_col, "rows": rows}


def _cell_value(cell: str) -> tuple[float, str | None] | None:
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(%|초|tok/s|GB)?", cell or "")
    if not m:
        return None
    return float(m.group(1)), m.group(2)


# ---------------------------------------------------------------------------
# 표기 찾기
# ---------------------------------------------------------------------------


def _name_forms(name: str) -> set[str]:
    """한 모델을 노트가 부를 법한 표기들(정규화). 전체 이름, 배포처를 뗀 이름, 태그를 뗀 이름,
    별칭, 계열+크기(`qwen3:4b-instruct…` → `qwen34b`)."""
    import model_aliases

    base = name.rpartition("/")[2]
    family, _, tag = base.partition(":")
    forms = {name, base, family, model_aliases.short_alias(name)}
    if tag:
        forms.add(f"{family}{tag.split('-')[0]}")
    return {n for n in (_norm(f) for f in forms) if len(n) >= 4}


def _model_keys(columns: list[str], baseline_col: int | None, name_variants: dict[str, list[str]] | None) -> dict[int, list[str]]:
    keys: dict[int, list[str]] = {}
    for i, header in enumerate(columns):
        if i == baseline_col:
            continue
        out: set[str] = set()
        for v in [header, *((name_variants or {}).get(header) or [])]:
            out |= _name_forms(v)
        keys[i] = sorted(out, key=len, reverse=True)
    # 두 모델이 공유하는 짧은 표기(예: 계열명만)는 누구인지 가르지 못하므로 뺀다
    shared = {k for col, ks in keys.items() for k in ks if any(k in other for c2, os_ in keys.items() if c2 != col for other in os_)}
    return {col: [k for k in ks if k not in shared] or ks for col, ks in keys.items()}


def _find_models(norm: str, keys: dict[int, list[str]]) -> list[tuple[int, int]]:
    """(정규화 위치, 열 인덱스). 같은 자리에 여러 모델이 걸리면(흔한 접두만 겹침) 가장 긴 표기를 택한다."""
    hits: list[tuple[int, int, int]] = []
    for col, variants in keys.items():
        for v in variants:
            start = 0
            while (pos := norm.find(v, start)) != -1:
                hits.append((pos, len(v), col))
                start = pos + 1
    hits.sort(key=lambda h: (h[0], -h[1]))
    out: list[tuple[int, int]] = []
    covered_until = -1
    for pos, length, col in hits:
        if pos < covered_until:
            continue
        out.append((pos, col))
        covered_until = pos + length
    return out


def _metric_keys(rows: dict[str, list[str]]) -> list[tuple[str, str, bool]]:
    """(정규화 표기, 라벨, 전체 라벨 여부). 괄호 앞 본체만 쓴 표기도 받되, 본체가 겹치는 지표끼리는 모호하다."""
    keys = []
    for label in rows:
        keys.append((_norm(label), label, True))
        main = re.sub(r"\s*\(.*?\)\s*", "", label).strip()
        if main and main != label:
            keys.append((_norm(main), label, False))
    return keys


def _find_metrics(norm: str, keys: list[tuple[str, str, bool]]) -> list[tuple[int, list[str]]]:
    """(정규화 위치, 후보 라벨들). 전체 라벨이 맞으면 그 하나, 본체만 맞으면 본체를 공유하는 라벨 전부."""
    found: dict[int, tuple[int, list[str], bool]] = {}
    for k, label, full in keys:
        if len(k) < 3:
            continue
        start = 0
        while (pos := norm.find(k, start)) != -1:
            prev = found.get(pos)
            if prev is None or len(k) > prev[0] or (len(k) == prev[0] and full and not prev[2]):
                found[pos] = (len(k), [label], full)
            elif len(k) == prev[0] and not full and not prev[2] and label not in prev[1]:
                prev[1].append(label)
            start = pos + 1
    # 짧은 표기가 긴 표기 안에 들어가 있으면 버린다
    spans = sorted(((pos, length, labels) for pos, (length, labels, _f) in found.items()), key=lambda s: (s[0], -s[1]))
    out: list[tuple[int, list[str]]] = []
    covered_until = -1
    for pos, length, labels in spans:
        if pos < covered_until:
            continue
        out.append((pos, labels))
        covered_until = pos + length
    return out


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def _bullets(note: str) -> list[str]:
    out: list[str] = []
    for line in note.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^([*\-+•·]|\d+[.)])\s+", stripped):
            out.append(re.sub(r"^([*\-+•·]|\d+[.)])\s+", "", stripped))
        elif out and line.startswith((" ", "\t")):
            out[-1] += " " + stripped  # 불릿 안에서 줄바꿈된 이어지는 줄
    return out


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"[.!?。]\s+|다\.\s*|$", text):
        end = m.end()
        if end > start:
            spans.append((start, end))
        start = end
        if m.end() == len(text):
            break
    return spans or [(0, len(text))]


def _matches(cited: float, unit: str | None, cell: str) -> bool:
    value = _cell_value(cell)
    if value is None:
        return False
    number, cell_unit = value
    if unit and cell_unit and unit != cell_unit:
        return False
    decimals = len(cell.split(".")[1].split()[0].rstrip("%초tok/sGB")) if "." in cell.split()[0] else 0
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    return abs(number - cited) <= max(tolerance, 0.5 if unit == "%" else tolerance)


def verify_bullet(bullet: str, table: dict[str, Any], name_variants: dict[str, list[str]] | None = None) -> dict[str, Any]:
    columns, rows, baseline_col = table["columns"], table["rows"], table["baseline_col"]
    norm, index = _norm_with_map(bullet)
    for a, b in _SYNONYMS.items():  # 동의어 치환은 길이가 달라 위치 지도를 다시 만든다
        while a in norm:
            pos = norm.find(a)
            norm = norm[:pos] + b + norm[pos + len(a):]
            index = index[:pos] + [index[pos]] * len(b) + index[pos + len(a):]
    model_hits = [(index[p], col) for p, col in _find_models(norm, _model_keys(columns, baseline_col, name_variants))]
    baseline_hits = [m.start() for m in re.finditer("기준선", bullet)] if baseline_col is not None else []
    model_hits.sort()
    metric_hits = [(index[p], labels) for p, labels in _find_metrics(norm, _metric_keys(rows))]

    citations: list[dict[str, Any]] = []
    for m in _NUMBER_RE.finditer(bullet):
        if _inside_name(bullet, m.start()) or (m.group(2) is None and _COUNT_AFTER.match(bullet[m.end():])):
            continue
        citations.append({"value": float(m.group(1)), "unit": m.group(2), "pos": m.start(), "end": m.end()})
    for prev, cur in zip(citations, citations[1:]):
        if _ALTERNATIVE_BETWEEN.fullmatch(bullet[prev["end"]:cur["pos"]]):
            options = prev.setdefault("options", [(prev["value"], prev["unit"])])
            options.append((cur["value"], cur["unit"]))
            cur["options"] = options

    sentences = _sentences(bullet)

    def sentence_of(pos: int) -> tuple[int, int]:
        return next(((s, e) for s, e in sentences if s <= pos < e), sentences[-1])

    results: list[dict[str, Any]] = []
    for c in citations:
        text_after = bullet[c["end"]:]
        rate = _RATE_AFTER.match(text_after) if c["unit"] == "%" else None
        if rate:
            results.append({**c, "status": UNVERIFIABLE, "relative": True, "rate": rate.group(1),
                            "reason": f"{rate.group(1)}율을 계산할 두 값을 문장에서 정하지 못함"})
            continue
        difference = c["unit"] in (None, "%p") and _DIFFERENCE_AFTER.match(text_after) and not _APPROX_AFTER.match(text_after)
        if difference or c["unit"] in ("%p", "배") or _RELATIVE_AFTER.match(text_after):
            # 표의 칸이 아니다. 정확한 차이면 아래에서 두 인용과 대조하고, 확인하지 못하면 대조 불가로 남는다
            results.append({**c, "status": UNVERIFIABLE, "relative": True, "difference": bool(difference),
                            "reason": "차이를 계산할 두 값을 문장에서 정하지 못함" if difference else "차이·비교 표현(표의 칸이 아님)"})
            continue
        s, e = sentence_of(c["pos"])
        model_col = _bind_model(bullet, c, model_hits, baseline_hits, baseline_col, s, e)
        labels = _bind_metric(bullet, c, metric_hits, citations, s, e, sentence_of)
        if model_col is None or labels is None:
            results.append({**c, "status": UNVERIFIABLE, "reason": "모델이나 지표를 문장에서 묶지 못함"})
            continue
        if len(labels) > 1:
            results.append({**c, "status": UNVERIFIABLE, "reason": f"지표가 모호함({', '.join(labels)})"})
            continue
        row = rows[labels[0]]
        candidates = [i for i in range(len(columns)) if i != baseline_col]
        if model_col == _ALL_CANDIDATES:
            target_cols, who = candidates, "후보 전원"
        elif isinstance(model_col, list):
            target_cols, who = model_col, " · ".join(columns[i] for i in model_col)
        else:
            target_cols, who = [model_col], columns[model_col]
        cells = [row[i] if i < len(row) else "" for i in target_cols]
        # `또는`으로 이은 값은 각 모델 칸이 그 값들 중 하나인지 본다 — 한쪽 값을 골라 대조하지 않는다
        options = c.get("options") or [(c["value"], c["unit"])]
        fits = [any(_matches(v, u, cell) for v, u in options) for cell in cells]
        entry = {**c, "status": VERIFIED if all(fits) else MISMATCH, "model": who, "metric": labels[0],
                 "table": " / ".join(cells), "cols": target_cols}
        if not all(fits):
            failing = target_cols[fits.index(False)]
            cause, where = _mismatch_cause(c, rows, columns, labels[0], failing)
            entry.update(reason=f"표에서 {who}의 {labels[0]} 칸은 {' / '.join(cells)}{where}", cause=cause)
        results.append(entry)

    _check_differences(results, sentence_of)
    _check_rates(results, sentence_of)
    checks = _contradiction(bullet, results, baseline_col, columns, sentences)
    checks += _comparative_direction(bullet, results, model_hits, columns, sentences)
    checks += _superlatives(bullet, results, model_hits, metric_hits, table, sentences)

    # 표식은 목록이다 — 우선순위를 매기면 하나가 숨는다. 틀린 값이 둘이면 표와 불일치도 둘이다(`또는`으로 이은 값은 한 주장).
    markers, claimed = [], set()
    for r in results:
        if r["status"] != MISMATCH or (r.get("options") and id(r["options"]) in claimed):
            continue
        if r.get("options"):
            claimed.add(id(r["options"]))
        markers.append({"marker": MISMATCH, "cause": r["cause"], "detail": r["reason"]})
    markers += [{"marker": DIRECTION, "detail": ch["detail"]} for ch in checks if ch["status"] == DIRECTION]
    if not markers:
        # `확인됨`은 확인했다는 뜻이어야 한다 — 확인하지 못한 수치 주장이나 판정하려다 재료가 없던 비교가 하나라도 있으면 대조 불가
        unchecked = [r["reason"] for r in results if r["status"] == UNVERIFIABLE] + \
                    [ch["detail"] for ch in checks if ch["status"] == UNVERIFIABLE]
        if unchecked:
            markers = [{"marker": UNVERIFIABLE, "detail": "; ".join(dict.fromkeys(unchecked))}]
        elif any(r["status"] == VERIFIED for r in results) or checks:
            markers = [{"marker": VERIFIED}]
        else:
            markers = [{"marker": UNVERIFIABLE, "detail": "인용한 수치 없음"}]
    hidden = ("pos", "end", "difference", "cols", "options")
    return {
        "text": bullet,
        "markers": markers,
        "citations": [{**{k: v for k, v in r.items() if k not in hidden},
                       **({"alternatives": [v for v, _u in r["options"]]} if r.get("options") else {})} for r in results],
        "checks": [{k: v for k, v in ch.items() if v is not None} for ch in checks],
    }


def _mismatch_cause(c: dict[str, Any], rows: dict[str, list[str]], columns: list[str], metric: str,
                    col: int) -> tuple[str, str]:
    """틀린 인용 하나의 원인과 이유 꼬리말. 막는 방법이 달라 가른다.
    - `대상 혼동` — 같은 지표의 다른 모델(기준선 포함) 칸에 그 값이 있다.
    - `단위·지표 착오` — **인용 단위가 그 칸의 단위와 다르고** 같은 모델의 다른 지표(그 단위)에 그 값이 있다.
      단위가 같으면 찾지 않는다 — `0%`·`100%` 같은 흔한 값이 우연히 맞아 지어낸 값이 착오로 가려진다.
      단위 불일치가 곧 "다른 칸에서 가져왔다"의 증거다.
    - `표에 없는 값` — 나머지."""
    row = rows[metric]
    others = [i for i in range(len(columns)) if i != col and i < len(row) and _matches(c["value"], c["unit"], row[i])]
    if others:
        return CAUSE_TARGET, f" ({c['value']:g}{c['unit'] or ''}: {columns[others[0]]}의 값)"
    own = _cell_value(row[col] if col < len(row) else "")
    if c["unit"] and own and own[1] and own[1] != c["unit"]:
        for label, cells in rows.items():
            other = _cell_value(cells[col] if col < len(cells) else "")
            if label != metric and other and other[1] == c["unit"] and _matches(c["value"], c["unit"], cells[col]):
                return CAUSE_METRIC, f" ({c['value']:g}{c['unit']}: 같은 모델의 {label} 값)"
    return CAUSE_ABSENT, ""


# 활용형까지 줄기로 받는다(`큽니다`·`빠릅니다`·`느려서`). 같은 글자로 시작하는 다른 말(`적합`·`작업`·`커버`)은 뺀다
_SIZE_SPEED_WORD = (r"(?:높|낮|크|큰|큽|커(?!버)|작(?!업|동|성)|많|적(?!합|극|절|용|응|중|자|성)"
                    r"|빠르|빠른|빠릅|빨리|빨라|느리|느린|느립|느려|늦)[가-힣]*")
_COMPARATIVE_RE = re.compile(rf"더\s*({_SIZE_SPEED_WORD})")
_SUPERLATIVE_RE = re.compile(rf"가장\s*({_SIZE_SPEED_WORD})")
# `더 큰 차이`·`가장 큰 격차`는 두 모델 사이의 틈을 말하는 것이지 한 모델 값의 크기가 아니다
_GAP_AFTER = re.compile(r"^\s*(?:차이|격차)")
_UP_WORDS = ("높", "크", "큰", "큽", "커", "많")
_DOWN_WORDS = ("낮", "작", "적")
_FAST_WORDS = ("빠르", "빠른", "빠릅", "빨리", "빨라")
# 속도 말은 단위가 대소를 정한다 — 초는 작을수록, tok/s는 클수록 빠르다. 그 밖의 단위는 판정하지 않는다
_FASTER_IS_SMALLER = {"초": True, "tok/s": False}


def _value_unit(r: dict[str, Any]) -> str | None:
    """인용 값의 단위 — 인용에 없으면 표 칸의 단위, 칸에도 없으면 지표 이름이 단위인 행(`tok/s (짧은 탐침, …)`)."""
    cell = _cell_value(r.get("table") or "")
    return r.get("unit") or (cell[1] if cell else None) or ("tok/s" if r["metric"].startswith("tok/s") else None)


def _shown(r: dict[str, Any], unit: str | None) -> str:
    """이유 문구의 값 — 표와 맞은 값이라 단위가 붙은 칸이면 칸 그대로(`5.10 GB`)."""
    if unit and unit in (r.get("table") or ""):
        return r["table"]
    return f"{r['value']:g}{'' if unit in (None, '%', '초') else ' '}{unit or ''}"


def _check(kind: str, status: str, detail: str | None = None) -> dict[str, Any]:
    """비교 서술 하나의 판정 — `확인됨` / `방향 불일치` / `대조 불가`(판정하려 했는데 재료가 없다)."""
    return {"kind": kind, "status": status, "detail": detail}


def _claims_larger(word: str, unit: str | None) -> bool | None:
    """서술어가 "값이 더 크다"를 주장하는가. 크기 말은 단위와 무관하고, 속도 말은 초·tok/s일 때만 뜻이 정해진다
    (None = 판정하지 않는 말)."""
    if word.startswith(_UP_WORDS):
        return True
    if word.startswith(_DOWN_WORDS):
        return False
    if unit in _FASTER_IS_SMALLER:
        return word.startswith(_FAST_WORDS) != _FASTER_IS_SMALLER[unit]
    return None


def _number(r: dict[str, Any]) -> float:
    """표와 맞은 인용의 값 — 칸 값이 더 정밀하면(`5.10`) 칸 값."""
    cell = _cell_value(r.get("table") or "")
    return cell[0] if cell else r["value"]


def _comparative_direction(bullet: str, results: list[dict[str, Any]], model_hits: list[tuple[int, int]],
                           columns: list[str], sentences: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """두 모델 값을 견주고 **"더 높다/낮다/많다/적다/빠르다/느리다"**로 맺은 서술이 두 값의 대소와 맞는가.
    지표의 좋은 방향(↑/↓)과는 별개다 — "누가 더 큰가"를 바로 썼는지를 본다.
    - **두 값이 모두 표와 맞을 때만** 본다. 값이 틀린 불릿은 이미 불일치이고, 어느 값으로 재느냐에 따라 결론이 갈린다
      — 판정하려 하지 않은 것이라 아무것도 남기지 않는다.
    - 주어는 서술어가 있는 절의 첫 모델 이름이다(값의 주인과 같은 규칙). **단 `X보다`의 X는 뺀다** — 견주는
      기준이지 주어가 아니다. 그 절의 이름이 기준뿐이면 주어는 앞 절에서 이어받은 것이라 이 절만으로 정할 수
      없고, **없는 주어로 판정하지 않는다**(`대조 불가`). 견주는 짝은 같은 문장에서 주어의 값과
      같은 지표로 인용된 다른 모델의 값 — 그런 짝이 하나가 아니면 판정하려 했는데 재료가 없는 것이라 `대조 불가`다.
    - 표의 두 값이 같으면(반올림된 칸) 어느 쪽이 큰지 모르므로 `대조 불가`.
    - 크기 말은 단위와 무관하게, 속도 말은 `초`·`tok/s`일 때만 판정한다."""
    checks = []
    for s, e in sentences:
        for cs, ce in _clauses(bullet, s, e):
            m = _COMPARATIVE_RE.search(bullet, cs, ce)
            if not m or _GAP_AFTER.match(bullet[m.end():]):
                continue
            phrase = bullet[m.start():m.end()].strip()
            names = [(p, col) for p, col in model_hits if cs <= p < ce]
            subject = next((columns[col] for p, col in names if not _STANDARD_AFTER.match(bullet, p, ce)), None)
            if subject is None and names:
                checks.append(_check("비교급", UNVERIFIABLE,
                                     f"'{phrase}' — 이 절의 모델 이름이 견주는 기준(`…보다`)뿐이라 주어를 정하지 못함"))
                continue
            cited = [r for r in results if s <= r["pos"] < e and not r.get("relative") and isinstance(r.get("model"), str)
                     and r.get("metric") and r["model"] in columns]
            pairs = [(a, b) for a in cited if a["model"] == subject
                     for b in cited if b["metric"] == a["metric"] and b["model"] != subject]
            if len(pairs) != 1:
                checks.append(_check("비교급", UNVERIFIABLE, f"'{phrase}' — 견줄 두 값을 문장에서 정하지 못함"))
                continue
            a, b = pairs[0]
            if a["status"] != VERIFIED or b["status"] != VERIFIED:
                continue
            unit = _value_unit(a)
            claims_larger = _claims_larger(m.group(1), unit)
            if claims_larger is None:
                continue
            if unit != _value_unit(b):
                checks.append(_check("비교급", UNVERIFIABLE, f"'{phrase}' — 두 값의 단위가 달라 견주지 못함"))
            elif _number(a) == _number(b):
                checks.append(_check("비교급", UNVERIFIABLE, f"'{phrase}' — 표의 두 값이 같아(반올림) 어느 쪽이 큰지 모름"))
            elif claims_larger != (_number(a) > _number(b)):
                checks.append(_check("비교급", DIRECTION,
                                     f"{subject} {_shown(a, unit)} · {b['model']} {_shown(b, unit)}인데 서술은 {subject} 쪽이 '{phrase}'"))
            else:
                checks.append(_check("비교급", VERIFIED))
    return checks


# 이름을 잇는 말 — `A와 B`, `A(100%)와 B(99%), C`, `A 모델 및 B`
_LIST_JOIN = re.compile(r"\S+\s*(?:모델)?\s*(?:\([^)]*\))?\s*(?:와|과|및|,|·|하고|그리고)\s*")


def _listed_group(bullet: str, model_hits: list[tuple[int, int]], i: int, s: int, e: int) -> list[int]:
    """`model_hits[i]`와 같은 문장에서 이름을 잇는 말로만 이어진 모델 표기들의 인덱스(나열이 아니면 자기 하나)."""
    lo = hi = i
    while lo > 0 and model_hits[lo - 1][0] >= s and _LIST_JOIN.fullmatch(bullet[model_hits[lo - 1][0]:model_hits[lo][0]]):
        lo -= 1
    while hi + 1 < len(model_hits) and model_hits[hi + 1][0] < e and _LIST_JOIN.fullmatch(bullet[model_hits[hi][0]:model_hits[hi + 1][0]]):
        hi += 1
    return list(range(lo, hi + 1))


def _superlatives(bullet: str, results: list[dict[str, Any]], model_hits: list[tuple[int, int]],
                  metric_hits: list[tuple[int, list[str]]], table: dict[str, Any],
                  sentences: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """`가장` + 크기 말·속도 말 — 주장된 모델이 **노트가 받은 표의 후보** 중 그 지표의 극값인가.
    - 기준선은 비교에서 뺀다(노트의 "모든 모델 중"은 로컬 모델이고, 조건이 같을 수 없는 값이다).
    - 표시된 동점은 `대조 불가`다 — 칸이 반올림이라 진짜 동점인지 모른다. `능력 부재` 칸은 뺀다(해당 없음이 확인된 칸).
    - 값을 모르는 칸(`측정 안 됨` 등)이 있으면 — 아는 값이 이미 이기면 `방향 불일치`, 아니면 `대조 불가`.
      모르는 값은 주장을 뒤집을 수만 있고 구할 수는 없다.
    - 주어의 그 지표 인용값이 틀렸으면 건너뛴다(두 값이 모두 표와 맞을 때만 방향을 본다는 규칙과 같다).
    - 주어가 여럿(`A와 B가 가장 …`)이면 상위 N위(N = 나열 수) 안일 때 "공동 1위"와 "상위 N"을 가를 수 없어
      `대조 불가`, 그 밖은 `방향 불일치`다(모두 극값이려면 표에서 동점이어야 해 맞음은 없다).
    - 주어·지표·주어의 칸 값을 못 찾으면 `대조 불가`."""
    columns, rows, baseline_col = table["columns"], table["rows"], table["baseline_col"]
    candidates = [i for i in range(len(columns)) if i != baseline_col]
    checks = []
    for s, e in sentences:
        for cs, ce in _clauses(bullet, s, e):
            for m in _SUPERLATIVE_RE.finditer(bullet, cs, ce):
                if _GAP_AFTER.match(bullet[m.end():]):
                    continue
                phrase = bullet[m.start():m.end()].strip()
                metric = _superlative_metric(metric_hits, s, m.start())
                if metric is None:
                    checks.append(_check("최상급", UNVERIFIABLE, f"'{phrase}' — 어느 지표인지 문장에서 정하지 못함"))
                    continue
                row = rows[metric]
                unit = next((cv[1] for cv in map(_cell_value, row) if cv and cv[1]), None) or \
                    ("tok/s" if metric.startswith("tok/s") else None)
                claims_larger = _claims_larger(m.group(1), unit)
                if claims_larger is None:
                    continue
                subjects = _superlative_subjects(bullet, results, model_hits, metric, s, e, cs, m.start())
                if not subjects:
                    checks.append(_check("최상급", UNVERIFIABLE, f"'{phrase}' — 주어 모델을 문장에서 정하지 못함"))
                    continue
                if any(r["status"] == MISMATCH and r.get("metric") == metric and s <= r["pos"] < e
                       and any(col in r.get("cols", []) for col in subjects) for r in results):
                    continue
                checks.append(_rank_claim(phrase, subjects, claims_larger, row, columns, candidates))
    return checks


def _superlative_metric(metric_hits: list[tuple[int, list[str]]], s: int, at: int) -> str | None:
    """`가장 …`이 말하는 지표 — 같은 문장에서 가장 가까운 앞 표기, 없으면 불릿 앞머리의 표기.
    본체만 쓴 표기(`지시 따르기 정확도`)라 여럿이면 그 불릿에 앞서 온 전체 표기로 좁힌다."""
    before = [labels for p, labels in metric_hits if s <= p < at] or [labels for p, labels in metric_hits if p < at][:1]
    if not before:
        return None
    labels = before[-1]
    if len(labels) > 1:
        exact = [ls[0] for p, ls in metric_hits if p < at and len(ls) == 1 and ls[0] in labels]
        labels = exact[-1:] or labels
    return labels[0] if len(labels) == 1 else None


def _superlative_subjects(bullet: str, results: list[dict[str, Any]], model_hits: list[tuple[int, int]], metric: str,
                          s: int, e: int, cs: int, at: int) -> list[int]:
    """주어 — 서술어가 있는 절에서 처음 나온 모델 이름(나열이면 그 모델들). 절에 이름이 없으면("해당 모델의 …는
    3.05 GB로, … 가장 낮은") 같은 문장에서 그 지표 값이 묶인 모델."""
    first = next((i for i, (p, _col) in enumerate(model_hits) if cs <= p < at), None)
    if first is not None:
        return list(dict.fromkeys(model_hits[i][1] for i in _listed_group(bullet, model_hits, first, s, e)
                                  if model_hits[i][0] < at))
    bound = [r["cols"] for r in results if r.get("metric") == metric and s <= r["pos"] < at and len(r.get("cols", [])) == 1]
    return bound[0] if bound else []


def _rank_claim(phrase: str, subjects: list[int], claims_larger: bool, row: list[str], columns: list[str],
                candidates: list[int]) -> dict[str, Any]:
    cells = {i: (row[i] if i < len(row) else "") for i in candidates}
    known = {i: cv[0] for i, cell in cells.items() if (cv := _cell_value(cell))}
    unknown = [i for i, cell in cells.items() if i not in known and cell.strip() != "능력 부재"]
    names = " · ".join(columns[i] for i in subjects)
    missing = [i for i in subjects if i not in known]
    if missing:
        return _check("최상급", UNVERIFIABLE, f"'{phrase}' — {' · '.join(columns[i] for i in missing)} 칸에 값이 없음")
    key = {i: v if claims_larger else -v for i, v in known.items()}
    best = max(key.values())
    # 순위는 표시된 값이 **엄격히 큰** 후보만 세어 매긴다 — 같은 값은 반올림 뒤 어느 쪽이 큰지 모르므로 불리하게 세지 않는다
    rank = {i: 1 + sum(1 for j in known if key[j] > key[i]) for i in subjects}
    leaders = [i for i in known if key[i] == best]
    if len(subjects) == 1 and rank[subjects[0]] == 1:
        if len(leaders) > 1:
            # 표시된 동점은 진짜 동점인지 모른다(칸이 반올림) — `가장`의 뜻에 공동 극값이 들어가도 사실이 확립되지 않는다
            return _check("최상급", UNVERIFIABLE,
                          f"'{phrase}' — 표시된 극값이 {' · '.join(columns[i] for i in leaders)} 동점이라(칸이 반올림) 확정하지 못함")
        if unknown:
            return _check("최상급", UNVERIFIABLE,
                          f"'{phrase}' — {' · '.join(columns[i] for i in unknown)} 칸 값을 몰라 확정하지 못함")
        return _check("최상급", VERIFIED)
    # 주어가 여럿이면 맞음이 없다 — 모두 극값이려면 표에서 동점이어야 하는데 동점은 확정하지 못한다
    if all(r <= len(subjects) for r in rank.values()):
        why = ("모두 표시된 극값이지만 칸이 반올림돼 진짜 공동 극값인지 모름" if all(r == 1 for r in rank.values())
               else f"상위 {len(subjects)}위 안이지만 모두 극값은 아니라 공동 1위인지 상위 {len(subjects)}위인지 가리지 못함")
        return _check("최상급", UNVERIFIABLE, f"'{phrase}' — {names}: {why}")
    extreme = "최댓값" if claims_larger else "최솟값"
    return _check("최상급", DIRECTION,
                  f"{names} {' / '.join(cells[i] for i in subjects)}인데 서술은 '{phrase}' — "
                  f"후보 중 {extreme}은 {' · '.join(columns[i] for i in leaders)} {cells[leaders[0]]}")


def _bind_model(bullet: str, c: dict[str, Any], model_hits: list[tuple[int, int]], baseline_hits: list[int],
                baseline_col: int | None, s: int, e: int) -> int | str | list[int] | None:
    """인용 하나가 가리키는 모델 열.
    - "기준선"은 바로 뒤에 붙은 값만 묶는다(`_BASELINE_REACH`자 안, 뒤따르는 괄호 속 이름은 세지 않는다).
    - 모델 이름 바로 뒤 괄호 속 값은 그 모델이다("A (1.91초)는 B (2.22초)보다").
    - 그 밖에는 값이 든 절의 첫 모델 표기("A는 B와 달리 0%"의 0%는 A). 절 안에 없으면 같은 불릿에서
      가장 가까운 앞의 모델 표기(대명사 "이 모델"은 앞 문장을 잇는다).
    - 그 모델 표기와 값 사이에 "모두"·"다른 모델들은" 같은 말이 있으면 후보 전원이다. 단 이름을 둘 이상
      나열한 바로 뒤의 "모두"는 그 모델들이고, "A는 B와 함께"는 A와 B다.
    - 앞에 없으면 같은 문장의 뒤 표기를 쓴다.
    - 값 바로 뒤가 "인 <모델>"이면 그 뒤 모델이다("A(99%)가 100%인 B에 근접").
    - "두 모델 모두"는 같은 문장에서 앞서 이름이 나온 두 모델이다(목록을 돌려준다)."""
    relative_clause = re.match(r"\s*\)?\s*인\s+", bullet[c["end"]:])
    if relative_clause:
        target = c["end"] + relative_clause.end()
        following = [col for p, col in model_hits if target <= p <= target + 1]
        if following:
            return following[0]
    # 모델 이름 바로 뒤 괄호 속 값은 그 모델의 것이다("llama3.2-3b (1.91초)는 gemma3-4b (2.22초)보다")
    opening = bullet.rfind("(", 0, c["pos"])
    if opening != -1 and not bullet[opening + 1:c["pos"]].strip():
        named = [(p, col) for p, col in model_hits if p < opening]
        if named and re.fullmatch(r"\S+\s*", bullet[named[-1][0]:opening]):
            return named[-1][1]
    near_baseline = [p for p in baseline_hits if p < c["pos"] and _baseline_gap(bullet, p, c["pos"]) <= _BASELINE_REACH]
    before_models = [(p, col) for p, col in model_hits if p < c["pos"]]
    if near_baseline and (not before_models or near_baseline[-1] > before_models[-1][0]):
        return baseline_col
    if before_models:
        # 값의 주인은 그 값이 든 절의 첫 모델 이름이다(방향 판정의 주어와 같은 규칙) — "A는 B와 달리 0%"의 0%는 A의 것
        cs, _ce = next(((a, b) for a, b in _clauses(bullet, s, e) if a <= c["pos"] < b), (s, e))
        in_clause = [(p, col) for p, col in before_models if p >= cs]
        last_pos, col = in_clause[0] if in_clause else before_models[-1]
        between = bullet[last_pos:c["pos"]]
        if any(w in between for w in _PAIR_WORDS):
            named = list(dict.fromkeys(col for p, col in model_hits if s <= p < c["pos"]))
            return named[-2:] if len(named) >= 2 else None
        # `A는 B와 함께 100%` — 두 모델 모두에 대한 주장이다
        together = re.search(r"(?:와|과)\s*함께", between)
        if together:
            named = list(dict.fromkeys(cl for p, cl in model_hits if last_pos <= p < last_pos + together.start()))
            if len(named) >= 2:
                return named
        if any(w in between for w in _GROUP_WORDS):
            # 이름을 둘 이상 나열한 **바로 뒤의** `모두`는 그 모델들이다 — 후보 전원이 아니다
            group = _listed_group(bullet, model_hits, model_hits.index((last_pos, col)), s, e)
            last = max((k for k in group if model_hits[k][0] < c["pos"]), key=lambda k: model_hits[k][0])
            if len(group) >= 2 and _RIGHT_BEFORE_ALL.match(bullet, model_hits[last][0]):
                return list(dict.fromkeys(model_hits[k][1] for k in group if model_hits[k][0] < c["pos"]))
            return _ALL_CANDIDATES
        return col
    if any(w in bullet[s:c["pos"]] for w in _GROUP_WORDS):
        return _ALL_CANDIDATES
    after = [col for p, col in model_hits if c["end"] <= p < e]
    return after[0] if after else None


_RIGHT_BEFORE_ALL = re.compile(r"\S+\s*(?:모델)?\s*(?:\([^)]*\))?\s*(?:이|가|은|는|도)?\s*모두")


def _baseline_gap(bullet: str, at: int, pos: int) -> int:
    """`기준선` 첫 글자에서 값까지의 거리. 기준선 뒤의 괄호 속 이름(`기준선(gpt-5.6-luna)은 100%`)은 세지 않는다 —
    괄호 안에 값이 있는 `기준선(80%)`은 그대로 가깝다."""
    end = at + len("기준선")
    paren = re.match(r"\s*\([^)]*\)", bullet[end:pos])
    return pos - at - (paren.end() if paren else 0)


def _bind_metric(bullet: str, c: dict[str, Any], metric_hits: list[tuple[int, list[str]]],
                 citations: list[dict[str, Any]], s: int, e: int, sentence_of) -> list[str] | None:
    """인용 하나가 가리키는 지표(후보 라벨들).
    - 값 바로 뒤에 지표가 붙어 있으면("100%의 폐쇄형 정답 정확도") 그것.
    - 나열("A와 B에서 X와 Y")이면 순서대로 짝짓는다 — 사이에 인용이 끼지 않은 연속 지표 묶음과, 그 뒤
      다음 지표 전까지의 인용 수가 같을 때만.
    - 그 밖에는 같은 문장에서 가장 가까운 앞 표기, 앞에 없으면 뒤 표기."""
    before = [(p, labels) for p, labels in metric_hits if s <= p < c["pos"]]
    after = [(p, labels) for p, labels in metric_hits if c["end"] <= p < e]
    if after and bullet[c["end"]:after[0][0]].strip() == "의":
        return after[0][1]
    if not before:
        return after[0][1] if after else None
    in_sentence = [x for x in citations if sentence_of(x["pos"]) == (s, e)]
    run = [before[-1]]
    for prev in reversed(before[:-1]):
        if any(prev[0] < x["pos"] < run[0][0] for x in in_sentence):
            break
        run.insert(0, prev)
    if len(run) > 1:
        next_metric = after[0][0] if after else e
        group = [x for x in in_sentence if run[-1][0] < x["pos"] < next_metric]
        if len(group) == len(run) and c in group:
            return run[group.index(c)][1]
    return before[-1][1]


def _inside_name(text: str, pos: int) -> bool:
    """모델 이름·지표 이름 속 숫자(`qwen3:4b`, `2507`, `q4_K_M`)는 인용이 아니다 — 앞뒤가 이름 문자로 붙어 있다."""
    before = text[pos - 1] if pos > 0 else " "
    return before.isalpha() or before in ":_-/\\."


def _check_differences(results: list[dict[str, Any]], sentence_of) -> None:
    """차이 표현("25점 차이", "0.007 높은")을 같은 문장의 **앞선 두 인용**(표와 맞은 값, 같은 지표)과 대조한다.
    그런 두 값이 정확히 있을 때만 판정하고, 아니면 `대조 불가`로 둔다. 표 칸이 반올림된 값이라
    백분율 차이는 1점까지 허용한다."""
    for r in results:
        if not r.get("difference"):
            continue
        span = sentence_of(r["pos"])
        prior = [x for x in results if x.get("model") and x["status"] == VERIFIED and x["pos"] < r["pos"]
                 and sentence_of(x["pos"]) == span]
        if len(prior) < 2 or prior[-1]["metric"] != prior[-2]["metric"]:
            continue
        a, b = prior[-2], prior[-1]
        expected = abs(a["value"] - b["value"])
        decimals = len(f"{r['value']:g}".partition(".")[2])
        tolerance = max(0.5 * 10 ** (-decimals), 1.0 if a.get("unit") == "%" else 0.0) + 1e-9
        if abs(r["value"] - expected) <= tolerance:
            r.update(status=VERIFIED, reason=None, table=f"{a['value']:g} − {b['value']:g}")
        else:
            # 틀린 차이는 표 어느 칸에서도 온 값이 아니다
            r.update(status=MISMATCH, cause=CAUSE_ABSENT,
                     reason=f"{a['model']}와 {b['model']}의 {a['metric']} 차이는 표에서 {expected:g}")
    for r in results:
        if r.get("reason") is None:
            r.pop("reason", None)


# 비율 대조의 허용 오차(%p) — 표 칸이 반올림된 값이라 거기서 나온 비율도 그만큼 흔들린다
_RATE_TOLERANCE = 1.0


def _check_rates(results: list[dict[str, Any]], sentence_of) -> None:
    """`N% 감소·증가`를 같은 문장에서 **같은 지표로 표와 맞은 값 정확히 둘**과 대조한다.
    `감소`는 큰 값 대비, `증가`는 작은 값 대비로 계산한다(주어를 가리지 않아도 된다). 그런 둘이 없으면 대조 불가로 남는다.
    % 지표에서는 비율로도 %p 차이로도 읽힌다 — 둘 다 틀리면 표에 없는 값, 둘 다 맞으면 확인됨, 한쪽만 맞으면
    어느 뜻으로 썼는지 가릴 수 없어 대조 불가다."""
    for r in results:
        if not r.get("rate"):
            continue
        span = sentence_of(r["pos"])
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for x in results:
            if x["status"] == VERIFIED and not x.get("relative") and len(x.get("cols", [])) == 1 and sentence_of(x["pos"]) == span:
                by_metric.setdefault(x["metric"], []).append(x)
        groups = [g for g in by_metric.values() if len(g) >= 2]
        if len(groups) != 1 or len(groups[0]) != 2:
            continue
        a, b = groups[0]
        big, small = max(_number(a), _number(b)), min(_number(a), _number(b))
        base = big if r["rate"] == "감소" else small
        if base == 0:
            continue
        rate = (big - small) / base * 100
        rate_ok = abs(r["value"] - rate) <= _RATE_TOLERANCE + 1e-9
        pair = f"{a['model']}와 {b['model']}의 {a['metric']}"
        if _value_unit(a) != "%":
            if rate_ok:
                r.update(status=VERIFIED, reason=None, table=f"({big:g} − {small:g}) / {base:g}")
            else:
                r.update(status=MISMATCH, cause=CAUSE_ABSENT, reason=f"{pair} {r['rate']}율은 표에서 {rate:.0f}%")
            continue
        points = big - small
        points_ok = abs(r["value"] - points) <= _RATE_TOLERANCE + 1e-9
        if rate_ok and points_ok:
            r.update(status=VERIFIED, reason=None, table=f"({big:g} − {small:g}) / {base:g} · {big:g} − {small:g}")
        elif rate_ok or points_ok:
            r.update(reason=f"{pair} {r['rate']}율은 표에서 {rate:.0f}%, 차이는 {points:g}%p — 어느 뜻으로 썼는지 가릴 수 없음")
        else:
            r.update(status=MISMATCH, cause=CAUSE_ABSENT,
                     reason=f"{pair} {r['rate']}율은 표에서 {rate:.0f}%(차이로 읽어도 {points:g}%p)")
    for r in results:
        if r.get("reason") is None:
            r.pop("reason", None)


def _clauses(bullet: str, s: int, e: int) -> list[tuple[int, int]]:
    spans, start = [], s
    for m in _CLAUSE_SPLIT.finditer(bullet, s, e):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < e:
        spans.append((start, e))
    return spans


def _contradiction(bullet: str, results: list[dict[str, Any]], baseline_col: int | None, columns: list[str],
                   sentences: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """같은 **절**에서 기준선 인용과 후보 인용이 비교어로 이어졌는데 대소가 반대면 모순이다(`방향 불일치`).
    기준선이 없으면 앞의 두 인용을 비교한다. 단위가 다르면 비교하지 않는다. 절을 넘으면 비교어의
    주어를 알 수 없으므로 판정하지 않는다(모르는 것을 틀린 것으로 표시하지 않는다).
    두 모델 비교와 같이 **두 값이 모두 표와 맞을 때만** 본다.
    짝을 못 찾아도 `대조 불가`로 남기지 않는다 — 비교어(`뒤처`·`앞서`)가 차이 표현에 붙거나 "앞서 언급한"처럼
    비교가 아닌 자리에도 나와, 이미 확인된 문장에 "확인 못 했다"를 붙이게 된다."""
    baseline_name = columns[baseline_col] if baseline_col is not None else None
    clauses = [c for s, e in sentences for c in _clauses(bullet, s, e)]
    checks = []
    for s, e in clauses:
        sentence = bullet[s:e]
        exceed = any(w in sentence for w in _EXCEED)
        below = any(w in sentence for w in _BELOW)
        if not (exceed or below):
            continue
        cited = [r for r in results if s <= r["pos"] < e and "model" in r and not r.get("relative")]
        base = [r for r in cited if r["model"] == baseline_name]
        others = [r for r in cited if r["model"] != baseline_name]
        pairs = [(o, base[0]) for o in others if o.get("unit") == base[0].get("unit")] if base else (
            [(cited[0], cited[1])] if len(cited) >= 2 and cited[0].get("unit") == cited[1].get("unit") else []
        )
        for a, b in pairs:
            if a["status"] != VERIFIED or b["status"] != VERIFIED:
                # 짝은 문장에서 고르고 표와 맞는지는 그 뒤에 본다 — 맞는 값만 남긴 뒤 고르면 틀린 값 자리에
                # 다른 값이 들어와, 서술이 견주지 않은 두 값을 견줄 수 있다
                continue
            if exceed and a["value"] < b["value"]:
                checks.append(_check("기준선 모순", DIRECTION,
                                     f"{a['value']:g}{a.get('unit') or ''}이(가) {b['value']:g}{b.get('unit') or ''}을(를) 넘는다고 서술"))
            elif below and a["value"] > b["value"]:
                checks.append(_check("기준선 모순", DIRECTION,
                                     f"{a['value']:g}{a.get('unit') or ''}이(가) {b['value']:g}{b.get('unit') or ''}에 못 미친다고 서술"))
            else:
                continue
            return checks  # 한 불릿에 모순 하나면 충분하다 — 같은 서술을 짝마다 되풀이하지 않는다
    return checks


def verify(note: str, table_text: str, name_variants: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """노트 전체. `name_variants`는 표 머리글(별칭) → 같은 모델의 다른 표기(전체 이름 등)."""
    table = parse_table(table_text)
    bullets = [verify_bullet(b, table, name_variants) for b in _bullets(note)]
    # 표식별 **불릿 수** — 한 불릿에 표식이 여럿일 수 있어 합이 불릿 수를 넘는다. 그래서 전체 불릿 수를 함께 싣는다
    counts = {m: sum(1 for b in bullets if any(x["marker"] == m for x in b["markers"])) for m in MARKERS}
    return {"version": VERSION, "bullets": bullets, "counts": counts, "total": len(bullets)}
