# -*- coding: utf-8 -*-
"""문항 재료 — 문서에서 **사실 후보**를 뽑고, **정답 표기 후보**를 만들고, **원천 뼈대**를 짠다.

사람이 문항을 쓰기 전에 코드가 할 수 있는 일은 셋이다. 셋 다 세는 값과 규칙뿐이고 모델을 부르지 않는다.

1. **사실 후보** — 문서에서 숫자가 든 줄을 모아, 줄마다 **긴 문서에도 그대로 있는지**와 **다른 문서에도 같은 값이
   나오는지**를 붙인다. 짧은 문서에만 있는 사실로 문항을 만들면 한쪽에서만 맞는 정답이 되고, 여러 문서에 같은
   값이 있으면 문서를 지정하지 않은 문항은 정답이 둘이 되어 채점이 깨진다.
2. **정답 표기 후보** — `5일`에는 `오 일`도, `12,000원`에는 `12000원`·`1만 2천 원`도 정답이다. 표기를 빠뜨리면
   맞게 답하고도 오답이 된다. 규칙으로 만들고 사람이 지우거나 더한다.
3. **원천 뼈대** — 고른 사실로 빈칸만 남은 원천을 짠다. 사람은 질문·변형과 `문서에 없는 사실`만 채운다.

**질문 문장, 문서에 없는 사실, 지어냄을 잡는 정규식, 긴 컨텍스트 시나리오는 사람이 쓴다** — 규칙으로 만들 수
없고, 모델에 맡기면 재는 대상이 문제를 알게 되거나 오판이 조용히 점수를 망친다.
"""

import json
import re
from typing import Any

import quality_testsets as qt
import testset_documents as td

SOURCE_PATH = qt.TESTSETS_DIR / "facts.json"

# 사실이 실릴 만한 줄 — 숫자에 단위가 붙은 자리. 단위가 없는 숫자(조항 번호·표 구분선)는 사실이 아니다
VALUE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:자|일|주|개월|개|명|회|번|시간|분|초|원|%|퍼센트|USD|달러|영업일|등급|배|건)")
_SINO = "영일이삼사오육칠팔구"
_NATIVE = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열")
# 고유어로 세는 단위 — `두 시간`은 자연스럽고 `이 시간`은 아니다
_NATIVE_UNITS = ("시간", "개", "명", "번", "달", "살")


def _sino(number: int) -> str:
    """한자어 수사 — `180` → `백팔십`. 만 단위까지만 다룬다(문서의 사실은 그 안에 든다)."""
    if number == 0:
        return "영"
    if number >= 10_000:
        man, rest = divmod(number, 10_000)
        return (_sino(man) if man > 1 else "") + "만" + (f" {_sino(rest)}" if rest else "")
    out = ""
    for size, name in ((1000, "천"), (100, "백"), (10, "십")):
        digit, number = divmod(number, size)
        if digit:
            out += (_SINO[digit] if digit > 1 else "") + name
    return out + (_SINO[number] if number else "")


def _mixed(number: int) -> str:
    """숫자와 한글을 섞어 읽는 표기 — `12000` → `1만 2천`. 금액에서 사람이 흔히 쓰는 꼴이다."""
    man, rest = divmod(number, 10_000)
    if not man:
        return ""
    if not rest:
        return f"{man}만"
    if rest % 1000 == 0:
        return f"{man}만 {rest // 1000}천"
    return f"{man}만 {rest:,}"


def answer_forms(value: str) -> list[str]:
    """정답 표기 후보 — 원래 표기를 맨 앞에 두고 규칙으로 만든 것을 뒤에 붙인다. **사람이 확인한다**:
    규칙이 만든 표기가 어색하면 지우고, 문서에만 있는 말(`전 직원`)은 사람이 더한다."""
    value = " ".join(value.split())
    found = re.match(r"(\d[\d,]*(?:\.\d+)?)\s*(.*)", value)
    if not found:
        return [value]
    digits, unit = found.group(1), found.group(2).strip()
    forms = [value]
    plain = digits.replace(",", "")
    if plain != digits:
        forms.append(f"{plain}{unit}")
    if "." in plain:
        return forms
    number = int(plain)
    space = " " if unit and not unit.startswith("%") else ""
    if unit in _NATIVE_UNITS and number <= 10:
        forms.append(f"{_NATIVE[number]} {unit}")
    elif unit and number < 100_000_000:
        forms.append(f"{_sino(number)}{space}{unit}")
    if mixed := _mixed(number):
        forms.append(f"{mixed}{space}{unit}".strip())
    if unit == "%":
        forms.append(f"{digits}퍼센트")
    out: list[str] = []
    for form in forms:
        if form and form not in out:
            out.append(form)
    return out


def candidates() -> list[dict[str, Any]]:
    """놓인 문서에서 사실 후보를 뽑는다 — 짧은 문서를 훑고, 줄마다 긴 문서에도 있는지와 다른 문서에도 같은 값이
    나오는지를 붙인다. **고르는 것은 사람이 한다** — 코드는 고를 거리를 모아 줄 뿐이다."""
    folder = qt.TESTSETS_DIR / td.EDITIONS["짧은 문서"]
    texts = {path.name: path.read_text(encoding="utf-8")
             for path in sorted(folder.glob("*.md"))} if folder.exists() else {}
    rows: list[dict[str, Any]] = []
    for name, text in texts.items():
        long_path = td.target_path("긴 문서", name)
        long_text = " ".join(long_path.read_text(encoding="utf-8").split()) if long_path.exists() else ""
        section = ""
        for number, raw in enumerate(text.splitlines(), 1):
            line = " ".join(raw.split())
            if raw.startswith("#"):
                section = raw.lstrip("# ").strip()
            if not line or set(line) <= set("|-: "):
                continue
            # 한 줄에 값이 여럿이면(표 한 행에 한도와 횟수가 함께 있는 식) 값마다 후보를 낸다
            values: list[str] = []
            for found in VALUE.finditer(line):
                value = " ".join(found.group(0).split())
                if value not in values:
                    values.append(value)
            for index, value in enumerate(values):
                rows.append({
                    "id": f"{name[:-3]}#{number}" + (f".{index}" if index else ""),
                    "doc": f"{td.EDITIONS['짧은 문서']}/{name}",
                    "section": section,
                    "line": line[:160],
                    "value": value,
                    "in_long": line in long_text,
                    "also_in": [other[:-3] for other, body in texts.items() if other != name and value in body],
                    "answer_forms": answer_forms(value),
                })
    return rows


def skeleton(picked: list[str], canary: str = "") -> dict[str, Any]:
    """고른 사실 후보로 **빈칸만 남은 원천**을 짠다. 사람이 채우는 칸은 넷이다 — 질문과 그 변형, 문서에 없는
    사실과 그 변형, 지어냄을 잡는 정규식, 그리고 문서마다의 요약 질문과 `task_keywords`.

    canary는 빈칸이 아니다 — 심은 문서에서 읽어 채운다. 옮겨 적다 한 글자만 틀려도 인젝션 채점이 통째로
    어긋나고, 그 사실은 측정이 끝난 뒤에야 드러난다."""
    canary = canary.strip() or td.find_canary()[0]
    by_id = {row["id"]: row for row in candidates()}
    missing = [one for one in picked if one not in by_id]
    if missing:
        raise ValueError(f"없는 사실 후보다: {' · '.join(missing)}")
    facts = []
    for i, one in enumerate(picked, 1):
        row = by_id[one]
        facts.append({
            "id": f"f{i:02d}",
            "doc": row["doc"],
            "label": row["section"] or row["value"],
            "ask": ["", ""],
            "answer_forms": row["answer_forms"],
            "absent": {"ask": ["", ""], "fabrication_patterns": []},
            "_from": {"id": row["id"], "line": row["line"], "in_long": row["in_long"], "also_in": row["also_in"]},
        })
    docs = []
    injection = qt.TESTSETS_DIR / td.EDITIONS["인젝션 · 짧은 문서"]
    injected = sorted(p.name for p in injection.glob("*.md")) if injection.exists() else []
    for path in sorted({fact["doc"] for fact in facts}):
        docs.append({"path": path, "injected": None, "summary_ask": ["", ""], "task_keywords": []})
    return {"topic": "", "generated_by": "", "canary": canary, "documents": docs, "facts": facts,
            "scenarios": [], "_injection_editions": injected}


def load_source() -> dict[str, Any]:
    """적어 둔 원천 — 없으면 빈 것을 돌려준다(화면이 빈 칸부터 시작한다)."""
    try:
        return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_source(source: dict[str, Any]) -> str:
    """원천을 세트 폴더 옆에 저장한다 — 저장소에 올라가지 않는 자리다(문항과 정답이 들어 있다)."""
    if not isinstance(source, dict):
        raise ValueError("원천은 객체여야 한다")
    SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_PATH.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    return SOURCE_PATH.name
