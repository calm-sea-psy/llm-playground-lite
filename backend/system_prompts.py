"""시스템 프롬프트 — 폴더 기반 파일, 두 페이지 공용, 화면에서 편집.

`backend/prompts/`의 `.txt`/`.md` 파일을 스캔해 목록과 내용을 돌려주고, 화면에서 만든
프롬프트를 저장·삭제한다. 캐싱은 안 한다(매번 새로 스캔).

**파일 형식** — 제목은 본문과 분리해 YAML frontmatter의 `title`로 둔다. 본문 첫 줄에 `# 제목`을
쓰면 그 줄이 모델에게 그대로 전달돼 조건이 오염된다. 파일명은 식별자(slug), `title`은 화면에
보이는 이름, **모델에게 가는 것은 frontmatter를 뺀 본문뿐**이다. frontmatter는 `title` 한 줄만
쓰므로 YAML 파서를 들이지 않고 여기서 읽는다.

**경로 안전** — 읽기만 할 때는 고정 폴더를 훑어 경로를 밖에서 받지 않았지만, 저장·삭제는 이름을
밖에서 받는다. slug를 허용 문자로 제한하고, 최종 경로가 고정 폴더 안인지 다시 확인하며,
확장자는 `.txt`로 고정한다.
"""

import hashlib
import re
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"
_EXTENSIONS = (".txt", ".md")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


class InvalidPromptName(ValueError):
    pass


def parse(raw: str) -> tuple[str | None, str]:
    """(title, body). frontmatter가 없으면 제목 없음 — 본문은 그대로다."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None, raw.strip()
    title = None
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "title":
            title = value.strip().strip('"').strip("'") or None
    return title, raw[m.end():].strip()


def content_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _entry(path: Path) -> dict[str, Any]:
    title, body = parse(path.read_text(encoding="utf-8"))
    return {"name": path.stem, "title": title or path.stem, "content": body, "sha256": content_sha256(body)}


def list_prompts() -> list[dict[str, Any]]:
    if not PROMPTS_DIR.exists():
        return []
    out = []
    for path in sorted(PROMPTS_DIR.iterdir()):
        if path.suffix.lower() not in _EXTENSIONS or not path.is_file():
            continue
        out.append(_entry(path))
    return out


def _path_for(name: str, *, must_exist: bool) -> Path:
    if not isinstance(name, str) or not _SLUG_RE.match(name):
        raise InvalidPromptName("이름은 영문 소문자·숫자·하이픈만(첫 글자는 영문 소문자나 숫자, 64자 이내) 쓸 수 있습니다")
    root = PROMPTS_DIR.resolve()
    if must_exist:
        for ext in _EXTENSIONS:
            candidate = (PROMPTS_DIR / f"{name}{ext}").resolve()
            if candidate.parent == root and candidate.is_file():
                return candidate
        raise FileNotFoundError(name)
    candidate = (PROMPTS_DIR / f"{name}.txt").resolve()
    if candidate.parent != root:
        raise InvalidPromptName("프롬프트 폴더 밖을 가리키는 이름입니다")
    return candidate


def get_prompt(name: str) -> dict[str, Any]:
    return _entry(_path_for(name, must_exist=True))


def save(name: str, title: str, content: str) -> dict[str, Any]:
    """새로 만들거나 덮어쓴다. 기존 파일이 `.md`면 그 파일을 고친다(같은 이름 두 벌을 만들지 않는다)."""
    try:
        path = _path_for(name, must_exist=True)
    except FileNotFoundError:
        path = _path_for(name, must_exist=False)
    title = " ".join((title or "").split()) or name
    body = (content or "").strip()
    if not body:
        raise ValueError("본문이 비어 있습니다")
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace('"', "'")
    path.write_text(f'---\ntitle: "{safe_title}"\n---\n{body}\n', encoding="utf-8")
    return _entry(path)


def delete(name: str) -> None:
    _path_for(name, must_exist=True).unlink()
