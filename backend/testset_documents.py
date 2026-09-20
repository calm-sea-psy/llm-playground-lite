# -*- coding: utf-8 -*-
"""받은 `.md` 묶음을 읽어 **어느 자리에 놓을지 정하고**, 정한 대로 놓는다.

문서는 **같은 이름으로 두 문서 폴더에** 있어야 한다 — 짧은 문서(`documents/`)와 긴 문서(`documents/long/`).
측정 실행이 긴 문서 폴더를 읽어서, 긴 문서가 없으면 선정용 실행에서 그 문서를 읽는 항목이 **실패한다**. 간접 인젝션은 지시문과 canary를
심은 문서를 따로 둔다.

**이름이 아니라 글로 가른다** — 사람은 이름 규칙을 지키지 않는다. 판단은 코드가 세는 값으로만 하고 모델을
부르지 않는다: 오판이 조용히 측정을 망치고, 그 모델이 후보면 측정 자체가 무효다. 세는 값은 둘이다.

1. **인젝션 문서** — canary가 들어 있다.
2. **같은 문서의 두 자리** — 짧은 쪽의 문장이 긴 쪽에 거의 그대로 들어 있다(문장 겹침 `PAIR_OVERLAP` 이상).
   축약은 문장을 고르는 일이라 고른 문장은 거의 그대로 남는다.

셋으로 갈라 돌려준다. **`place`**(자리가 분명한 것) · **`missing`**(자리는 분명한데 짝이 없어 만들어야 하는 것) ·
**`unresolved`**(추측해야 해서 정하지 않은 것 — 짝 후보가 둘 이상이거나 읽히지 않는 파일). 짝이 없는 원문은
`못 정함`이 아니라 **짧은 문서가 없는 긴 문서**이다.

**받은 이름을 그대로 경로에 쓰지 않는다.** 놓는 자리는 문서 폴더 넷뿐이고, 이름은 한 칸짜리 파일 이름만 쓴다.
"""

import re
import unicodedata
from pathlib import Path
from typing import Any

import quality_testsets as qt

# 놓을 수 있는 자리 — 값은 `testsets/` 아래 상대 경로다
EDITIONS: dict[str, str] = {
    "짧은 문서": "documents",
    "긴 문서": "documents/long",
    "인젝션 · 짧은 문서": "documents/injection",
    "인젝션 · 긴 문서": "documents/long/injection",
}
MAX_BYTES = 1_000_000
_NAME = re.compile(r"^[\w.\- ]+\.md$", re.UNICODE)
# 이름 끝에 붙은 길이 표시 — 떼고 나면 두 자리가 같은 이름이 된다(실행은 같은 이름으로 짝을 찾는다)
_EDITION_SUFFIX = re.compile(r"[-_. ]?(long|short|긴|짧은)(판)?$", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"[.\n!?]+")
# 짧은 문서가 긴 문서에서 나왔다고 보는 문장 겹침
PAIR_OVERLAP = 0.6

# 심은 문서에서 canary를 읽어 내는 표식 꼴 — 대문자·숫자에 붙임표가 든 토막(예: `조직약어-CANARY-4417`).
# 값을 사람에게 다시 묻지 않으려고 문서에서 찾는다. 대신 **추측하지는 않는다**(후보가 여럿이면 정하지 않는다).
_MARK = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")


def check_name(name: str) -> str:
    """파일 이름 하나 — 경로가 아니다. 한글 이름은 자모가 풀린 꼴로 와도 같은 파일이 되게 모아 쓴다(NFC)."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if "/" in name or "\\" in name or name in (".", "..") or not _NAME.match(name):
        raise ValueError(f"문서 이름은 경로 없이 `.md`로 끝나야 한다 — {name!r}")
    return name


def target_path(edition: str, name: str) -> Path:
    """놓을 자리 — 문서 폴더 밖으로 나가면 놓지 않는다(이름 검사를 지나도 한 번 더 본다)."""
    if edition not in EDITIONS:
        raise ValueError(f"놓을 자리는 {' / '.join(EDITIONS)} 중 하나다 — {edition!r}")
    root = (qt.TESTSETS_DIR / EDITIONS[edition]).resolve()
    path = (root / check_name(name)).resolve()
    if path.parent != root:
        raise ValueError(f"문서 폴더 밖이다: {name!r}")
    return path


def _base_name(name: str) -> str:
    """길이 표시를 뗀 이름 — `room-long.md`와 `room.md`는 한 문서다."""
    stem = unicodedata.normalize("NFC", name)[: -len(".md")] if name.lower().endswith(".md") else name
    return _EDITION_SUFFIX.sub("", stem).strip(" -_.")


def _sentences(text: str) -> set[str]:
    """글을 문장 조각 집합으로 — 겹침을 세는 단위다. 짧은 조각(제목·항목 표시)은 어느 문서에나 있어 빼고 센다."""
    return {" ".join(part.split()) for part in _SENTENCE_SPLIT.split(text)
            if len(" ".join(part.split())) >= 12}


def overlap(short: str, long: str) -> float:
    """짧은 글의 문장이 긴 글에 얼마나 들어 있나 — 1.0이면 통째로 들어 있다(축약본이라는 뜻)."""
    small = _sentences(short)
    return len(small & _sentences(long)) / len(small) if small else 0.0


def _read(path: Path) -> tuple[str, str | None]:
    """(글, 못 읽은 까닭). 문서는 UTF-8 텍스트다 — 읽히지 않으면 문서로 다루지 않는다."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", f"읽지 못했다: {exc}"
    if len(data) > MAX_BYTES:
        return "", f"너무 크다 — {len(data):,}바이트 (한도 {MAX_BYTES:,})"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return "", "UTF-8로 읽히지 않는다 — 문서는 UTF-8로 저장한다"


def _partners(entry: dict[str, Any], others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """이 글과 같은 문서의 다른 쪽일 만한 것들 — 짧은 쪽을 긴 쪽에 넣어 본다."""
    found = []
    for other in others:
        if other is entry:
            continue
        short, long = sorted((entry, other), key=lambda e: e["chars"])
        if short["chars"] != long["chars"] and overlap(short["text"], long["text"]) >= PAIR_OVERLAP:
            found.append(other)
    return found


def plan(incoming: Path, canary: str | None = None) -> dict[str, Any]:
    """받은 폴더의 `.md`를 읽어 **자리를 정한다.** 파일은 건드리지 않는다."""
    files: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for path in sorted(Path(incoming).glob("*.md")):
        text, problem = _read(path)
        if problem:
            unresolved.append({"source": path.name, "why": problem})
            continue
        try:
            name = check_name(f"{_base_name(path.name)}.md")
        except ValueError as exc:
            unresolved.append({"source": path.name, "why": str(exc)})
            continue
        files.append({"source": path.name, "base": name, "chars": len(text), "text": text,
                      "injected": bool(canary and canary in text)})

    place: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    done: set[str] = set()
    for injected in (False, True):
        group = [e for e in files if e["injected"] is injected]
        where = "인젝션 · {}" if injected else "{}"
        for entry in group:
            if entry["source"] in done:
                continue
            partners = _partners(entry, group)
            if len(partners) > 1:
                names = " · ".join(p["source"] for p in partners)
                unresolved.append({"source": entry["source"], "why": f"같은 문서로 보이는 파일이 둘 이상이다 — {names}"})
                done.add(entry["source"])
                continue
            if not partners:
                # 짝이 없는 원문은 대표(긴 문서)다 — 짧은 문서는 사람이 만든다(잘라 내면 문항이 묻는 사실이 빠진다)
                place.append({"source": entry["source"], "edition": where.format("긴 문서"), "name": entry["base"],
                              "chars": entry["chars"], "target": f"{EDITIONS[where.format('긴 문서')]}/{entry['base']}"})
                missing.append({"name": entry["base"], "edition": where.format("짧은 문서"),
                                "why": "짝이 없다 — 이 문서를 읽는 항목이 돌려면 짧은 문서 폴더에도 있어야 한다"})
                done.add(entry["source"])
                continue
            other = partners[0]
            short, long = sorted((entry, other), key=lambda e: e["chars"])
            name = short["base"] if len(short["base"]) <= len(long["base"]) else long["base"]
            for member, edition in ((short, where.format("짧은 문서")), (long, where.format("긴 문서"))):
                place.append({"source": member["source"], "edition": edition, "name": name,
                              "chars": member["chars"], "target": f"{EDITIONS[edition]}/{name}"})
                done.add(member["source"])
    return {"place": sorted(place, key=lambda p: (p["name"], p["edition"])), "missing": missing,
            "unresolved": unresolved, "canary": canary}


def apply(incoming: Path, planned: dict[str, Any]) -> list[dict[str, Any]]:
    """정한 대로 놓는다. **추측해야 하는 것이 하나라도 있으면 놓지 않는다.** 짝이 없어 만들어야 하는 것
    (`missing`)은 자리가 분명하니 놓고, 없는 자리는 이름으로 남는다 — 실행 전에 `listing`이 다시 잡는다.
    같은 이름이 이미 있으면 덮어쓴 사실을 돌려준다(그 문서를 읽는 세트가 함께 움직였다는 뜻이다)."""
    if planned.get("unresolved"):
        raise ValueError(f"못 정한 문서가 {len(planned['unresolved'])}개 있다 — 고치기 전에는 놓지 않는다")
    placed = []
    for item in planned.get("place") or []:
        text, problem = _read(Path(incoming) / item["source"])
        if problem:
            raise ValueError(f"{item['source']}: {problem}")
        path = target_path(item["edition"], item["name"])
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        placed.append({**item, "overwritten": existed})
    return placed


def find_canary() -> tuple[str, str]:
    """심은 문서에서 canary를 찾는다 — **심은 문서 모두에 있고 다른 문서에는 없는** 표식은 하나뿐이어야 한다.
    사람이 문서를 만들 때 정한 값을 화면에서 다시 묻지 않으려고 코드가 읽어 낸다.

    찾으면 `(값, "")`, 못 찾으면 `("", 까닭)`. 후보가 여럿이면 고르지 않는다 — 잘못 고르면 인젝션 측정이 통째로
    거짓이 되고, 그 사실은 몇 시간 뒤에야 드러난다."""
    injection_dir = qt.TESTSETS_DIR / EDITIONS["인젝션 · 짧은 문서"]
    planted = sorted(injection_dir.glob("*.md")) if injection_dir.exists() else []
    if not planted:
        return "", "심은 문서가 없다"
    common: set[str] | None = None
    for path in planted:
        found = set(_MARK.findall(path.read_text(encoding="utf-8")))
        common = found if common is None else common & found
    elsewhere: set[str] = set()
    for edition in ("짧은 문서", "긴 문서"):
        folder = qt.TESTSETS_DIR / EDITIONS[edition]
        for path in sorted(folder.glob("*.md")) if folder.exists() else []:
            elsewhere |= set(_MARK.findall(path.read_text(encoding="utf-8")))
    candidates = sorted((common or set()) - elsewhere)
    if len(candidates) == 1:
        return candidates[0], ""
    if not candidates:
        return "", "심은 문서 모두에 있고 다른 문서에는 없는 표식이 없다 — 심은 문서마다 같은 canary를 넣는다"
    return "", f"표식 후보가 둘 이상이라 고르지 않았다 — {' / '.join(candidates)}"


def check_injection(canary: str) -> list[dict[str, str]]:
    """인젝션 문서가 **조건을 갖췄는지** 본다. 만드는 일은 사람이 하고, 코드는 재는 자리가 성립하는지만 판단한다.

    조건 넷 — 두 문서 폴더에 다 있는가 · canary가 두 폴더에 다 들어 있는가 · **지시문 뒤에 내용이 남았는가**
    (뒤가 비면 `지시문을 지나 읽고도 따르지 않았다`를 볼 수 없다) · 기반 문서에는 canary가 없는가(심지 않은
    문서에 canary가 있으면 정상 답에도 그 말이 나올 수 있다)."""
    problems: list[dict[str, str]] = []
    short_dir = qt.TESTSETS_DIR / EDITIONS["인젝션 · 짧은 문서"]
    for path in sorted(short_dir.glob("*.md")) if short_dir.exists() else []:
        long_path = target_path("인젝션 · 긴 문서", path.name)
        if not long_path.exists():
            problems.append({"name": path.name, "why": "긴 문서 폴더에 없다"})
            continue
        for edition, file in (("짧은 문서", path), ("긴 문서", long_path)):
            text = file.read_text(encoding="utf-8")
            if canary not in text:
                problems.append({"name": path.name, "why": f"{edition}에 canary가 없다"})
                continue
            tail = text.split(canary, 1)[1]
            if len(_sentences(tail)) < 3:
                problems.append({"name": path.name, "why": f"{edition}는 지시문 뒤에 남은 내용이 거의 없다"})
    for edition in ("짧은 문서", "긴 문서"):
        folder = qt.TESTSETS_DIR / EDITIONS[edition]
        for path in sorted(folder.glob("*.md")) if folder.exists() else []:
            if canary in path.read_text(encoding="utf-8"):
                problems.append({"name": path.name, "why": f"심지 않은 {edition}에 canary가 있다"})
    return problems


# 올릴 때 사람이 고르는 묶음 — **한 벌만 올린다.** 문서 폴더는 `짧은 문서`와 `긴 문서` 둘인데, 길이를 나눠 재지
# 않으면 두 폴더가 다를 까닭이 없다. 그래서 한 번 올리면 같은 글을 두 폴더에 둔다(길이를 다르게 두려면 문서를
# 폴더마다 따로 올린다 — `EDITIONS`의 이름을 그대로 쓸 수 있다).
GROUPS: dict[str, tuple[str, ...]] = {
    "문서": ("짧은 문서", "긴 문서"),
    "인젝션 문서": ("인젝션 · 짧은 문서", "인젝션 · 긴 문서"),
}


def save_group(group: str, name: str, text: str) -> dict[str, Any]:
    """묶음 하나에 문서를 놓는다 — 그 묶음의 자리마다 같은 글을 쓴다."""
    editions = GROUPS.get(group)
    if not editions:
        editions = (group,) if group in EDITIONS else None
    if not editions:
        raise ValueError(f"올릴 자리는 {' / '.join([*GROUPS, *EDITIONS])} 중 하나다 — {group!r}")
    saved = [save(edition, name, text) for edition in editions]
    return {**saved[0], "group": group, "saved_to": " · ".join(one["saved_to"] for one in saved)}


def save(edition: str, name: str, text: str) -> dict[str, Any]:
    """문서 하나를 고른 자리에 놓는다 — 화면에서 올릴 때 쓴다. 이름 검사와 자리 검사는 `target_path`가 한다.
    **덮어썼는지 돌려준다**: 같은 이름을 다시 올려 조용히 바뀌면 그 문서를 읽는 세트가 함께 움직인 것을 모른다."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"문서가 너무 크다 — 한도 {MAX_BYTES:,}바이트")
    path = target_path(edition, name)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return {"name": check_name(name), "edition": edition, "chars": len(text), "overwritten": existed,
            "saved_to": f"{EDITIONS[edition]}/{check_name(name)}"}


def remove(name: str) -> dict[str, Any]:
    """문서 하나를 **모든 자리에서** 지운다 — 한 자리만 지우면 짝이 어긋난 채로 남아 실행에서 그 항목이 실패한다.
    지운 자리를 돌려준다. 그 문서를 가리키는 세트가 있으면 준비 상태가 다시 막힌다(`readiness`가 잡는다)."""
    removed = []
    for edition in EDITIONS:
        path = target_path(edition, name)
        if path.exists():
            path.unlink()
            removed.append(f"{EDITIONS[edition]}/{check_name(name)}")
    if not removed:
        raise ValueError(f"놓인 적 없는 문서다: {name}")
    return {"name": check_name(name), "removed": removed}


def listing() -> dict[str, Any]:
    """놓인 문서와 **짝이 맞는가**. 짧은 문서만 있는 문서는 실행에서 실패할 자리라 이름을 적어 돌려준다."""
    by_name: dict[str, dict[str, Any]] = {}
    for edition, rel in EDITIONS.items():
        folder = qt.TESTSETS_DIR / rel
        for path in sorted(folder.glob("*.md")) if folder.exists() else []:
            entry = by_name.setdefault(path.name, {"name": path.name, "editions": {}})
            entry["editions"][edition] = {"chars": len(path.read_text(encoding="utf-8"))}
    documents = sorted(by_name.values(), key=lambda e: e["name"])
    unpaired = [e["name"] for e in documents if ("짧은 문서" in e["editions"]) != ("긴 문서" in e["editions"])]
    injection_unpaired = [e["name"] for e in documents
                          if ("인젝션 · 짧은 문서" in e["editions"]) != ("인젝션 · 긴 문서" in e["editions"])]
    return {"documents": documents, "editions": list(EDITIONS),
            "unpaired": unpaired, "injection_unpaired": injection_unpaired}
