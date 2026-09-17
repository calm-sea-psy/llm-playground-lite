"""성능 테스트 실행.

실행 순서는 고정이다: ① 모델 로드 시간(언로드→재로드가
그 시점에만 확실히 언로드 상태이므로 맨 앞) → ② 워밍업(로드 직후 초기화
비용을 속도 측정에서 걷어냄) → ③ 속도·리소스 지표 → ④ 품질·보안 지표.

개별 항목이 실패해도 전체 실행은 멈추지 않고 그 항목만 `failed`로 표시한 채
다음으로 넘어간다. 사용자가 취소하면 이미 시작된 항목은 끝까지 돌고(강제로
끊지 않는다) 다음 항목부터 멈춰서 `partial`로 저장된다.

실행 상태(진행률, 현재 항목)는 메모리에만 둔다. `uvicorn --reload`로 재시작되면
사라진다 — 의도된 동작이다. 완료된 결과만 파일로 남는다.
"""

import copy
import json
import platform
import threading
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aux_models
import baseline
import bench_config as cfg
import bench_measure
import cloud_cost
import machine_info
import memory_probe
import ollama_client
import power_meter
import providers
import quality_runner
import quality_scoring as qs
import quality_testsets as qt
import reproduction
import response_health
import run_errors
import scorer_versions as sv
import summarizer
import system_prompts
import tool_calling_runner

RESULTS_DIR = Path(__file__).parent / "test-results"
PROBE_PATH = qt.PROBE_PATH
# 기준선 사전 점검에서 멈춘 실행과 실패한 기준선 실행의 기록은 `RESULTS_DIR/precheck`에 둔다 —
# **기준선 폴더에 두지 않는다**. 거기 두면 `baseline.latest()`가 측정 시각 순으로 골라 깨진 실행이
# "최신 기준선"이 된다. (경로는 저장 시점에 `RESULTS_DIR`에서 만든다.)

# 실행 종류. **선정용 실행에는 사용자 시스템 프롬프트를 걸지 않는다** — 이
# 벤치마크의 목적은 모델을 재는 것이고 프롬프트를 걸면 프롬프트를 재게 된다. 프롬프트를 거는 것은
# `프롬프트 실험` 모드이고, 그 실행은 모델 선정 비교군에서 기본으로 빠진다.
SELECTION = "selection"
PROMPT_EXPERIMENT = "prompt_experiment"
# 과제용 부분 실행 — 고정 10문항을 같은 질문 그대로 반복한다. 선정 비교에 들어가지 않는다(화면이 목록에서 뺀다)
ASSIGNMENT = "assignment"
RUN_TYPES = (SELECTION, PROMPT_EXPERIMENT, ASSIGNMENT)
ASSIGNMENT_SCOPE = "assignment"
ASSIGNMENT_CLOUD_SCOPE = "assignment_cloud"
# 자체 system_prompt를 가진 세트 — 실험 프롬프트를 뒤에 이어 붙여 한 system 메시지로 합친다
_SETS_WITH_OWN_SYSTEM = ("injection_direct", "prompt_leak", "long_context")

RunStatus = Literal["running", "cancelling", "completed", "partial", "failed"]
ItemStatus = Literal["pending", "running", "completed", "failed"]

# 지표의 저장 상태(넷으로 나눈다). `status`는 진행 표시용이고
# 판정은 `outcome`이 한다. "측정 불가"(안 돌렸거나 대조군 무효)는 항목이 없거나
# 값이 null이라 따로 적지 않는다. 능력 부재와 재현 확정의 0점은 결과 파일에 써넣지
# 않는다 — 프론트의 상태 해석 함수 하나가 만든다(표시와 점수가 갈라지지 않게).
MEASURED = "measured"
INCAPABLE = "incapable"  # 능력 부재 — 모델이 그 능력 자체가 없다(= 0점)
FAILED = "failed"  # 실행 실패 — 아직 아무것도 모른다(= 값 없음, 재측정 신호)
CONFIRMED_FAILURE = "confirmed_failure"  # 이 환경에서 재현됨 — 모델×기계 원인 2회(= 0점)

# 실행 순서 그대로 — ①로드 ②워밍업 ③속도·리소스. 항목 id는 measure 함수 디스패치에도 쓰인다.
_CONTEXT_ITEM_IDS = [f"context_{t}" for t in cfg.CONTEXT_STAGE_TOKENS]

# 품질·보안 지표 — id는 그대로 run.metrics의 키가 되고, bench_config의
# QUALITY_TESTSET_FILES 키와도 일치한다(순서는 testsets/README.md의 1차 세트
# 규격 표와 같다 — 실측 컨텍스트 한계는 1차 제외이므로 여기 없다).
_QUALITY_ITEM_LABELS: dict[str, str] = {
    "instruction_following": "지시 따르기 정확도 (zero/few-shot)",
    "long_context": "긴 컨텍스트 기억력 + 다중 턴 제약 유지",
    "consistency": "일관성/재현성",
    "hallucination": "환각 저항",
    "key_coverage": "핵심 정보 포함률",
    "closed_qa": "폐쇄형 정답 정확도",
    "structured_output": "구조적 출력 준수 (zero/few-shot)",
    "injection_direct": "프롬프트 인젝션 저항성 (직접)",
    "injection_indirect": "프롬프트 인젝션 저항성 (간접)",
    "prompt_leak": "시스템 프롬프트 유출 저항",
    "over_refusal": "과잉 거절률 (정상 응답률로 저장)",
}

# tool-calling 정확도 — 1차 세트와 별개 축이라 위 dict와 분리해서 마지막에 붙인다.
# injection_probe는 "모델이 주입에 넘어가는가"만 잰다 — "확인 단계가 실제로
# 막아주는가"는 tests/test_injection_defense.py로 분리했다.
_TOOL_CALLING_ITEM_LABELS: dict[str, str] = {
    "tool_calling": "Tool-calling 정확도 (트리거·파라미터·오탐·심화 시나리오)",
    "injection_probe": "도구 결과 프롬프트 인젝션 저항성",
}

# 지표 단위 재실행이 가능한 항목 — 속도·리소스 항목은 로드→워밍업 순서에 묶여 있어 뺀다.
RERUNNABLE_ITEM_IDS = set(_QUALITY_ITEM_LABELS) | set(_TOOL_CALLING_ITEM_LABELS)

# 기준선이 선언한 의도적 제외(baseline.DECLARED_EXCLUSIONS와 짝) — 실행 자체에서도 뺀다.
# 쓸 수 없는 값을 저장해두면 언젠가 누가 쓴다.
_BASELINE_SKIPPED_ITEMS = {"consistency"}

# 2회차 — 선정용 로컬 전체 실행은 1회차를 다 돈 뒤 **모델을 내렸다 올려** 같은 호출을 같은 순서로 한 번 더 돈다. 모델을 내리면
# 캐시가 비어, 두 바퀴가 같은 캐시 상태에서 출발한다(같은 호출을 연달아 보내면 2회차 입력이 통째로 캐시에서 나와 재현과 캐시가
# 겹친다). 워밍업까지 다시 돈다 — 없으면 첫 문항 호출만 캐시 상태와 응답 시간 조건이 다르다. 속도 탐침·컨텍스트 부하는 돌지
# 않는다(속도는 반복 중앙값으로 이미 변동을 잰다). 긴 컨텍스트는 압축 끔 경로만 돈다. 점수에는 넣지 않는다(reproduction.py)
SECOND_ROUND_EXCLUDED = {
    "consistency": "샘플링을 일부러 흔들어 같은 질문을 여러 번 묻는 세트라 글자 일치로 재현을 보면 늘 갈린다",
    "tool_calling": "문항 세트 밖이고 선정 규칙에 들지 않는다",
    "injection_probe": "문항 세트 밖이고 선정 규칙에 들지 않는다",
}
SECOND_ROUND_LONG_CONTEXT = "압축 끔 경로만 — 켬 경로는 요약 모델 호출이 끼고 그 요약이 고정 샘플링이 아니라 후보 모델의 재현을 못 본다"
REPEAT_RULE = ("선정용 실행은 1회차를 다 돈 뒤 모델을 내렸다 올려 같은 호출을 같은 순서로 한 번 더 돈다(2회차). "
               "점수·n·게이트·재채점은 1회차만 쓰고, 2회차는 같은 답이 나왔는지만 본다")
# 계측(응답 시간·GPU 전력)이 세는 호출 — 두 참고 값이 같은 호출을 본다(power_meter.RunMeter)
METERING_RULE = ("계측(응답 시간·GPU 전력)은 계측 구간 안의 후보 모델 호출 전부(두 바퀴)다 — 속도·리소스 탐침, 두 바퀴 사이의 "
                 "모델 로드·워밍업, 긴 컨텍스트 켬 경로(요약 모델이 끼는 호출), 도구 호출은 구간 밖이다. 채점은 1회차만 쓴다")

# 기준선이 재는 범위(`_build_items`의 baseline 스코프)의 까닭 — 리포트가 `포함된 실행` 아래에 싣는다. 범위를 바꾸면 함께 고친다
BASELINE_SCOPE_REASON = (
    "품질·보안 항목만 잰다. 속도·리소스·안정성은 이 기계에서 도는 모델의 값이라 외부 API로 재면 다른 것을 재고, "
    "Tool-calling은 로컬 Ollama 경로로만 잴 수 있고, 실측 컨텍스트 한계는 어느 실행도 아직 재지 않는다. "
    "일관성/재현성은 정의상 비교 제외라 재지 않는다"
)


# 표현 강건성 계산에 넣는 지표 — 패러프레이즈 변형이 있는 것만.
# long_context/consistency는 변형 축이 없어 뺀다.
# tool_calling은 품질 카테고리가 아니라 별도 카테고리라 여기 안 넣는다.
# 계산(`_compute_robustness`·`qs.item_score_stdev`)이나 아래 목록의 detail 경로를 바꾸면 `ROBUSTNESS_RULE`도 고친다
# — 리포트가 표현 강건성의 정의로 싣는다. 모으는 지표 이름은 리포트가 이 목록에서 센다
ROBUSTNESS_RULE = (
    "문항별 원문·변형 점수 표준편차(모집단)를 지표 안에서, 다시 지표끼리 평균한다. 0~0.5(0이 흔들림 없음), "
    "변형 없는 문항은 0. 지시 따르기·구조적 출력은 zero-shot 답만"
)
_ROBUSTNESS_SOURCES: list[tuple[str, Any]] = [
    ("instruction_following", lambda m: (m.get("instruction_following") or {}).get("zero", {}).get("detail")),
    ("structured_output", lambda m: (m.get("structured_output") or {}).get("zero", {}).get("detail")),
    ("hallucination", lambda m: (m.get("hallucination") or {}).get("detail")),
    ("key_coverage", lambda m: (m.get("key_coverage") or {}).get("detail")),
    ("closed_qa", lambda m: (m.get("closed_qa") or {}).get("detail")),
    ("injection_direct", lambda m: (m.get("injection_direct") or {}).get("detail")),
    ("injection_indirect", lambda m: (m.get("injection_indirect") or {}).get("detail")),
    ("prompt_leak", lambda m: (m.get("prompt_leak") or {}).get("detail")),
    ("over_refusal", lambda m: (m.get("over_refusal") or {}).get("detail")),
]


def _build_items(scope: str = "full") -> list["RunItem"]:
    """`scope="baseline"`이면 품질·보안 11개 지표만 돈다
    (속도·리소스·안정성·실측 컨텍스트 한계·Tool-calling 전부 제외)."""
    if scope == "baseline":
        return [
            RunItem(id=k, label=v) for k, v in _QUALITY_ITEM_LABELS.items() if k not in _BASELINE_SKIPPED_ITEMS
        ]
    if scope in (ASSIGNMENT_SCOPE, ASSIGNMENT_CLOUD_SCOPE):
        quality = [RunItem(id=k, label=_QUALITY_ITEM_LABELS[k]) for k in qt.ASSIGNMENT_QUESTION_COUNTS]
        if scope == ASSIGNMENT_CLOUD_SCOPE:
            return quality
        # 로드·워밍업·메모리는 로컬 과제 실행에도 든다 — 로딩 시간·VRAM·실제 컨텍스트가 과제의 실행 조건이다
        return [
            RunItem(id="model_load", label="모델 로드 시간"),
            RunItem(id="warmup", label="워밍업"),
            RunItem(id="memory", label="메모리·VRAM 확인"),
            *quality,
        ]

    items = [
        RunItem(id="model_load", label="모델 로드 시간"),
        RunItem(id="warmup", label="워밍업"),
        RunItem(id="memory", label="메모리·VRAM 확인"),
        RunItem(id="short_probe", label=f"짧은 탐침 반복측정 ({cfg.REPEAT_COUNT}회)"),
    ]
    items += [
        RunItem(id=f"context_{t}", label=f"컨텍스트 부하 {t}토큰") for t in cfg.CONTEXT_STAGE_TOKENS
    ]
    items += [RunItem(id=k, label=v) for k, v in _QUALITY_ITEM_LABELS.items()]
    items += [RunItem(id=k, label=v) for k, v in _TOOL_CALLING_ITEM_LABELS.items()]
    return items


def list_suites() -> list[dict]:
    return [{"id": it.id, "label": it.label} for it in _build_items()]


@dataclass
class RunItem:
    id: str
    label: str
    status: ItemStatus = "pending"
    error: str | None = None
    started_at: str | None = None  # 예상 소요 시간 — 항목별 실측 소요시간의 근거
    finished_at: str | None = None
    outcome: str | None = None  # measured | incapable | failed | confirmed_failure
    # 실행 실패의 원재료 — {cause, message, response_body, traceback, memory}. 판정은
    # cause 하나이고 나머지는 사람이 원인을 좁혀볼 단서다(run_errors.describe).
    failure: dict[str, Any] | None = None
    # 이 항목이 실제로 부른 보조 모델 — [{role, model, digest}] (aux_models.py). 안 불렀으면 None.
    aux_models: list[dict[str, Any]] | None = None


@dataclass
class Run:
    id: str
    model: str
    system_prompt: str | None
    status: RunStatus
    total: int
    completed: int
    current_item: str | None
    items: list[RunItem]
    started_at: str
    finished_at: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    scope: str = "full"  # "full" | "baseline"
    provider_name: str = "ollama"  # "ollama" | "cloud"
    # 규칙 버전별 항목 지문 맵(baseline.py) — **실행 시작 시점**에 찍는다. 저장
    # 시점에 계산하면 수십 분 도는 동안 세트를 고쳤을 때 편집 후 값이 남는다.
    fingerprints: dict[str, Any] = field(default_factory=dict)
    # 지표별 판정기 버전 — 재채점하면 함께 갱신된다(측정은 불변, 판정은 갱신).
    scorer_versions: dict[str, dict[str, int]] = field(default_factory=dict)
    # "run" | "metric_rerun". 지표 재실행은 별도 파일로 두고 부모에 합쳐 보여준다 —
    # 원본의 측정값은 건드리지 않는다.
    kind: str = "run"
    parent_run_id: str | None = None
    # 재실행이 부모에게서 물려받는 판정 근거 — 인젝션·유출 지표의 if-010 대조군 결과.
    inherited: dict[str, Any] = field(default_factory=dict)
    # "selection" | "prompt_experiment". 실험일 때만 system_prompt(본문 스냅숏)와 메타가 있다.
    run_type: str = SELECTION
    system_prompt_meta: dict[str, Any] | None = None  # {name, title, sha256, chars}
    system_prompt_application: dict[str, Any] | None = None  # 자체 프롬프트와 합친 세트
    # 기준선 사전 점검(빈 응답이 났던 문항 2개) — 조건 쪽 빈 응답이면 실행을 멈춘다.
    precheck: dict[str, Any] | None = None
    # 파생 값(response_health.py) — 실행 종료 시 저장한다. 옛 결과는 읽을 때 계산한다.
    response_health: dict[str, Any] = field(default_factory=dict)
    korean_purity: dict[str, Any] | None = None
    # 2회차 — {items: [{id, label, status, …}], model_load: {…}, calls: {지표: [호출 기록]}}. 점수를 읽는 곳은 보지 않는다
    second_round: dict[str, Any] | None = None
    # 두 바퀴의 재현 요약(reproduction.compare) — 실행이 끝날 때 한 번 센다
    reproduction: dict[str, Any] | None = None


_runs: dict[str, Run] = {}
_lock = threading.Lock()
_active_run_id: str | None = None  # 동시 실행 방지 — 테스트끼리도, 나중엔 일반 대화와도


def get_active_run_id() -> str | None:
    with _lock:
        return _active_run_id


def config_snapshot(provider_name: str = "ollama") -> dict[str, Any]:
    # 샘플링은 그 경로가 **실제로 보내는** 값만 적는다 — 클라우드 경로는 temperature·seed를 보내지 않는데(보내면 거부한다)
    # 설정값을 적어 두면 결과 파일이 쓰지 않은 조건을 말한다. 보내지 않았으면 None이다(프로바이더 기본값으로 돌았다)
    fixed = providers.applies_fixed_sampling(provider_name) is not False
    return {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": cfg.NUM_PREDICT,
        "sampling": dict(cfg.SAMPLING) if fixed else None,
        "think": cfg.THINK,
        "warmup_count": cfg.WARMUP_COUNT,
        "repeat_count": cfg.REPEAT_COUNT,
        "timeout_short_sec": cfg.TIMEOUT_SHORT,
        "timeout_long_sec": cfg.TIMEOUT_LONG,
        "context_stage_tokens": list(cfg.CONTEXT_STAGE_TOKENS),
        "quality_num_predict": cfg.QUALITY_NUM_PREDICT,
        "quality_timeout_sec": cfg.QUALITY_TIMEOUT,
        "consistency_num_predict": cfg.CONSISTENCY_NUM_PREDICT,
        "consistency_sampling": dict(cfg.REALISTIC_SAMPLING) if fixed else None,
        # 문서를 주고 묻는 세트가 읽는 판(점수에 드는 쪽). 과제용 고정 문항은 실행을 시작할 때 짧은 판으로 덮어 쓴다
        "document_length": qt.REPRESENTATIVE_DOCUMENT_LENGTH,
        # 긴 컨텍스트 기억력의 압축 요약은 항상 이 고정 모델이 한다(채점 대상과
        # 무관 — quality_runner.py의 _SUMMARIZER_MODEL 주석 참고). 요약 모델이 바뀌면
        # 과거 결과와 비교가 성립하지 않으므로 num_ctx·샘플링과 같은 급의
        # 측정 조건으로 기록한다.
        "summarizer_model": quality_runner._SUMMARIZER_MODEL,
        # 요약 호출이 보내는 샘플링 — seed가 없어 요약이 끼는 경로(긴 컨텍스트 켬)는 고정 샘플링이 아니다. 컨텍스트는 요청값이
        # 아니라 실제로 올라간 값을 긴 컨텍스트 결과(`summarizer_loaded`)에 남긴다
        "summarizer_sampling": {"temperature": summarizer.SUMMARY_TEMPERATURE, "top_p": ollama_client.APP_TOP_P, "seed": None},
        # 기계에 딸린 지표를 다른 기계의 값과 섞지 않기 위한 기록. 호스트 이름은 설정을 빠뜨린
        # 보조 PC를 나중에 되짚기 위한 것이라 조건 비교(runDiff)에서는 보지 않는다.
        "measurement_machine": cfg.measurement_machine(),
        "measurement_host": platform.node(),
    }


def measurement_environment(provider_name: str) -> dict[str, Any]:
    """이 기계에서 도는 실행의 사양과 Ollama 서버 버전, 이 앱의 Python·패키지 버전 — 실행을 시작할 때 한 번 읽는다(나중에
    붙일 수 없다). 클라우드 실행은 이 기계에서 돌지 않아 사양과 Ollama 버전을 적지 않지만, 호출하고 채점하는 쪽의 소프트웨어는
    적는다. 버전을 못 읽으면 None으로 남기고 실행은 막지 않는다 — 서버가 정말 안 떠 있으면 첫 항목이 그 사실로 실패한다."""
    software = machine_info.software_snapshot()
    if not providers.NATIVE_API.get(provider_name):
        return {"software": software}
    try:
        version = ollama_client.server_version()
    except Exception:  # noqa: BLE001 — 기록이 목적이라 조회 실패로 실행을 멈추지 않는다
        version = None
    return {"hardware": machine_info.hardware_snapshot(), "ollama_version": version, "software": software}


def model_identity(model: str, provider_name: str) -> dict[str, Any] | None:
    """실제로 부른 모델이 무엇인가 — 태그만으로는 같은 이름에 다른 가중치가 올라왔는지 모른다. 실행을 시작할 때 읽는다.
    - `digest`·`download_bytes`(받은 파일 크기)·`modified_at`(받은 시각): `/api/tags`
    - `quantization_level`·`parameter_size`·`max_context_length`(모델이 지원하는 최대): `/api/show` — 양자화는 tags가
      `unknown`으로 주는 모델이 있어(gemma-4-E4B) show 쪽을 쓴다
    실제로 쓴 컨텍스트는 모델이 올라간 뒤에야 보여 메모리 항목이 따로 남긴다(`loaded_context_length`).
    클라우드 모델은 None — 식별값을 줄 곳이 없다. 못 읽은 칸은 None이고 실행은 막지 않는다."""
    if not providers.NATIVE_API.get(provider_name):
        return None
    tags: dict[str, Any] = {}
    show: dict[str, Any] = {}
    try:
        tags = next((m for m in ollama_client.list_models() if model in (m.get("name"), m.get("model"))), {})
    except Exception:  # noqa: BLE001 — 기록이 목적이라 조회 실패로 실행을 멈추지 않는다
        pass
    try:
        show = ollama_client.show_model(model)
    except Exception:  # noqa: BLE001
        pass
    details = show.get("details") or {}
    info = show.get("model_info") or {}
    return {
        "tag": model,
        "digest": tags.get("digest"),
        "download_bytes": tags.get("size"),
        "modified_at": tags.get("modified_at"),
        "quantization_level": details.get("quantization_level"),
        "parameter_size": details.get("parameter_size"),
        "max_context_length": next((v for k, v in info.items() if k.endswith(".context_length")), None),
    }


def run_conditions(model: str, provider_name: str) -> dict[str, Any]:
    """실행 시작 때 설정 스냅샷에 더하는 기록 — 기계·소프트웨어와 모델 식별값."""
    identity = model_identity(model, provider_name)
    return {**measurement_environment(provider_name), **({"model_identity": identity} if identity else {})}


def _load_probe() -> dict[str, Any]:
    return json.loads(PROBE_PATH.read_text(encoding="utf-8"))


def prompt_meta(prompt: dict[str, Any]) -> dict[str, Any]:
    """결과에 남길 프롬프트 식별 정보 — 이름이 없으면 사람이 못 알아보고, 해시가 없으면 두 실행의
    조건 일치를 못 본다. 길이는 `prompt_eval_count`가 아니라 글자 수다(캐시된 앞부분을 빼고 세어
    반복 호출에서 값이 작아진다)."""
    return {
        "name": prompt["name"],
        "title": prompt.get("title"),
        "sha256": prompt["sha256"],
        "chars": len(prompt["content"]),
    }


def repeats_twice(run_type: str, scope: str, provider_name: str, kind: str = "run") -> bool:
    """2회차를 도는 실행 — 선정용 로컬 전체 실행. 프롬프트 실험·기준선·과제용·지표 재실행은 한 바퀴다."""
    return run_type == SELECTION and scope == "full" and provider_name == "ollama" and kind == "run"


def second_round_item_ids() -> list[str]:
    return ["model_load", "warmup", *[k for k in _QUALITY_ITEM_LABELS if k not in SECOND_ROUND_EXCLUDED]]


def repeat_condition() -> dict[str, Any]:
    """2회차의 조건 — 무엇을 다시 돌고 무엇을 뺐고 어떻게 판정했나."""
    return {
        "rounds": 2,
        "scored_round": 1,
        "second_round_items": second_round_item_ids(),
        "excluded": dict(SECOND_ROUND_EXCLUDED),
        "long_context": SECOND_ROUND_LONG_CONTEXT,
        "rule": REPEAT_RULE,
        "reproduction_rule": reproduction.RULE,
        "metering_rule": METERING_RULE,
    }


def _start(
    model: str,
    *,
    scope: str,
    provider_name: str,
    run_type: str = SELECTION,
    prompt: dict[str, Any] | None = None,
    assignment: dict[str, Any] | None = None,
    document_length: str | None = None,
) -> dict:
    """`start_run`/`start_baseline_run`/`start_assignment_run` 공용부. 실행을 시작하고 즉시 그 시점의
    상태를 dict로 반환한다. 실제 작업은 백그라운드 스레드가 한다."""
    document_length = qt.check_document_length(document_length or qt.REPRESENTATIVE_DOCUMENT_LENGTH)
    environment = run_conditions(model, provider_name)  # 외부 호출이라 잠금 밖에서 읽는다
    with _lock:
        if _active_run_id is not None:
            raise RuntimeError("이미 다른 실행이 진행 중입니다")
        run_id = str(uuid.uuid4())
        items = _build_items(scope)
        # 문서를 주고 묻는 세트가 어느 판을 읽었나 — 실행 하나에 하나라 한 줄로 적힌다
        config = {**config_snapshot(provider_name), **environment, "document_length": document_length}
        if provider_name == "cloud":
            config["cloud_reasoning_effort"] = cfg.CLOUD_REASONING_EFFORT
        if assignment:
            config["assignment"] = assignment  # 고른 문항·반복 수·고른 규칙 — 무엇을 잰 실행인지가 조건이다
        elif scope == "full":
            config["question_set"] = qt.QUESTION_SET  # 세트의 모든 문항 — 고른 문항만 도는 실행은 `assignment`가 그 목록을 적는다
        second_round = None
        if repeats_twice(run_type, scope, provider_name):
            config["repeat"] = repeat_condition()
            labels = {"model_load": "모델 로드 시간", "warmup": "워밍업", **_QUALITY_ITEM_LABELS}
            second_round = {"items": [{"id": i, "label": labels[i], "status": "pending"} for i in second_round_item_ids()],
                            "calls": {}}
        run = Run(
            id=run_id,
            model=model,
            # 본문은 실행 시작 시점에 스냅숏한다 — 측정 중 파일을 고쳐도 조건이 바뀌지 않는다
            system_prompt=prompt["content"] if prompt else None,
            status="running",
            total=len(items) + (len(second_round["items"]) if second_round else 0),
            completed=0,
            current_item=items[0].id if items else None,
            items=items,
            started_at=datetime.now(UTC).isoformat(),
            config=config,
            scope=scope,
            provider_name=provider_name,
            # 이 실행 종류가 실제로 재는 범위만, 선언된 제외는 빼고
            fingerprints=baseline.compute_run_fingerprints(scope, document_length),
            scorer_versions=_scorer_versions_for(items),
            run_type=run_type,
            system_prompt_meta=prompt_meta(prompt) if prompt else None,
            system_prompt_application=(
                {"merged_after_set_prompt": list(_SETS_WITH_OWN_SYSTEM), "standalone": "그 밖의 모든 항목(속도 탐침 포함)"}
                if prompt
                else None
            ),
            second_round=second_round,
        )
        return _launch(run)


def _scorer_versions_for(items: list["RunItem"]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for it in items:
        if (versions := sv.current(it.id)) is not None:
            out[it.id] = versions
    if any(it.id in qs.KOREAN_PURITY_SETS for it in items):
        out["korean_purity"] = {"korean_purity": qs.KOREAN_PURITY_VERSION}
    return out


def _launch(run: "Run") -> dict:
    """`_lock`을 잡은 채로 부른다 — 활성 실행으로 등록하고 백그라운드 스레드를 띄운다."""
    global _active_run_id
    _runs[run.id] = run
    _active_run_id = run.id
    out = asdict(run)
    threading.Thread(target=_execute, args=(run.id,), daemon=True).start()
    return out


def start_assignment_run(provider_name: str = "ollama", model: str | None = None) -> dict:
    """과제용 부분 실행. 로컬은 고정 10문항을 **같은 질문 그대로 2회**, 클라우드(기준선 모델 고정)는 그 가운데 세트마다 첫
    문항을 1회 돈다. 문항은 실행을 시작할 때 규칙으로 고르고 그 ID를 조건에 남긴다 — 세트가 바뀌어도 무엇을 쟀는지 남는다."""
    questions = qt.assignment_questions()
    if provider_name == "cloud":
        model, scope, repeats = baseline.BASELINE_MODEL, ASSIGNMENT_CLOUD_SCOPE, qt.CLOUD_ASSIGNMENT_REPEATS
        questions = qt.cloud_questions(questions)
    elif not model:
        raise ValueError("로컬 과제 실행에는 모델이 필요합니다")
    else:
        scope, repeats = ASSIGNMENT_SCOPE, qt.ASSIGNMENT_REPEATS
    assignment = {"questions": questions, "repeats": repeats, "requested": dict(qt.ASSIGNMENT_QUESTION_COUNTS),
                  "rule": qt.ASSIGNMENT_RULE}
    # 과제용 고정 문항은 이미 짧은 판으로 잰 기록이다 — 같은 조건으로만 다시 잰다
    return _start(model, scope=scope, provider_name=provider_name, run_type=ASSIGNMENT, assignment=assignment,
                  document_length=qt.DOCUMENTS_SHORT)


def start_rerun(parent_run_id: str, item_ids: list[str]) -> dict:
    """지표 단위 재실행. 한 문항의 버그 때문에 253회를 전부 다시
    돌리지 않도록, 고른 항목만 **별도 결과 파일**로 다시 잰다. 지문·config는
    재실행 시작 시점에 새로 찍는다 — 합쳐 보여줄 때 지표마다 출처를 달아야 하므로."""
    with _lock:
        if _active_run_id is not None:
            raise RuntimeError("이미 다른 실행이 진행 중입니다")
        parent = _read_result(parent_run_id)
        if parent is None:
            raise LookupError(f"실행을 찾을 수 없습니다: {parent_run_id}")
        if parent.get("kind") == "metric_rerun":
            raise ValueError("재실행 결과는 다시 재실행할 수 없습니다 — 원래 실행에서 고르세요")
        if parent.get("scope", "full") != "full":
            raise ValueError("기준선은 1회 원칙이라 지표 재실행을 지원하지 않습니다")
        bad = [i for i in item_ids if i not in RERUNNABLE_ITEM_IDS]
        if not item_ids or bad:
            raise ValueError(f"재실행할 수 없는 항목: {bad or '(없음)'}")
        labels = {**_QUALITY_ITEM_LABELS, **_TOOL_CALLING_ITEM_LABELS}
        items = [RunItem(id=i, label=labels[i]) for i in item_ids]
        if010 = (parent.get("metrics", {}).get("instruction_following") or {}).get("capability_control_passed", True)
        # 재실행은 원래 실행과 같은 문서 판을 읽는다 — 합친 뷰에서 지표마다 문서 길이가 갈리지 않게.
        # 기록이 없는 원래 실행은 긴 판이 생기기 전에 잰 것이라 짧은 판이다
        document_length = (parent.get("config") or {}).get("document_length") or qt.DOCUMENTS_SHORT
        run = Run(
            id=str(uuid.uuid4()),
            model=parent["model"],
            system_prompt=parent.get("system_prompt"),
            status="running",
            total=len(items),
            completed=0,
            current_item=items[0].id,
            items=items,
            started_at=datetime.now(UTC).isoformat(),
            config={**config_snapshot(parent.get("provider_name", "ollama")),
                    **run_conditions(parent["model"], parent.get("provider_name", "ollama")),
                    "document_length": document_length},
            scope="full",
            provider_name=parent.get("provider_name", "ollama"),
            fingerprints=baseline.compute_run_fingerprints("full", document_length),
            scorer_versions=_scorer_versions_for(items),
            kind="metric_rerun",
            parent_run_id=parent_run_id,
            inherited={"capability_control_passed": if010},
            # 실험 실행의 재실행은 같은 프롬프트(부모가 스냅숏한 본문)로 다시 잰다
            run_type=parent.get("run_type", SELECTION),
            system_prompt_meta=parent.get("system_prompt_meta"),
            system_prompt_application=parent.get("system_prompt_application"),
        )
        return _launch(run)


def start_run(model: str, run_type: str = SELECTION, system_prompt_name: str | None = None) -> dict:
    """로컬 모델 전체 실행(모든 지표). **선정용 실행은 프롬프트를 받지 않는다**(ValueError).
    실험은 저장된 프롬프트 이름을 받고 본문은 여기서 파일에서 읽는다 — 화면이 보낸 본문을 믿으면
    결과의 이름·해시와 실제 적용된 내용이 어긋날 수 있다. 문서 세트는 대표 판(`REPRESENTATIVE_DOCUMENT_LENGTH`)을 읽는다."""
    if run_type not in RUN_TYPES:
        raise ValueError(f"알 수 없는 실행 종류: {run_type}")
    if run_type == ASSIGNMENT:
        if system_prompt_name:
            raise ValueError("과제용 실행에는 시스템 프롬프트를 걸지 않습니다")
        return start_assignment_run("ollama", model)
    if run_type == SELECTION:
        if system_prompt_name:
            raise ValueError("선정용 실행에는 시스템 프롬프트를 걸지 않습니다 — 프롬프트 실험 모드를 고르세요")
        return _start(model, scope="full", provider_name="ollama")
    if not system_prompt_name:
        raise ValueError("프롬프트 실험에는 저장된 시스템 프롬프트가 필요합니다")
    try:
        prompt = system_prompts.get_prompt(system_prompt_name)
    except FileNotFoundError as exc:
        raise LookupError(f"시스템 프롬프트를 찾을 수 없습니다: {system_prompt_name}") from exc
    return _start(model, scope="full", provider_name="ollama", run_type=PROMPT_EXPERIMENT, prompt=prompt)


def start_baseline_run() -> dict:
    """클라우드 베이스라인 실행 — 품질·보안 지표만, `gpt-5.6-luna`로. 항상 선정용이다.
    모델은 사용자가 고르지 않는다 — 베이스라인은 이 모델 하나로 고정이다."""
    # 기준선이 고정 샘플링을 받지 않는다는 것이 일관성 제외(`_BASELINE_SKIPPED_ITEMS`, 프론트 `BASELINE_EXCLUDED_ITEMS`)와
    # 그 까닭 문구의 근거다. 고정 샘플링을 받는 프로바이더로 바꾸면 그 제외와 문구가 근거를 잃는다 — 함께 본다(테스트가 깨진다)
    return _start(baseline.BASELINE_MODEL, scope="baseline", provider_name="cloud")


class CapabilityAbsent(Exception):
    """모델이 그 능력 자체를 갖고 있지 않다 — 측정은 성립하고 결과를 이미 안다(못 한다).
    실행 전에 `/api/show`의 `capabilities`로 판정한다. 그냥 호출해서 400을 받는 것은
    근거가 못 된다(400은 우리가 요청을 잘못 만들어도 나온다)."""


def _record_aux_models(item: RunItem, used: list[dict[str, Any]]) -> None:
    """항목이 부른 보조 모델을 digest와 함께 남긴다 — 모델 이름만으로는 `ollama pull` 뒤에 바뀐 것을 가를 수
    없다. digest를 못 읽어도(태그 조회 실패) 사용 사실은 남긴다."""
    if not used:
        return
    for u in used:
        try:
            u["digest"] = ollama_client.model_digest(u["model"])
        except Exception:  # noqa: BLE001 — 사용 표시가 목적이라 조회 실패로 항목을 실패시키지 않는다
            u["digest"] = None
    with _lock:
        item.aux_models = used


def _supports_tools(model: str) -> bool:
    return "tools" in (ollama_client.show_model(model).get("capabilities") or [])


def _run_item(run: Run, item: RunItem, probe: dict[str, Any], provider: providers.Provider) -> None:
    """항목 하나를 실행해 `run.metrics`에 그 결과를 채운다. 실패하면 예외를 그대로 던진다.

    `provider`는 품질 지표(quality_runner) 쪽에만 쓰인다 — 속도·리소스
    측정(bench_measure)과 도구 호출 지표(tool_calling_runner)는 베이스라인
    적용 범위 밖이라 항상 로컬 Ollama 전용이고, 베이스라인 실행(`scope="baseline"`)
    에서는 애초에 그 항목들이 `_build_items()`에 들어가지 않는다."""
    short_text = probe["short"]
    stages = {s["target_tokens"]: s["text"] for s in probe["context_stages"]}
    # `프롬프트 실험`일 때만 모든 항목(속도 탐침 포함)에 건다. 선정용 실행은 None이다.
    us = run.system_prompt if run.run_type == PROMPT_EXPERIMENT else None

    if item.id == "model_load":
        run.metrics.update(bench_measure.measure_model_load(run.model, user_system=us))
    elif item.id == "warmup":
        for _ in range(cfg.WARMUP_COUNT):
            bench_measure.measure_warmup(run.model, short_text, user_system=us)
    elif item.id == "memory":
        run.metrics.update(bench_measure.measure_memory(run.model))
    elif item.id == "short_probe":
        run.metrics.update(bench_measure.measure_short_probe(run.model, short_text, user_system=us))
    elif item.id in _CONTEXT_ITEM_IDS:
        target = int(item.id.removeprefix("context_"))
        run.metrics[item.id] = bench_measure.measure_context_stage(run.model, stages[target], user_system=us)
    elif item.id in _QUALITY_ITEM_LABELS:
        run.metrics[item.id] = _quality_result(run, item.id, provider, us)
    elif item.id in ("tool_calling", "injection_probe"):
        if not _supports_tools(run.model):
            raise CapabilityAbsent(f"{run.model}의 capabilities에 tools가 없음")
        if item.id == "tool_calling":
            run.metrics["tool_calling"] = tool_calling_runner.run_tool_calling(run.model, us)
        else:
            run.metrics["injection_probe"] = tool_calling_runner.run_injection_probe(run.model, us)
    else:
        raise ValueError(f"알 수 없는 항목: {item.id}")


def _quality_result(run: Run, item_id: str, provider: providers.Provider, us: str | None,
                    **options: Any) -> dict[str, Any]:
    """품질·보안 지표 하나를 돌린 결과 — 1회차는 `run.metrics`에 넣고 2회차는 호출 기록만 꺼낸다(같은 함수라 같은 호출을
    같은 순서로 보낸다). `options`는 긴 컨텍스트의 경로 선택(`compress_paths`)."""
    if item_id == "long_context":
        return quality_runner.run_long_context(run.model, provider, us, **options)
    if item_id in ("injection_direct", "injection_indirect", "prompt_leak"):
        # if-010(지시 따르기의 능력 대조군)을 통과 못 했으면 세 지표 다 "값 없음" —
        # instruction_following 항목이 아직 안 돌았거나 실패했으면(run.metrics에
        # 없으면) 증거가 없다는 뜻이라 무효 처리하지 않는다(기본값 True).
        if010_passed = run.metrics.get("instruction_following", {}).get(
            "capability_control_passed", run.inherited.get("capability_control_passed", True)
        )
        return getattr(quality_runner, f"run_{item_id}")(run.model, provider, if010_passed, us)
    return getattr(quality_runner, f"run_{item_id}")(run.model, provider, us)


def _finalize_metrics(run: Run) -> None:
    """개별 항목 결과들을 모아 파생 지표(유지율·에러율·완결성·표현 강건성)를 계산한다."""
    m = run.metrics
    if run.kind == "metric_rerun":
        return  # 재실행은 일부 지표만 담는다 — 파생 지표는 부모와 합친 뒤(load_result) 계산한다

    # 속도·리소스·안정성 파생 지표는 그 항목들이 실제로 도는 "full" 스코프에서만
    # 계산한다 — 베이스라인(scope="baseline")은 model_load/context_* 자체를
    # 돌리지 않으므로, 여기 그대로 두면 "시도 0건인데 성공률 100%"처럼 거짓
    # 지표가 나온다.
    if run.scope == "full":
        stage_ids = _CONTEXT_ITEM_IDS

        # prefill 처리량 — 가장 짧은(=2000토큰) 컨텍스트 단계에서 뽑는다("긴 입력" 대표값)
        first_stage = m.get(stage_ids[0])
        if first_stage:
            m["prefill_tok_per_sec"] = first_stage.get("prefill_tok_per_sec")

        # 컨텍스트 부하 시 tok/s 유지율 = 가장 긴 단계 tok/s ÷ 짧은 탐침 tok/s
        last_stage = m.get(stage_ids[-1])
        if last_stage and last_stage.get("tok_per_sec") and m.get("tok_per_sec"):
            m["context_retention_ratio"] = last_stage["tok_per_sec"] / m["tok_per_sec"]

        # 단계별 원값 — 표시 정보 (지표 아님)
        m["context_tok_per_sec_by_stage"] = {
            str(t): (m.get(f"context_{t}") or {}).get("tok_per_sec") for t in cfg.CONTEXT_STAGE_TOKENS
        }

        # 부하 조건 에러율 -> 성공률로 뒤집어 저장 (0으로 나누기 방지)
        attempted = len(stage_ids)
        failed = sum(1 for it in run.items if it.id in stage_ids and it.status == "failed")
        m["load_success_ratio"] = (attempted - failed) / attempted if attempted else None

        # 응답 완결성 = model_load(1) + short_probe(N) + context 단계(3) 중 완결 비율.
        # num_predict 상한 도달(`length`)은 완결로 친다 — 우리가 건 통제이지 모델 결함이 아니다.
        complete, total = 0, 0
        if "load_complete" in m:
            total += 1
            complete += 1 if m["load_complete"] else 0
        total += m.get("short_probe_total_count", 0)
        complete += m.get("short_probe_complete_count", 0)
        for t in cfg.CONTEXT_STAGE_TOKENS:
            stage = m.get(f"context_{t}")
            if stage:
                total += 1
                complete += 1 if stage.get("complete") else 0
        m["completion_rate"] = (complete / total) if total else None

    if run.provider_name == "cloud":
        m["cost_estimate"] = cloud_cost.estimate(run.model, m, [it.id for it in run.items])
    _compute_robustness(m, run.run_type)


def _compute_robustness(m: dict[str, Any], run_type: str | None = SELECTION) -> None:
    """표현 강건성. 패러프레이즈 변형이 있는 지표들의
    문항별 점수 편차를 모아 지표별 값 + 전체 대표값 하나를 낸다. full/baseline
    양쪽 다 해당하는 지표만 쓰므로 스코프 구분 없이 계산한다.

    **과제용 실행은 재지 않는다** — 같은 질문을 그대로 반복해 표현 변형이 없어, 계산하면 `표현 흔들림`이 아니라 `반복 흔들림`이
    같은 이름으로 남는다. 판단을 여기 두는 이유: 실행 확정·결과 불러오기(재실행 병합)·재채점이 모두 이 함수를 다시 부른다."""
    if run_type == ASSIGNMENT:
        m["robustness"] = {"overall": None, "by_metric": {},
                           "not_measured": "같은 질문을 그대로 반복한 실행이라 표현 변형이 없어 재지 않는다"}
        return
    by_metric: dict[str, float] = {}
    for name, get_detail in _ROBUSTNESS_SOURCES:
        detail = get_detail(m)
        if not detail:
            continue
        stdev = qs.mean_item_stdev(detail)
        if stdev is not None:
            by_metric[name] = stdev
    m["robustness"] = {
        "overall": (sum(by_metric.values()) / len(by_metric)) if by_metric else None,
        "by_metric": by_metric,
    }


def _meter_for(run: Run) -> power_meter.RunMeter | None:
    """계측(GPU 전력·응답 시간)을 거는 실행 — 이 기계에서 도는 과제용 실행과 2회차를 도는 선정용 실행."""
    if run.provider_name != "ollama":
        return None
    if run.run_type == ASSIGNMENT or repeats_twice(run.run_type, run.scope, run.provider_name, run.kind):
        return power_meter.RunMeter()
    return None


def _run_round_one(run: Run, probe: dict[str, Any], provider: providers.Provider, meter: power_meter.RunMeter | None) -> bool:
    """1회차 — 항목 목록 그대로. 계측 구간은 문항 항목(품질·보안)만이다: 첫 문항 직전에 열고, 문항이 아닌 항목(도구 호출)에서
    끊는다. 완료된 항목이 하나라도 있었나를 돌려준다."""
    question_ids = set(qt.ASSIGNMENT_QUESTION_COUNTS) if run.run_type == ASSIGNMENT else set(_QUALITY_ITEM_LABELS)
    any_completed = False
    for item in run.items:
        with _lock:
            if run.status != "running":  # cancel_run이 끊었으면 다음 항목은 시작 안 함
                break
            item.status = "running"
            item.started_at = datetime.now(UTC).isoformat()
            run.current_item = item.id
        if meter is not None:
            if item.id in question_ids:
                meter.begin()  # 처음이면 모델이 올라간 상태의 대기 전력을 재고 연다, 끊겨 있었으면 다시 연다
            else:
                meter.pause()
        # 가용 메모리는 요청 **전에** 읽는다 — OOM이 나면 Ollama가 메모리를 풀어버려
        # 직후에는 최고치가 남지 않는다. 클라우드 실행에는 의미가 없어 건너뛴다.
        memory = memory_probe.snapshot(run.model) if run.provider_name == "ollama" else None
        used_aux: list[dict[str, Any]] = []
        try:
            with aux_models.collect() as used_aux:
                _run_item(run, item, probe, provider)
            _record_aux_models(item, used_aux)
            with _lock:
                item.status = "completed"
                item.outcome = MEASURED
                item.finished_at = datetime.now(UTC).isoformat()
                run.completed += 1
                any_completed = True
        except CapabilityAbsent as exc:
            _record_aux_models(item, used_aux)
            with _lock:
                item.status = "completed"
                item.outcome = INCAPABLE
                item.error = str(exc)
                item.finished_at = datetime.now(UTC).isoformat()
                run.completed += 1
                any_completed = True
        except Exception as exc:  # noqa: BLE001 — 이 항목만 실패, 다음으로 계속
            _record_aux_models(item, used_aux)  # 실패한 항목도 부른 적이 있으면 남긴다
            failure = run_errors.describe(exc, memory)
            outcome = FAILED
            if failure["cause"] == run_errors.MODEL_MACHINE and _reproduced_before(run, item.id, failure):
                outcome = CONFIRMED_FAILURE
            with _lock:
                item.status = "failed"
                item.outcome = outcome
                item.failure = failure
                item.error = str(exc)
                item.finished_at = datetime.now(UTC).isoformat()
    if meter is not None:
        meter.pause()
    return any_completed


def _run_second_round(run: Run, probe: dict[str, Any], provider: providers.Provider,
                      meter: power_meter.RunMeter | None) -> None:
    """2회차 — 모델 로드(내리고 → 다시 올리고) → 워밍업 → 1회차와 같은 문항 호출. 문항 결과는 점수 자리에 넣지 않고 호출 기록만
    `second_round.calls`에 둔다. 항목 하나가 실패해도 다음으로 넘어간다."""
    state = run.second_round or {}
    for entry in state.get("items") or []:
        with _lock:
            if run.status != "running":
                break
            entry["status"] = "running"
            entry["started_at"] = datetime.now(UTC).isoformat()
            run.current_item = f"2회차 · {entry['id']}"
        try:
            if entry["id"] == "model_load":
                state["model_load"] = bench_measure.measure_model_load(run.model)
            elif entry["id"] == "warmup":
                for _ in range(cfg.WARMUP_COUNT):
                    bench_measure.measure_warmup(run.model, probe["short"])
            else:
                if meter is not None:
                    meter.begin()
                options = {"compress_paths": (False,)} if entry["id"] == "long_context" else {}
                result = _quality_result(run, entry["id"], provider, None, **options)
                state["calls"][entry["id"]] = reproduction.records(entry["id"], result)
            with _lock:
                entry["status"] = "completed"
                entry["finished_at"] = datetime.now(UTC).isoformat()
                run.completed += 1
        except Exception as exc:  # noqa: BLE001 — 이 항목만 실패, 다음으로 계속
            with _lock:
                entry["status"] = "failed"
                entry["error"] = str(exc)
                entry["finished_at"] = datetime.now(UTC).isoformat()
    if meter is not None:
        meter.pause()


def _execute(run_id: str) -> None:
    global _active_run_id
    run = _runs[run_id]
    subset = run.config.get("assignment")
    token = qt.enter_question_subset(subset["questions"], subset["repeats"]) if subset else None
    # 두 바퀴 모두 같은 판을 읽는다 — 문맥은 이 실행 스레드에만 걸리고 끝나면 풀린다
    doc_token = qt.enter_document_length(run.config.get("document_length") or qt.DOCUMENTS_SHORT)
    meter = _meter_for(run)
    try:
        probe = _load_probe()
        provider = providers.get_provider(run.provider_name)
        if run.provider_name == "cloud":
            run.precheck = _run_precheck(run, provider)
            if not run.precheck["passed"]:
                with _lock:
                    run.status = "failed"
        with quality_runner.metering(meter) if meter is not None else nullcontext():
            any_completed = _run_round_one(run, probe, provider, meter)
            if run.second_round is not None:
                _run_second_round(run, probe, provider, meter)

        if meter is not None:
            measured = meter.finish()  # 꼬리·대기 측정에 몇 초 걸린다 — 잠금 밖에서
            with _lock:
                run.metrics.update(measured)
        with _lock:
            if run.second_round is not None and run.second_round.get("calls"):
                run.reproduction = reproduction.compare(run.metrics, run.second_round["calls"])
            _finalize_metrics(run)
            _attach_derived(run)
            if run.status == "running":
                run.status = "completed" if any_completed else "failed"
            elif run.status == "cancelling":
                run.status = "partial"
    except Exception:  # noqa: BLE001 — probe.json 로드 실패 등, 실행 자체가 죽은 경우
        with _lock:
            run.status = "failed"
    finally:
        if token is not None:
            qt.exit_question_subset(token)
        qt.exit_document_length(doc_token)
        with _lock:
            run.finished_at = datetime.now(UTC).isoformat()
            run.current_item = None
            _active_run_id = None
        if run.scope == "baseline":
            if run.status == "failed":
                # 사전 점검에서 멈췄거나 실행 자체가 죽은 기준선 — 기준선 폴더에 두면 latest()가 고른다
                _save_to(RESULTS_DIR / "precheck", run)
            else:
                baseline.save(
                    asdict(run),
                    conditions={
                        "quality_num_predict": cfg.QUALITY_NUM_PREDICT,
                        "quality_timeout_sec": cfg.QUALITY_TIMEOUT,
                        "provider": run.provider_name,
                        "reasoning_effort_requested": cfg.CLOUD_REASONING_EFFORT,
                        "reasoning_effort_applied": quality_runner.applied_reasoning_effort(run.model),
                        "reasoning_effort_rejection": quality_runner.reasoning_effort_rejection(run.model),
                        "note": "클라우드 reasoning 모델이라 temperature/top_p 커스텀 값을 쓰지 않는다(providers.py 참고)",
                    },
                )
        else:
            _save_result(run)


def _attach_derived(run: Run) -> None:
    """응답 상태 요약·한국어 순도를 실행 결과에 저장한다(response_health.py)."""
    derived = response_health.attach_derived({"metrics": run.metrics})
    run.response_health = derived["response_health"]
    run.korean_purity = derived.get("korean_purity")


def _precheck_questions() -> list[dict[str, Any]]:
    """사전 점검 문항 — **최신 기준선에서 빈 응답이 났던 문항 그대로**(과잉 거절 변형 하나 +
    일관성 문항 하나). 짧은 임의 질문은 예산이 모자라지 않아 "정상"으로 나오고, 그러면 확인한 것이
    없다. 기록이 없으면 각 세트의 첫 문항을 쓴다."""
    prev = baseline.latest() or {}
    metrics = prev.get("metrics") or {}
    over = qt.load_quality_testset("over_refusal")
    cons = qt.load_quality_testset("consistency")
    picked: list[dict[str, Any]] = []
    for e in (metrics.get("over_refusal") or {}).get("detail") or []:
        if not (e.get("response") or "").strip():
            picked.append({"metric": "over_refusal", "item_id": e["id"], "question": e["variant"]})
            break
    else:
        it = over["items"][0]
        picked.append({"metric": "over_refusal", "item_id": it["id"], "question": it["variants"][0]})
    questions = {it["id"]: it["prompt"] for it in cons["items"]}
    for e in (metrics.get("consistency") or {}).get("detail") or []:
        if e["id"] in questions and any(not (r or "").strip() for r in e.get("responses") or []):
            picked.append({"metric": "consistency", "item_id": e["id"], "question": questions[e["id"]]})
            break
    else:
        it = cons["items"][0]
        picked.append({"metric": "consistency", "item_id": it["id"], "question": it["prompt"]})
    return picked


def _run_precheck(run: Run, provider: providers.Provider) -> dict[str, Any]:
    """기준선 사전 점검. 측정이 아니라 조건 점검이므로 1회 원칙에 세지 않는다. **조건 쪽 빈 응답**
    (텍스트 없음 + `finish_reason=length` + 추론 토큰 > 0)이 하나라도 나오면 멈춘다 — 확인 없이
    돌리면 1회 원칙을 쓰고도 같은 빈 응답 더미를 얻는다. 호출 자체가 실패해도 멈춘다."""
    checks: list[dict[str, Any]] = []
    error = None
    try:
        for q in _precheck_questions():
            reply = quality_runner.ask_model(
                run.model, quality_runner._build_messages(question=q["question"]), provider=provider
            )
            call = reply.call or {}
            checks.append(
                {
                    **q,
                    "kind": response_health.classify(reply.text, refused=reply.refused, call=reply.call),
                    "refused": reply.refused,
                    "finish_reason": call.get("finish_reason"),
                    "reasoning_tokens": call.get("reasoning_tokens"),
                    "completion_tokens": call.get("completion_tokens"),
                    "text_head": reply.text[:200],
                }
            )
    except Exception as exc:  # noqa: BLE001 — 점검 실패는 실행 중단 사유로 기록한다
        error = run_errors.describe(exc, None)
    passed = error is None and not any(c["kind"] == "empty_condition" for c in checks)
    applied = quality_runner.applied_reasoning_effort(run.model)
    rejection = quality_runner.reasoning_effort_rejection(run.model)
    run.config["cloud_reasoning_effort_applied"] = applied
    if rejection:
        run.config["cloud_reasoning_effort_rejection"] = rejection
    return {
        "passed": passed,
        "checks": checks,
        "error": error,
        "reasoning_effort_applied": applied,
        "reasoning_effort_rejection": rejection,
        "at": datetime.now(UTC).isoformat(),
    }


def _reproduced_before(run: Run, item_id: str, failure: dict[str, Any]) -> bool:
    """재현 확정 — 같은 모델·같은 항목에서 **같은 원인(모델×기계)**으로,
    **같은 측정 조건**에서 전에 실패한 적이 있으면 이번이 2회째다. 조건을 빼면
    `num_ctx`를 바꿔 OOM 여부 자체가 달라진 두 번이 재현으로 세어진다."""
    for data in _iter_results():
        if data.get("id") == run.id or data.get("model") != run.model or data.get("config") != run.config:
            continue
        for it in data.get("items", []):
            prior = it.get("failure") or {}
            if it.get("id") == item_id and prior.get("cause") == failure["cause"]:
                return True
    return False


def cancel_run(run_id: str) -> bool:
    """실행 중단을 요청한다. 이미 시작된 항목은 끝까지 돈다 — 다음 항목부터 멈춘다."""
    with _lock:
        run = _runs.get(run_id)
        if run is None or run.status != "running":
            return False
        run.status = "cancelling"
    return True


def get_run(run_id: str) -> dict | None:
    with _lock:
        run = _runs.get(run_id)
        return asdict(run) if run else None


def _save_result(run: Run) -> None:
    _save_to(RESULTS_DIR, run)


def _save_to(directory: Path, run: Run) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(run), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _iter_results():
    if not RESULTS_DIR.exists():
        return
    for f in RESULTS_DIR.glob("*.json"):
        try:
            yield json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def _read_result(run_id: str) -> dict | None:
    path = RESULTS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _reruns_of(run_id: str) -> list[dict]:
    """부모에 붙은 지표 재실행들, 오래된 것부터 — 합칠 때 뒤의 것이 앞의 것을 덮는다
    ("같은 지표를 여러 번 재실행했으면 최신 것을 쓴다")."""
    reruns = [
        d
        for d in _iter_results()
        if d.get("kind") == "metric_rerun" and d.get("parent_run_id") == run_id and d.get("status") != "running"
    ]
    return sorted(reruns, key=lambda d: d["started_at"])


def _provenance(data: dict) -> dict:
    return {
        "run_id": data["id"],
        "started_at": data["started_at"],
        "fingerprints": data.get("fingerprints") or {},
        "config": data.get("config") or {},
        "kind": data.get("kind", "run"),
        # 재실행에서 온 값은 그 파일의 재채점 시각을 따른다 — 합친 뷰의 `rescored_at`은 부모 파일의 것이다
        "rescored_at": data.get("rescored_at"),
    }


def merge_with_reruns(parent: dict, reruns: list[dict]) -> dict:
    """부모 + 항목별 최신 재실행을 합친 뷰. 원본 파일은 건드리지 않는다. 각 항목에
    그 값이 온 실행의 id·시각·지문·측정 조건을 달아둔다 — 합쳐 보여주는 순간
    "이 값이 어디서 왔는가"를 말할 수 있어야 하고, 조건이 섞였는지도 봐야 한다."""
    merged = copy.deepcopy(parent)
    merged.setdefault("kind", "run")
    parent_prov = _provenance(parent)
    provenance = {it["id"]: parent_prov for it in merged.get("items", [])}
    items_by_id = {it["id"]: i for i, it in enumerate(merged.get("items", []))}
    replaced: list[str] = []
    for rerun in reruns:
        prov = _provenance(rerun)
        for it in rerun.get("items", []):
            if it.get("status") in ("pending", "running"):
                continue
            item_id = it["id"]
            if item_id in items_by_id:
                merged["items"][items_by_id[item_id]] = it
            else:
                items_by_id[item_id] = len(merged["items"])
                merged["items"].append(it)
            if item_id in (rerun.get("metrics") or {}):
                merged["metrics"][item_id] = rerun["metrics"][item_id]
            else:
                merged["metrics"].pop(item_id, None)
            # 버전도 값을 낸 실행의 기록에서 읽는다 — 재실행에 기록이 없으면 부모 기록을 남기지 않고 비운다.
            # 버전이 바뀐 기록도 같다: 부모의 기록은 부모가 낸 값에 대한 것이라 재실행 값에 붙이지 않는다
            for key in ("scorer_versions", "scorer_version_changes"):
                if item_id in (rerun.get(key) or {}):
                    merged.setdefault(key, {})[item_id] = rerun[key][item_id]
                else:
                    (merged.get(key) or {}).pop(item_id, None)
            provenance[item_id] = prov
            if item_id not in replaced:
                replaced.append(item_id)
    _compute_robustness(merged.setdefault("metrics", {}), merged.get("run_type"))
    merged["provenance"] = provenance
    merged["mixed"] = bool(replaced)
    merged["rerun_ids"] = [r["id"] for r in reruns]
    return merged


def list_results() -> list[dict]:
    out = []
    for data in _iter_results():
        if data.get("kind") == "metric_rerun":
            continue  # 독립 실행으로 세우지 않는다 — "모델별 최신"으로 올라가면 지표 대부분이 빈 실행이 선택된다
        reruns = _reruns_of(data["id"])
        out.append(
            {
                "id": data["id"],
                "model": data["model"],
                "status": data["status"],
                "started_at": data["started_at"],
                "finished_at": data.get("finished_at"),
                # 직전 실행과 조건 비교(달라진 점 한 줄)·비교 화면의
                # 조건 불일치 경고에 쓴다. 목록 하나 더 읽는 걸로 값이 이미 있어
                # 결과 파일을 또 열 필요가 없다.
                "system_prompt": data.get("system_prompt"),
                "run_type": data.get("run_type", SELECTION),
                "system_prompt_meta": data.get("system_prompt_meta"),
                "config": data.get("config", {}),
                # 리포트 표지의 재현 메타 — 목록에서 바로 실어 보낸다.
                "fingerprints": data.get("fingerprints", {}),
                "rerun_ids": [r["id"] for r in reruns],
                "mixed": bool(reruns),
            }
        )
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out


def load_result(run_id: str) -> dict | None:
    """저장된 실행 — 지표 재실행이 붙어 있으면 합친 뷰다. 재실행 파일 자체를 id로
    열면 합치지 않고 그대로 돌려준다."""
    data = _read_result(run_id)
    if data is None:
        return None
    if data.get("kind") == "metric_rerun":
        return response_health.attach_derived(data)
    # 합친 뷰는 부모 파일에 저장된 파생 값과 구성이 달라 항상 다시 계산한다
    return response_health.attach_derived(merge_with_reruns(data, _reruns_of(run_id)))


def estimate_duration(model: str, scope: str = "full") -> dict[str, Any]:
    """이번 실행 예상 소요 시간. 처음 계획은 "항목별 호출 횟수 ×
    호출당 평균 시간"을 가정했지만, 테스트 세트 구성(문항 수)은 항목마다 고정이라
    이미 과거 실측 소요시간(finished_at-started_at) 안에 그대로 녹아 있다 — 항목
    하나의 과거 소요시간을 평균 내는 쪽이 더 간단하고, 세트 문항 수가 나중에
    바뀌어도 호출 횟수 표를 따로 유지보수할 필요 없이 자동으로 맞다.

    같은 모델의 과거 실행이 있으면 그것만 쓰고, 없으면(그 모델의 첫 실행) 전체
    모델 평균으로 대체한다 — 그마저 없으면(누구도 그 항목을 돈 적 없음) 그
    항목은 예상 불가. 항목 중 하나라도 예상 불가면 총합도 예상 불가로 둔다
    (일부만 더해 실제보다 짧게 보이는 총합을 보여주는 것보다 안전하다)."""
    results_dir = baseline.BASELINE_DIR if scope == "baseline" else RESULTS_DIR
    items = _build_items(scope)

    same_model: dict[str, list[float]] = {}
    all_models: dict[str, list[float]] = {}
    sample_ids: set[str] = set()
    if results_dir.exists():
        for f in results_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("scope", "full") != scope:
                continue
            sample_ids.add(data.get("id", f.name))
            for it in data.get("items", []):
                started, finished = it.get("started_at"), it.get("finished_at")
                if it.get("status") != "completed" or not started or not finished:
                    continue
                try:
                    dur = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
                except ValueError:
                    continue
                all_models.setdefault(it["id"], []).append(dur)
                if data.get("model") == model:
                    same_model.setdefault(it["id"], []).append(dur)

    def mean(item_id: str) -> float | None:
        samples = same_model.get(item_id) or all_models.get(item_id)
        return (sum(samples) / len(samples)) if samples else None

    by_item: dict[str, float | None] = {it.id: mean(it.id) for it in items}
    values = list(by_item.values())
    if scope == "full":
        # 2회차(선정용 실행) — 1회차의 같은 항목 시간으로 어림한다. 긴 컨텍스트는 끔 경로만 돌아 절반으로 센다
        values += [(mean(i) or 0) * 0.5 if i == "long_context" and mean(i) is not None else mean(i) for i in second_round_item_ids()]
    total = sum(values) if values and all(v is not None for v in values) else None
    return {"by_item": by_item, "total_sec": total, "sample_count": len(sample_ids)}
