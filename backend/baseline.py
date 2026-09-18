"""클라우드 베이스라인 + 모든 실행이 남기는 세트 지문.

절대 기준점을 `gpt-5.6-luna`로 한 번만 돌려 `backend/test-results/baseline/`에
저장해두고 비교 화면에서 계속 재사용한다 — 외부 API라 호출마다 비용이 들어
**1회 원칙**이다. 저장해두고 쓰는 값이라 **조용히 낡는 것**이 가장 큰 위험이고,
그걸 자동으로 판정하는 것이 이 모듈의 핵심이다.

## 지문 — 무엇을 해시하나

- **경계는 "측정을 바꾸는 것"까지다. 응답을 읽는 방법은 밖이다.** 문항·도구
  스펙·주입 문구가 바뀌면 다시 재야 하지만, 채점 로직이나 채점 데이터
  (`refusal_expressions.json`, 세트 안의 `key_points`·정답·통과 문구)가 바뀌면
  **재채점으로 충분하다** — 그걸 낡음으로 띄우면 원칙이 스스로를 반증한다.
- **세트 JSON은 필드 단위로 거른다.** 한 파일에 질문과 채점 데이터가 섞여 있어서다.
  구현은 **제외 목록**이다: 새로 생긴 프롬프트 필드를 해시에서 빠뜨리면 조용히
  다른 조건으로 비교하게 되지만, 새 채점 필드를 제외에서 빠뜨리면 가짜 경고가
  떠서 바로 보인다 — 놓쳤을 때 보이는 쪽으로 기울여 실패한다.
- **파일만이 아니다.** `tool_calling` 범위는 코드 안 상수(주입 문구·프로브 변형)와
  **모델에 전달되는 도구 스펙을 도구 이름별 키로** 해시한다. 키 집합이 다르면
  도구 목록이 바뀐 것(키 설정), 같은 키의 해시가 다르면 스펙 문구가 바뀐 것이다.
- **고정된 것만** — 현재 시각이나 공휴일 조회 결과처럼 세상이 준 값은 넣지 않는다. 측정용으로 **기록해 둔**
  도구 응답(`tool_calling_fixtures.json`)은 고정값이라 넣되, 세트 해시에 섞지 않고 `fixture:` 키로 따로 둔다 —
  세트 문항이 바뀐 것과 도구 고정값이 바뀐(또는 생긴) 것은 다른 사실이고, 경고가 무엇이 바뀌었는지를 말해야 한다.

## 지문 — 어떻게 저장하나

`{"rules": {"<규칙 버전>": {범위: {키: 해시}}}, "upgrade_failed": {...}}`.

- **범위 안은 항목별 맵이다.** 기준선은 일관성을 재지 않아 `consistency.json`이
  빠지는데, 범위마다 해시 하나면 기준선과 후보가 **항상 불일치**가 된다.
- **규칙 버전을 함께 둔다.** 제외 목록에 필드를 넣는 순간 세트는 그대로인데
  해시가 전부 달라진다 — 세는 규칙이 바뀐 것을 `불일치`로 적으면 거짓말이다.
- **승급은 앱 시작 시 한 번에 한다**(`upgrade_all`). 현재 파일을 옛 규칙으로 다시
  해시해 저장값과 같으면 그 뒤로 안 바뀐 것이므로 새 규칙 해시를 **덧붙인다**.
  이 조건은 파일이 옛 실행 때와 같을 동안만 성립하는 한시적 창이라(세트도 결과도
  git 밖이라 복원할 이력이 없다), 규칙을 바꾸면 **승급을 먼저 끝내고 세트를 고친다.**
  옛 규칙 정의는 그래서 지우지 않는다.
- **실행 시작 시점에 찍는다** — 저장 시점에 계산하면 수십 분 도는 동안 세트를
  고쳤을 때 편집 후 값이 남는다.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import agent_tools
import bench_config as cfg
import quality_runner
import quality_testsets as qt
import tool_calling_runner

TESTSETS_DIR = qt.TESTSETS_DIR  # 실제 세트가 없으면 공개 샘플 세트 폴더다
RESULTS_DIR = Path(__file__).parent / "test-results"
BASELINE_DIR = RESULTS_DIR / "baseline"

BASELINE_MODEL = "gpt-5.6-luna"

QUALITY = "quality"
SPEED = "speed"
TOOL_CALLING = "tool_calling"
FINGERPRINT_SCOPES = (QUALITY, SPEED, TOOL_CALLING)

# 실행 종류별로 남기는 범위 — 베이스라인은 품질만 잰다.
SCOPES_FOR_RUN = {"baseline": (QUALITY,), "full": FINGERPRINT_SCOPES,
                  # 과제용 부분 실행 — 로컬은 워밍업에 속도 탐침을 쓰고, 클라우드는 품질 세트만 읽는다
                  "assignment": (QUALITY, SPEED), "assignment_cloud": (QUALITY,)}

# 실행 종류별로 **선언된 의도적 제외**. 여기 적힌 부재만 조용히 넘기고, 선언되지
# 않은 부재는 신호다. 기준선은 일관성/재현성을 재지 않는다 —
# 이 지표만 temperature 0.7로 일부러 흔드는데 클라우드 경로는 샘플링을 못 받고,
# 글자 단위 유사도가 길이에 편향되는데 기준선 응답이 5~6배 짧아 비교가 성립하지 않는다.
DECLARED_EXCLUSIONS: dict[str, set[str]] = {"baseline": {"set:consistency.json"}}

MATCH = "일치"
MISMATCH = "불일치"
UNRECORDED = "기록 없음"
RULE_DIFFERS = "비교 불가(규칙 다름)"


@dataclass(frozen=True)
class RuleSet:
    """세트 JSON에서 해시하지 않을 **채점 데이터** 필드. 경로 문법: 최상위 키는
    `"key"`, 목록 원소의 키는 `"items[].key"`. `"*"`는 모든 세트 파일에 적용한다."""

    excluded_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)


# 규칙 정의는 **지우지 않는다** — 승급이 옛 규칙으로 다시 해시할 수 있어야 한다.
RULES: dict[int, RuleSet] = {
    1: RuleSet(
        excluded_fields={
            "*": ("notes",),  # 문서용 설명 — 초기 항목으로 넣어둔다(넣는 순간 전 실행이 흔들리는 것을 피해)
            "closed_qa.json": ("items[].answer", "items[].allowed_forms"),
            "key_coverage.json": ("items[].key_points",),
            "hallucination.json": ("items[].scoring", "items[].type"),
            "instruction_following.json": ("items[].scoring",),
            # 스키마 전문은 variant 문장 안에 들어가 모델에게 가고, 이 필드는 검증용이다
            "structured_output.json": ("items[].schema",),
            # canary 값은 system_prompt·주입 문서 안에도 그대로 있어 그쪽이 해시된다
            "injection_direct.json": ("canary", "items[].task_keywords"),
            # 지시문 뒤 내용 목록도 답을 읽는 데만 쓰고 모델에게 가지 않는다
            "injection_indirect.json": (
                "canary",
                "items[].task_keywords",
                "items[].after_instruction_facts",
                "items[].after_instruction_facts_long",
            ),
            "prompt_leak.json": ("canary",),
            "long_context.json": ("scenarios[].recall_checks", "scenarios[].constraint"),
            "tool_calling.json": (
                "scoring",
                "basic_items[].expected_tool",
                "basic_items[].expected_args",
                "basic_items[].category",
                "basic_items[].note",
                "advanced_items[].expected_tool",
                "advanced_items[].expected_args",
                "advanced_items[].pass_phrases",
                "advanced_items[].fabrication_keywords",
                "advanced_items[].category",
                "advanced_items[].note",
            ),
        }
    ),
}
CURRENT_RULE = max(RULES)


# ---------------------------------------------------------------------------
# 해시 계산
# ---------------------------------------------------------------------------


def _scope_set_files(scope: str) -> list[str]:
    if scope == QUALITY:
        return sorted(set(cfg.QUALITY_TESTSET_FILES.values()))
    if scope == SPEED:
        return ["probe.json"]
    return ["tool_calling.json"]


def _strip(data: Any, path: list[str]) -> None:
    head, rest = path[0], path[1:]
    if head.endswith("[]"):
        items = data.get(head[:-2]) if isinstance(data, dict) else None
        for element in items or []:
            if isinstance(element, dict):
                _strip(element, rest)
        return
    if not isinstance(data, dict):
        return
    if rest:
        _strip(data.get(head), rest)
    else:
        data.pop(head, None)


def _filtered_set_bytes(name: str, raw: bytes, rule: int) -> bytes:
    """채점 데이터 필드를 뺀 뒤 결정적으로 직렬화한다. JSON이 아니면 원문 그대로."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    rules = RULES[rule].excluded_fields
    for spec in (*rules.get("*", ()), *rules.get(name, ())):
        _strip(data, spec.replace("[].", "[] ").split(" ") if "[]." in spec else [spec])
    return json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hash_obj(obj: Any) -> str:
    # 키 순서나 순회 순서로 지문이 흔들리면 아무것도 안 바뀌었는데 경고가 뜬다
    return _hash_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"))


FIXTURES_FILE = tool_calling_runner.FIXTURES_PATH.name  # 이름은 도구 호출 실행부가 한 번만 정한다


def _fixture_hash(path: Path) -> str:
    """도구 고정값의 해시 — `notes`(기록 시각·손으로 고친 까닭)는 빼고 값만 센다. 설명을 고쳤다고 고정값이 바뀐 것이 아니다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("notes", None)
    return _hash_obj(data)


def _tool_specs() -> dict[str, dict[str, Any]]:
    # 도구 고정값이 있으면 측정 경로에서 고정값 도구는 키 없이도 모델에게 간다 — 모델에게 가는 목록과 같은 목록을 센다
    fixed = (TESTSETS_DIR / FIXTURES_FILE).exists()
    with agent_tools.fixed_responses({} if fixed else None):
        specs = {t.name: t.openai_spec() for t in agent_tools.list_ready_tools("test")}
    specs[agent_tools.INJECTION_PROBE_TOOL.name] = agent_tools.INJECTION_PROBE_TOOL.openai_spec()
    return specs


def _set_path(name: str) -> Path:
    """세트 파일의 자리. 속도 탐침은 세트 폴더에 없으면 저장소에 든 한 벌이다 — 샘플 세트로 돌아도 같은 탐침을 읽는다."""
    path = TESTSETS_DIR / name
    return qt.PROBE_PATH if name == "probe.json" and not path.exists() else path


def compute_map(scope: str, rule: int | None = None, document_length: str = qt.DOCUMENTS_SHORT) -> dict[str, str]:
    """범위 하나의 `{키: 해시}`. 없는 파일은 키 자체가 없다(= 한쪽 전용 키 신호).
    참조 문서는 **그 문서 길이의 판만** 센다 — 읽지 않는 판까지 세면 긴 판을 더하는 것만으로 짧은 문서로 잰 실행들이
    `참조 문서 추가`로 낡는다. 짧은 판은 폴더 안에 있는 긴 판 폴더를 뺀다."""
    rule = CURRENT_RULE if rule is None else rule
    if scope not in FINGERPRINT_SCOPES:
        raise ValueError(f"알 수 없는 지문 범위: {scope}")
    out: dict[str, str] = {}
    for name in _scope_set_files(scope):
        path = _set_path(name)
        if path.exists():
            out[f"set:{name}"] = _hash_bytes(_filtered_set_bytes(name, path.read_bytes(), rule))
    if scope == QUALITY:
        documents_dir = TESTSETS_DIR / qt.DOCUMENT_DIRS[qt.check_document_length(document_length)]
        # 이 판의 폴더 **안에** 있는 다른 판 폴더만 뺀다(짧은 판 폴더 안의 `long/`) — 바깥 폴더까지 빼면 긴 판이 통째로 빠진다
        others = [TESTSETS_DIR / d for length, d in qt.DOCUMENT_DIRS.items()
                  if length != document_length and documents_dir in (TESTSETS_DIR / d).parents]
        if documents_dir.exists():
            for p in sorted(documents_dir.rglob("*")):
                if p.is_file() and not any(other in p.parents for other in others):
                    out[f"doc:{p.relative_to(TESTSETS_DIR).as_posix()}"] = _hash_bytes(p.read_bytes())
    if scope == TOOL_CALLING:
        if (TESTSETS_DIR / FIXTURES_FILE).exists():
            out[f"fixture:{FIXTURES_FILE}"] = _fixture_hash(TESTSETS_DIR / FIXTURES_FILE)
        out["const:INJECTION_PROBE_NOTE"] = _hash_obj(agent_tools.INJECTION_PROBE_NOTE)
        out["const:INJECTION_PROBE_VARIANTS"] = _hash_obj(tool_calling_runner.INJECTION_PROBE_VARIANTS)
        for name, spec in _tool_specs().items():
            out[f"tool:{name}"] = _hash_obj(spec)
    return out


def compute_run_fingerprints(run_scope: str, document_length: str = qt.DOCUMENTS_SHORT) -> dict[str, Any]:
    """실행 시작 시점에 한 번 부른다. 그 실행 종류가 재는 범위만, 선언된 제외는 빼고, 그 실행이 읽는 문서 판으로."""
    declared = DECLARED_EXCLUSIONS.get(run_scope, set())
    maps = {
        scope: {k: v for k, v in compute_map(scope, document_length=document_length).items() if k not in declared}
        for scope in SCOPES_FOR_RUN.get(run_scope, FINGERPRINT_SCOPES)
    }
    return {"rules": {str(CURRENT_RULE): maps}}


def _rules_of(entry: dict[str, Any]) -> dict[int, dict[str, dict[str, str]]]:
    """저장된 규칙별 맵. 옛 단일 문자열 지문(`testset_fingerprint`)은 해시 하나라
    항목별 맵으로 옮길 수 없다 — 없는 것과 같다(`기록 없음`)."""
    rules = ((entry or {}).get("fingerprints") or {}).get("rules") or {}
    return {int(k): v for k, v in rules.items()}


# ---------------------------------------------------------------------------
# 비교
# ---------------------------------------------------------------------------

_KIND_LABELS = {"set": "세트 파일", "doc": "참조 문서", "const": "프로브 상수", "tool": "도구", "fixture": "도구 고정값"}


def key_kind(key: str) -> str:
    return key.split(":", 1)[0]


def _one_sided_note(key: str, *, present_in: str) -> dict[str, Any]:
    """한쪽에만 있는 키의 뜻과 조치 — 키 종류마다 다르고, 조치는 두 맥락으로 나뉜다
    (다시 잴 수 있는 현재 세트 대조 / 과거 실행을 대조하는 리포트 표지)."""
    kind = key_kind(key)
    name = key.split(":", 1)[1]
    if kind == "set":
        meaning = f"지표 {'추가' if present_in == 'right' else '제거'} ({name})"
        remeasure, report = "재측정", "비교 가능성 경고"
    elif kind == "doc":
        meaning = f"참조 문서 {'추가' if present_in == 'right' else '제거'} ({name}) — 지표가 늘어난 것은 아니다"
        remeasure, report = "그 문서를 쓰는 문항이 있을 때만 재측정", "해당 문항이 있을 때만 경고"
    elif kind == "const":
        meaning = f"프로브 문항 구성 변화 ({name})"
        remeasure, report = "재측정", "비교 가능성 경고"
    elif kind == "fixture":
        # 세트 문항은 그대로다 — 한쪽은 기록된 도구 응답으로, 다른 쪽은 실시간 도구 응답으로 쟀다
        meaning = (f"도구 고정값 {'생김' if present_in == 'right' else '없어짐'} ({name}) — 한쪽은 실시간 도구 응답으로 쟀다, "
                   "세트 문항이 바뀐 것은 아니다")
        remeasure, report = "tool-calling 지표에 한해 재측정", "tool-calling 지표에 비교 가능성 경고"
    else:
        meaning = f"도구 목록 변화 — {name} {'추가' if present_in == 'right' else '빠짐'} (키 설정을 되돌리면 같은 조건)"
        remeasure, report = "tool-calling 지표에 한해 재측정", "tool-calling 지표에 비교 가능성 경고"
    return {"key": key, "kind": kind, "meaning": meaning, "remeasure": remeasure, "report": report}


def _changed_note(key: str) -> dict[str, Any]:
    kind = key_kind(key)
    name = key.split(":", 1)[1]
    meaning = {
        "set": f"세트 내용 변경 ({name})",
        "doc": f"참조 문서 본문 변경 ({name})",
        "const": f"프로브 문구 변경 ({name})",
        "tool": f"도구 스펙 문구가 바뀜 ({name})",
        "fixture": f"도구 고정값이 바뀜 ({name}) — 세트 문항이 바뀐 것은 아니다",
    }[kind]
    report = "tool-calling 지표에 비교 가능성 경고" if kind in ("tool", "fixture") else "비교 가능성 경고"
    return {"key": key, "kind": kind, "meaning": meaning, "remeasure": "재측정", "report": report}


def compare_scope(
    left: dict[str, Any], right: dict[str, Any], scope: str
) -> dict[str, Any]:
    """두 실행(결과 dict)의 한 범위를 대조한다. 양쪽이 모두 가진 규칙 버전 중 가장
    높은 것의 맵을 쓴다 — 어느 것을 쓸지 정하지 않으면 같은 두 실행이 비교할
    때마다 다른 답을 낸다."""
    lrules, rrules = _rules_of(left), _rules_of(right)
    base = {"scope": scope, "rule": None, "changed": [], "only_left": [], "only_right": [], "renamed": [], "unverifiable": []}
    lscoped = {v: m[scope] for v, m in lrules.items() if scope in m}
    rscoped = {v: m[scope] for v, m in rrules.items() if scope in m}
    if not lscoped or not rscoped:
        return {**base, "state": UNRECORDED}
    common = sorted(set(lscoped) & set(rscoped))
    if not common:
        return {**base, "state": RULE_DIFFERS}
    rule = common[-1]
    lmap, rmap = lscoped[rule], rscoped[rule]
    unverifiable = set(_upgrade_failed(left, rule, scope)) | set(_upgrade_failed(right, rule, scope))

    ldeclared = DECLARED_EXCLUSIONS.get(left.get("scope", "full"), set())
    rdeclared = DECLARED_EXCLUSIONS.get(right.get("scope", "full"), set())
    only_l = {k: h for k, h in lmap.items() if k not in rmap and k not in rdeclared}
    only_r = {k: h for k, h in rmap.items() if k not in lmap and k not in ldeclared}

    # 한쪽 전용 키가 양쪽에 동시에 있고 해시가 같으면 이름만 바뀐 것이다 —
    # 그대로 두면 "지표 하나 빠지고 하나 늘었다"로 읽힌다.
    renamed = []
    for lk, lh in list(only_l.items()):
        match = next((rk for rk, rh in only_r.items() if rh == lh and key_kind(rk) == key_kind(lk)), None)
        if match:
            renamed.append({"from": lk, "to": match})
            only_l.pop(lk)
            only_r.pop(match)

    changed = [k for k in lmap.keys() & rmap.keys() if lmap[k] != rmap[k] and k not in unverifiable]
    result = {
        **base,
        "rule": rule,
        "changed": [_changed_note(k) for k in sorted(changed)],
        "only_left": [_one_sided_note(k, present_in="left") for k in sorted(only_l)],
        "only_right": [_one_sided_note(k, present_in="right") for k in sorted(only_r)],
        "renamed": renamed,
        "unverifiable": sorted(unverifiable),
    }
    if result["changed"] or result["only_left"] or result["only_right"]:
        state = MISMATCH
    elif unverifiable:
        state = RULE_DIFFERS
    else:
        state = MATCH
    return {**result, "state": state}


def compare_entries(left: dict[str, Any], right: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """**양쪽이 모두 재는 범위에 대해서만** 대조한다 — 기준선에 없는 `speed`를
    불일치로 세면 "안 잰 것"이 "틀린 것"이 된다."""
    lscopes = SCOPES_FOR_RUN.get(left.get("scope", "full"), FINGERPRINT_SCOPES)
    rscopes = SCOPES_FOR_RUN.get(right.get("scope", "full"), FINGERPRINT_SCOPES)
    return {s: compare_scope(left, right, s) for s in FINGERPRINT_SCOPES if s in lscopes and s in rscopes}


def _upgrade_failed(entry: dict[str, Any], rule: int, scope: str) -> list[str]:
    failed = ((entry.get("fingerprints") or {}).get("upgrade_failed") or {}).get(str(rule)) or {}
    return failed.get(scope) or []


# ---------------------------------------------------------------------------
# 승급 — 규칙 버전이 올라간 직후, 저장된 결과 전체에 한 번에
# ---------------------------------------------------------------------------


def _rehash_key(key: str, rule: int) -> str | None:
    """현재 파일(또는 상수)을 주어진 규칙으로 다시 해시한다. 대상이 사라졌으면 None."""
    kind, name = key.split(":", 1)
    if kind == "set":
        path = _set_path(name)
        return _hash_bytes(_filtered_set_bytes(name, path.read_bytes(), rule)) if path.exists() else None
    if kind == "doc":
        path = TESTSETS_DIR / name
        return _hash_bytes(path.read_bytes()) if path.exists() else None
    if kind == "fixture":
        path = TESTSETS_DIR / name
        return _fixture_hash(path) if path.exists() else None
    if kind == "const":
        value = {
            "INJECTION_PROBE_NOTE": agent_tools.INJECTION_PROBE_NOTE,
            "INJECTION_PROBE_VARIANTS": tool_calling_runner.INJECTION_PROBE_VARIANTS,
        }.get(name)
        return _hash_obj(value) if value is not None else None
    spec = _tool_specs().get(name)
    return _hash_obj(spec) if spec is not None else None


def upgrade_entry(entry: dict[str, Any]) -> bool:
    """현재 규칙 맵이 없는 결과에 새 맵을 **덧붙인다**(원본 맵은 그대로 둔다 — 측정은
    불변). 옛 규칙으로 다시 해시한 값이 저장값과 같은 키만 승급하고, 다른 키는
    `upgrade_failed`에 남긴다 — 그 사이 세트가 실제로 바뀐 것이라 진짜 `비교 불가`다.
    바뀌었으면 True."""
    rules = _rules_of(entry)
    if not rules or CURRENT_RULE in rules:
        return False
    old = max(rules)
    new_maps: dict[str, dict[str, str]] = {}
    failed: dict[str, list[str]] = {}
    for scope, stored in rules[old].items():
        new_maps[scope] = {}
        for key, stored_hash in stored.items():
            if _rehash_key(key, old) == stored_hash:
                new_hash = _rehash_key(key, CURRENT_RULE)
                if new_hash is not None:
                    new_maps[scope][key] = new_hash
                    continue
            failed.setdefault(scope, []).append(key)
    fp = entry.setdefault("fingerprints", {})
    fp.setdefault("rules", {})[str(CURRENT_RULE)] = new_maps
    if failed:
        fp.setdefault("upgrade_failed", {})[str(CURRENT_RULE)] = failed
    return True


def upgrade_all() -> int:
    """앱 시작 시 한 번. 결과 파일과 기준선 파일 전체를 훑는다. 승급한 파일 수."""
    count = 0
    for directory in (RESULTS_DIR, BASELINE_DIR):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if upgrade_entry(data):
                _write_json(path, data)
                count += 1
    return count


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# 베이스라인 저장·조회·낡음 판정
# ---------------------------------------------------------------------------


def save(run: dict[str, Any], *, conditions: dict[str, Any]) -> Path:
    """베이스라인 실행 결과를 일반 결과와 분리된 경로에 저장한다. 지문은 실행 시작
    시점에 `test_runner`가 이미 찍어 `run`에 넣어뒀다 — 여기서 다시 계산하지 않는다."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    out = dict(run)
    out["is_baseline"] = True
    out["conditions"] = conditions
    out["measured_at"] = out.get("finished_at") or out.get("started_at")
    path = BASELINE_DIR / f"{out['id']}.json"
    _write_json(path, out)
    return path


def document_length_of(entry: dict[str, Any]) -> str:
    """기준선이 읽은 문서 판 — 기록이 없는 기준선은 긴 판이 생기기 전에 잰 것이라 짧은 판이다."""
    return (entry.get("config") or {}).get("document_length") or qt.DOCUMENTS_SHORT


def latest(document_length: str | None = None) -> dict[str, Any] | None:
    """가장 최근에 **측정한** 베이스라인(`measured_at` 기준). 없으면 None — "베이스라인
    없음"과 "낡음"은 화면에서 다르게 표시해야 한다.

    `document_length`를 주면 **그 문서 길이로 잰 기준선 중에서만** 고른다 — 기준선 열은 후보와 같은 조건이어야 한다.
    가장 최신으로 고르면 문서 길이 비교용으로 다른 판을 한 번 더 잰 순간 그 기준선이 조용히 열을 차지한다. 맞는 것이
    없으면 None이다(아무거나 채우지 않는다).

    파일 수정 시각으로 고르면 안 된다 — 재채점·지문 승급은 측정은 그대로 두고 판정만
    고쳐 쓰는데, 그러면 옛 기준선 파일의 수정 시각이 최신이 되어 옛 측정이 "가장 최근"으로
    뽑힌다(실제로 재채점 직후 09-12 기준선이 09-13 기준선을 밀어냈다)."""
    if not BASELINE_DIR.exists():
        return None
    entries = []
    for path in BASELINE_DIR.glob("*.json"):
        try:
            # 긴 컨텍스트의 옛 모양은 읽을 때 지금 모양으로 — 실행 결과와 같은 변환이다(파일은 그대로)
            entries.append(quality_runner.current_long_context(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    if document_length is not None:
        entries = [e for e in entries if document_length_of(e) == document_length]
    if not entries:
        return None
    return max(entries, key=lambda e: e.get("measured_at") or e.get("finished_at") or e.get("started_at") or "")


def unmatched_reason(document_length: str) -> str:
    return f"조건이 맞는 기준선이 없다 — 후보는 {document_length}로 쟀는데 그 문서 길이로 잰 기준선이 없다"


def staleness(entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """저장된 기준선을 **지금 세트**와 대조한다(다시 잴 수 있는 맥락). `stale`은
    비교 가능한 범위 중 하나라도 `불일치`일 때만 True — `기록 없음`과 `비교 불가`는
    경고가 아니라 "확인 불가"다(모르는 것을 틀린 것으로 표시하지 않는다)."""
    if entry is None:
        entry = latest()
    if entry is None:
        return {"exists": False, "stale": False, "model": None, "measured_at": None, "ranges": {}, "reasons": []}
    run_scope = entry.get("scope", "baseline")
    # 지금 세트도 그 실행이 읽은 문서 판으로 센다 — 기록이 없는 실행은 긴 판이 생기기 전에 잰 것이라 짧은 판이다
    length = (entry.get("config") or {}).get("document_length") or qt.DOCUMENTS_SHORT
    current = {"scope": run_scope, "fingerprints": compute_run_fingerprints(run_scope, length)}
    compared = compare_entries(entry, current)
    reasons = [
        f"{note['meaning']} — {note['remeasure']}"
        for result in compared.values()
        for note in (*result["changed"], *result["only_left"], *result["only_right"])
    ]
    return {
        "exists": True,
        "stale": any(r["state"] == MISMATCH for r in compared.values()),
        "model": entry.get("model"),
        "measured_at": entry.get("measured_at"),
        "ranges": {scope: r["state"] for scope, r in compared.items()},
        "reasons": reasons,
    }
