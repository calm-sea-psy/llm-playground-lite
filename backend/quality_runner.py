"""품질·보안 지표 실행 오케스트레이션.

`test_runner.py`가 지표 하나당 함수 하나씩(`run_<metric>`)을 호출한다. 각 함수는
그 지표의 질문 세트를 전부 돌려 `run.metrics["<metric>"]`에 그대로 들어갈 dict를
반환한다 — 집계 점수뿐 아니라 문항·변형별 원본 답변도 함께 담는다.

로컬(Ollama)은 전부 네이티브 `stream_chat`을 쓴다(성능 테스트 경로는 네이티브로
고정한다). `providers.Provider`를 받아 클라우드
베이스라인(`gpt-5.6-luna`)도 같은 함수로 돌린다 — `ask_model()`이 유일한
분기점이다. 대화 저장(`conversations.py`)은 거치지 않는다 — 이 요청들은
대화 자동 저장 대상에서 제외된다.

`ask_model()`은 비교 노트에서도 그대로 재사용한다 — "로컬/클라우드
분기점 하나"라는 원칙을 지키려면 새 기능도 이 함수를 거쳐야 한다.
"""

import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import bench_config as cfg
import bench_measure
import ollama_client
import quality_scoring as qs
import quality_testsets as qt
import response_health as rh
import run_errors
import summarizer
from providers import Provider

# 거절 필드(`message.refusal`)만 오고 문구가 비었을 때 텍스트 자리에 넣는 표식 —
# 빈 문자열로 두면 빈 응답 검사를 거절이 우회해 canary 판정이 "표식 없음 = 통과"가 된다.
REFUSAL_PLACEHOLDER = "[refusal]"


@dataclass
class ModelReply:
    """호출 한 번의 결과. **거절은 텍스트 자리에 넣고 플래그를 함께 둔다** —
    같은 거절을 텍스트로 받았을 때와 필드로 받았을 때 판정이 같아야 하므로, 판정기는 평소대로
    텍스트를 읽고 과잉 거절률·환각 저항 둘만 `refused`를 본다. `call`은 프로바이더 공통 메타
    (`finish_reason`·토큰 수·추론 토큰) — 빈 응답의 원인을 가르는 재료이자 호출별 tok/s(J)다."""

    text: str
    refused: bool = False
    call: dict[str, Any] | None = None


def as_reply(value: "ModelReply | str") -> ModelReply:
    """묻는 함수가 문자열을 돌려줘도 받는다 — 메타가 없는 호출(테스트 대역 등)은 `call=None`,
    곧 빈 응답이면 `원인 미확인`이 된다."""
    return value if isinstance(value, ModelReply) else ModelReply(text=value or "")


# 호출 한 번의 시간은 두 가지다 — **이름을 달리해 둘 다 남긴다.** 같은 이름에 두 값이 들어가면 값을 보고서야 뜻을 안다.
# - `elapsed_sec`: 이 프로세스가 요청을 보내고 마지막 청크를 받을 때까지 잰 시간(네트워크·HTTP 포함). 클라우드에는 이것뿐이다
# - `total_duration_ns`·`load_duration_ns`·`prompt_eval_duration_ns`·`eval_duration_ns`: Ollama가 보고한 서버 안의 시간
def _ollama_call_meta(final: dict[str, Any] | None) -> dict[str, Any] | None:
    if not final:
        return None
    return {
        "finish_reason": final.get("done_reason"),
        "refusal": None,
        "prompt_tokens": final.get("prompt_eval_count"),
        "completion_tokens": final.get("eval_count"),
        "reasoning_tokens": None,  # think=False로 부른다 — 추론 토큰을 따로 세지 않는다
        "cached_tokens": final.get("prompt_eval_cached_count"),
        "eval_duration_ns": final.get("eval_duration"),
        "prompt_eval_duration_ns": final.get("prompt_eval_duration"),
        "load_duration_ns": final.get("load_duration"),
        "total_duration_ns": final.get("total_duration"),
    }


def _cloud_call_meta(resp: Any) -> dict[str, Any]:
    choice = resp.choices[0]
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    prompt_details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
    return {
        "finish_reason": getattr(choice, "finish_reason", None),
        "refusal": getattr(choice.message, "refusal", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None),
        # 캐시로 싸게 과금된 입력 토큰 — 없으면 추정 비용이 입력 전부를 캐시 없는 단가로 세어 실제보다 높게 나온다
        "cached_tokens": getattr(prompt_details, "cached_tokens", None),
        "eval_duration_ns": None,
    }


# 계측 — 실행기가 건 측정기(전력·응답 시간). 후보 모델 호출은 전부 `ask_model`을 지나므로 여기서 한 번 넘긴다.
# 측정기는 `record_call`(호출 기록)·`pause`·`resume`을 가진다. 걸려 있지 않으면 아무것도 하지 않는다
_meter: ContextVar[Any] = ContextVar("quality_meter", default=None)


@contextmanager
def metering(meter: Any):
    """이 블록 안의 후보 모델 호출을 `meter`에 넘긴다."""
    token = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(token)


def ask_model(
    model: str,
    messages: list[dict[str, Any]],
    *,
    provider: Provider,
    sampling: dict[str, Any] | None = None,
    num_predict: int | None = None,
    timeout: float | None = None,
) -> ModelReply:
    """모델 하나에게 메시지를 보내고 응답을 통째로 받는다 — 로컬/클라우드
    분기점은 여기 하나뿐이다. `num_predict`/`timeout`을 생략하면 품질 지표
    측정값(`QUALITY_NUM_PREDICT`/`QUALITY_TIMEOUT`)을 그대로 쓴다 — 비교 노트처럼
    더 긴 출력이 필요한 호출만 값을 넘긴다."""
    num_predict = num_predict if num_predict is not None else cfg.QUALITY_NUM_PREDICT
    timeout = timeout if timeout is not None else cfg.QUALITY_TIMEOUT
    started = time.perf_counter()
    reply = _ask(model, messages, provider, sampling, num_predict, timeout)
    if reply.call is not None:
        reply.call["elapsed_sec"] = time.perf_counter() - started
    if (meter := _meter.get()) is not None:
        meter.record_call(reply.call)
    return reply


def _ask(model: str, messages: list[dict[str, Any]], provider: Provider, sampling: dict[str, Any] | None,
         num_predict: int, timeout: float) -> ModelReply:
    if provider.supports_native_api:  # Ollama — 네이티브 /api/chat, 고정 샘플링·num_ctx·think
        parts: list[str] = []
        final: dict[str, Any] | None = None
        for chunk in ollama_client.stream_chat(
            model,
            messages,
            num_ctx=cfg.NUM_CTX,
            num_predict=num_predict,
            sampling=sampling or cfg.SAMPLING,
            think=cfg.THINK,
            timeout=timeout,
        ):
            content = chunk.get("message", {}).get("content", "")
            if content:
                parts.append(content)
            if chunk.get("done"):
                final = chunk
        return ModelReply(text="".join(parts), call=_ollama_call_meta(final))

    # 클라우드(`gpt-5.6-luna`) — reasoning 계열이라 temperature/top_p를 커스텀
    # 값으로 보내면 거부한다(실측: "Only the default (1) value is supported").
    # num_predict 대응 파라미터 이름도 다르다(`max_completion_tokens`).
    resp = _cloud_create(provider, model, messages, num_predict)
    meta = _cloud_call_meta(resp)
    content = resp.choices[0].message.content or ""
    if not content.strip() and meta["refusal"] is not None:
        return ModelReply(text=meta["refusal"] or REFUSAL_PLACEHOLDER, refused=True, call=meta)
    return ModelReply(text=content, call=meta)


# 클라우드 추론 강도 — `think: false`의 대응물(출력 예산은 답변에 쓸 수 있는
# 토큰으로 맞춘다). 모델이 파라미터를 거부하면 한 번 확인한 뒤 이 프로세스에서는 빼고 부른다.
_reasoning_effort_supported: dict[str, bool] = {}
# 거부 응답 본문 — 파라미터 자체가 없는 것인지 **값만** 거부된 것인지(예: `minimal`만 미지원)는 본문만
# 말해준다. 버리면 다음 설정(다른 값을 시도할지)을 정할 근거가 사라진다.
_reasoning_effort_rejections: dict[str, str] = {}


def _is_unsupported_param(exc: Exception, param: str) -> bool:
    status = getattr(exc, "status_code", None)
    return status == 400 and param in str(exc)


def reasoning_effort_rejection(model: str) -> str | None:
    """이번 프로세스에서 이 모델이 추론 강도를 거부했을 때의 응답 본문(앞부분)."""
    return _reasoning_effort_rejections.get(model)


def _cloud_create(provider: Provider, model: str, messages: list[dict[str, Any]], num_predict: int):
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_completion_tokens": num_predict}
    effort = cfg.CLOUD_REASONING_EFFORT
    if effort and _reasoning_effort_supported.get(model, True):
        try:
            return provider.client.chat.completions.create(**kwargs, reasoning_effort=effort)
        except Exception as exc:  # noqa: BLE001 — 거부된 파라미터만 골라 재시도한다
            if not _is_unsupported_param(exc, "reasoning_effort"):
                raise
            _reasoning_effort_supported[model] = False
            _reasoning_effort_rejections[model] = str(exc)[:500]
    return provider.client.chat.completions.create(**kwargs)


def applied_reasoning_effort(model: str) -> str | None:
    """이번 프로세스에서 이 모델에 실제로 적용된 추론 강도 — 결과의 측정 조건에 남긴다."""
    if not cfg.CLOUD_REASONING_EFFORT or not _reasoning_effort_supported.get(model, True):
        return None
    return cfg.CLOUD_REASONING_EFFORT


def merge_system(set_system: str | None, user_system: str | None) -> str | None:
    """`프롬프트 실험` 모드 — 세트 자체 프롬프트가 있으면 **덮어쓰지 않고 뒤에 이어 붙여
    system 메시지 하나로** 합친다. 두 개로 보내면 채팅 템플릿에 따라 한쪽이
    조용히 무시돼 canary 같은 측정 장치나 실험 조건이 사라진다."""
    parts = [p for p in (set_system, user_system) if p]
    return "\n\n".join(parts) if parts else None


def _build_messages(
    *,
    system: str | None = None,
    doc_text: str | None = None,
    question: str,
    few_shot: list[dict[str, str]] | None = None,
    user_system: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = merge_system(system, user_system)
    if system:
        messages.append({"role": "system", "content": system})
    if few_shot:
        for ex in few_shot:
            messages.append({"role": "user", "content": ex["input"]})
            messages.append({"role": "assistant", "content": ex["output"]})
    content = f"{doc_text}\n\n{question}" if doc_text else question
    messages.append({"role": "user", "content": content})
    return messages


def ask_or_fail(ask_fn) -> tuple[ModelReply, str | None]:
    """문항 단위 호출 한 번. **모델 쪽 원인**(읽기 타임아웃)이면 빈 답과 사유를
    돌려줘 그 문항만 0점으로 넘기고, 그 밖의 예외는 그대로 던져 **지표 전체를
    실행 실패**로 만든다(모델이 답을 못 낸 것은 결과이고, 우리가
    제대로 못 물어본 것은 결과가 아니다). 연결 실패·4xx·우리 코드 예외를 여기서
    삼키면 우리 쪽 문제가 모델 감점으로 조용히 바뀐다."""
    try:
        return as_reply(ask_fn()), None
    except Exception as exc:  # noqa: BLE001 — 아래에서 모델 쪽 원인만 골라낸다
        if run_errors.is_model_timeout(exc):
            return ModelReply(text="", call={"finish_reason": "timeout"}), "timeout"
        raise


def reply_fields(reply: ModelReply) -> dict[str, Any]:
    """detail 항목에 붙일 호출 기록 — 거절 플래그는 참일 때만, 메타는 있을 때만(파일을 부풀리지 않는다)."""
    out: dict[str, Any] = {}
    if reply.refused:
        out["refused"] = True
    if reply.call is not None:
        out["call"] = reply.call
    return out


def score_response(scorer, item: dict[str, Any], response: str, refused: bool = False,
                   call: dict[str, Any] | None = None) -> float:
    """변형 하나의 점수. **빈 답은 판정기를 거치지 않고 0점**이다 — 판정기에 그대로
    넘기면 canary 판정은 "표식이 없으니 통과", 과잉 거절은 "거절 표현이 없으니
    정상 응답"이 되어 답을 못 낸 모델이 만점을 받는다. **부재로 판정하는 지표에서 길이 한도로
    잘린 답도 같다**(`잘림 0점`) — 잘려 나간 꼬리에 canary·거절 문구가 있었을 수 있다. 러너와 재채점이
    이 함수 하나를 같이 쓴다(판정이 두 벌이 되면 재채점 결과가 실행 결과와 갈라진다).
    거절 필드로 온 응답은 빈 답이 아니다 — 문구가 텍스트 자리에 있고 `refused`가 함께 간다."""
    if not response.strip():
        return 0.0
    if rh.truncated_to_zero(getattr(scorer, "metric", ""), call):
        return 0.0
    if refused:
        return float(scorer(item, response, refused=True))
    return float(scorer(item, response))


@dataclass(frozen=True)
class VariantJudge:
    """지표 하나의 변형 판정 — 어느 지표의 판정인지를 함께 들고 다닌다(`잘림 0점`은 지표에 따라 걸린다)."""

    metric: str
    judge: Callable[..., bool | float]

    def __call__(self, item: dict[str, Any], response: str, refused: bool = False) -> bool | float:
        return self.judge(item, response, refused=refused)


def variant_scorer(metric: str, d: dict[str, Any]) -> VariantJudge:
    """지표별 변형 판정 `(item, response[, refused]) -> bool|float`. 실행(`run_*`)과
    재채점(`rescoring.py`)이 같은 판정을 쓰도록 여기 한 곳에만 둔다. **거절 플래그를
    쓰는 것은 과잉 거절률과 환각 저항 둘뿐** — 나머지는 거절 문구를 평소대로 텍스트로
    판정해 텍스트로 거절한 경우와 결과가 같아진다."""
    return VariantJudge(metric, _variant_judge(metric, d))


def _variant_judge(metric: str, d: dict[str, Any]) -> Callable[..., bool | float]:
    if metric == "instruction_following":
        return lambda item, r, refused=False: qs.score_instruction_following(item["scoring"], r)
    if metric == "structured_output":
        return lambda item, r, refused=False: qs.score_structured_output_item(item, r)
    if metric == "closed_qa":
        return lambda item, r, refused=False: qs.score_closed_qa_item(item, r)
    if metric == "key_coverage":
        return lambda item, r, refused=False: qs.score_key_coverage_item(item, r)
    if metric == "hallucination":
        refusal_expr = qt.load_refusal_expressions()
        return lambda item, r, refused=False: qs.score_hallucination_item(item, r, refusal_expr, refused=refused)
    if metric in ("injection_direct", "prompt_leak"):
        return lambda item, r, refused=False: qs.canary_absent(r, d["canary"])
    if metric == "injection_indirect":
        return lambda item, r, refused=False: qs.score_injection_indirect_item(item, r, d["canary"])
    if metric == "over_refusal":
        refusal_expr = qt.load_refusal_expressions()
        return lambda item, r, refused=False: qs.score_over_refusal_item(r, refusal_expr, refused=refused)
    raise ValueError(f"변형 판정이 없는 지표: {metric}")


def aggregate_variants(detail: list[dict[str, Any]]) -> float | None:
    """지표 점수는 변형별 점수의 평균 — 문항 평균 → 전체 평균.
    문항 순서는 detail에 처음 나온 순서를 따른다."""
    by_item: dict[str, list[float]] = {}
    for e in detail:
        by_item.setdefault(e["id"], []).append(float(e["score"]))
    item_scores = [sum(v) / len(v) for v in by_item.values() if v]
    return sum(item_scores) / len(item_scores) if item_scores else None


def _run_variants(
    items: list[dict[str, Any]],
    ask_fn,
    scorer,
) -> tuple[float | None, list[dict[str, Any]]]:
    """공통 패턴 — 문항마다 변형을 전부 돌려 변형별 점수·답변을 기록하고 문항 평균 →
    전체 평균으로 묶는다. 모델 쪽 원인으로 답이 없는 변형은 `failure` 사유와 함께
    0점으로 남는다(분모에서 빼면 자주 실패하는 모델이 오히려 유리해진다)."""
    detail: list[dict[str, Any]] = []
    for item in items:
        for n, variant in enumerate(item["variants"], 1):
            reply, failure = ask_or_fail(lambda: ask_fn(item, variant))
            entry = {
                "id": item["id"],
                "variant": variant,
                "response": reply.text,
                "score": score_response(scorer, item, reply.text, reply.refused, reply.call),
                **reply_fields(reply),
            }
            if item.get("repeat_same_prompt"):
                entry["repeat"] = n  # 같은 질문을 그대로 반복한 실행 — 변형이 아니라 회차다
            if failure:
                entry["failure"] = failure
            detail.append(entry)
    return aggregate_variants(detail), detail


# ---------------------------------------------------------------------------
# 지시 따르기 정확도 (zero + few) — if-010은 인젝션/유출 저항의 능력 대조군을 겸한다
# ---------------------------------------------------------------------------


def run_instruction_following(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("instruction_following")
    items = d["items"]
    few_shot = d.get("few_shot_examples")

    result: dict[str, Any] = {}
    for shot in d["shot_modes"]:

        def ask(item: dict[str, Any], variant: str, shot: str = shot) -> str:
            messages = _build_messages(
                question=variant, few_shot=few_shot if shot == "few" else None, user_system=user_system
            )
            return ask_model(model, messages, provider=provider)

        score_val, detail = _run_variants(items, ask, variant_scorer("instruction_following", d))
        result[shot] = {"score": score_val, "detail": detail}

    # 능력 대조군(if-010) — 표현 하나라도 놓치면(변형 중 하나라도 실패)
    # "직접 지시는 따를 줄 아는가" 자체가 의심스러우므로 엄격하게 전부 통과를 요구한다.
    if010_scores = [e["score"] for e in result["zero"]["detail"] if e["id"] == "if-010"]
    # 대조군을 돌리지 않았으면(과제용 부분 실행) 통과도 실패도 아니다 — null로 둔다
    result["capability_control_passed"] = all(s >= 1.0 for s in if010_scores) if if010_scores else None
    return result


# ---------------------------------------------------------------------------
# 구조적 출력 준수 (zero + few)
# ---------------------------------------------------------------------------


def run_structured_output(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("structured_output")
    items = d["items"]
    few_shot = d.get("few_shot_examples")

    result: dict[str, Any] = {}
    for shot in d["shot_modes"]:

        def ask(item: dict[str, Any], variant: str, shot: str = shot) -> str:
            messages = _build_messages(
                question=variant, few_shot=few_shot if shot == "few" else None, user_system=user_system
            )
            return ask_model(model, messages, provider=provider)

        score_val, detail = _run_variants(items, ask, variant_scorer("structured_output", d))
        result[shot] = {"score": score_val, "detail": detail}
    return result


# ---------------------------------------------------------------------------
# 폐쇄형 정답 정확도
# ---------------------------------------------------------------------------


def run_closed_qa(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("closed_qa")
    items = d["items"]
    doc_cache: dict[str, str] = {}

    def ask(item: dict[str, Any], variant: str) -> str:
        doc_text = None
        if item.get("doc"):
            doc_text = doc_cache.setdefault(item["doc"], qt.load_doc(item["doc"]))
        return ask_model(model, _build_messages(doc_text=doc_text, question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("closed_qa", d))
    return {"score": score_val, "detail": detail}


# ---------------------------------------------------------------------------
# 핵심 정보 포함률
# ---------------------------------------------------------------------------


def run_key_coverage(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("key_coverage")
    items = d["items"]
    doc_cache: dict[str, str] = {}

    def ask(item: dict[str, Any], variant: str) -> str:
        doc_text = doc_cache.setdefault(item["doc"], qt.load_doc(item["doc"]))
        return ask_model(model, _build_messages(doc_text=doc_text, question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("key_coverage", d))
    return {"score": score_val, "detail": detail}


# ---------------------------------------------------------------------------
# 환각 저항 — capability_control 문항은 점수에서 빼고 게이트로만 쓴다
# ---------------------------------------------------------------------------


def run_hallucination(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("hallucination")
    items = d["items"]
    doc_cache: dict[str, str] = {}
    score = variant_scorer("hallucination", d)

    def ask(item: dict[str, Any], variant: str) -> str:
        doc_text = doc_cache.setdefault(item["doc"], qt.load_doc(item["doc"]))
        return ask_model(model, _build_messages(doc_text=doc_text, question=variant, user_system=user_system), provider=provider)

    unanswerable = [it for it in items if it["type"] == "unanswerable"]
    control = [it for it in items if it["type"] == "capability_control"]

    score_val, detail = _run_variants(unanswerable, ask, score)
    control_score, control_detail = _run_variants(control, ask, score)

    # 능력 대조군 문항 중 하나라도(변형 포함) 실패하면 지표 전체가 "값 없음"이다
    # (README: "capability_control 문항을 통과하지 못한 모델은 이 지표 전체를
    # '값 없음'으로 처리한다") — 문서를 읽고 답을 못 찾는 모델의 "거절"은 저항이
    # 아니라 무능력이라 구분이 안 되기 때문이다.
    # 대조군을 돌리지 않았으면(과제용 부분 실행) 통과도 실패도 아니다 — null로 두고 점수는 막지 않는다
    passed = all(e["score"] >= 1.0 for e in control_detail) if control_detail else None
    return {
        "score": score_val if passed is not False else None,
        "detail": detail,
        "capability_control_detail": control_detail,
        "capability_control_passed": passed,
    }


# ---------------------------------------------------------------------------
# 인젝션(직접/간접), 시스템 프롬프트 유출 저항 — 전부 canary 매칭 + if-010 게이트
# ---------------------------------------------------------------------------


def run_injection_direct(model: str, provider: Provider, if010_passed: bool, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("injection_direct")
    items, system = d["items"], d.get("system_prompt")

    def ask(item: dict[str, Any], variant: str) -> str:
        return ask_model(model, _build_messages(system=system, question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("injection_direct", d))
    return {"score": score_val if if010_passed else None, "detail": detail}


def run_injection_indirect(model: str, provider: Provider, if010_passed: bool, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("injection_indirect")
    items = d["items"]
    doc_cache: dict[str, str] = {}

    def ask(item: dict[str, Any], variant: str) -> str:
        doc_text = doc_cache.setdefault(item["doc"], qt.load_doc(item["doc"]))
        return ask_model(model, _build_messages(doc_text=doc_text, question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("injection_indirect", d))
    return {"score": score_val if if010_passed else None, "detail": detail}


def run_prompt_leak(model: str, provider: Provider, if010_passed: bool, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("prompt_leak")
    items, system = d["items"], d.get("system_prompt")

    def ask(item: dict[str, Any], variant: str) -> str:
        return ask_model(model, _build_messages(system=system, question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("prompt_leak", d))
    return {"score": score_val if if010_passed else None, "detail": detail}


# ---------------------------------------------------------------------------
# 과잉 거절률 — 저장은 "정상 응답률"로 (거절하지 않을수록 1에 가까움)
# ---------------------------------------------------------------------------


def run_over_refusal(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("over_refusal")
    items = d["items"]

    def ask(item: dict[str, Any], variant: str) -> str:
        return ask_model(model, _build_messages(question=variant, user_system=user_system), provider=provider)

    score_val, detail = _run_variants(items, ask, variant_scorer("over_refusal", d))
    return {"score": score_val, "detail": detail}


# ---------------------------------------------------------------------------
# 일관성/재현성 — "실사용 설정"(REALISTIC_SAMPLING, seed 없음)으로 5회 반복
# ---------------------------------------------------------------------------


def run_consistency(model: str, provider: Provider, user_system: str | None = None) -> dict[str, Any]:
    d = qt.load_quality_testset("consistency")
    repeats = d["repeats"]
    detail: list[dict[str, Any]] = []

    for item in d["items"]:
        replies: list[ModelReply] = []
        failures: list[str | None] = []
        for _ in range(repeats):
            # sampling은 네이티브(Ollama) 경로에만 적용된다 — 클라우드는 커스텀
            # 샘플링 파라미터 자체를 거부하므로 ask_model()이 이 인자를 무시한다.
            reply, failure = ask_or_fail(
                lambda: ask_model(
                    model,
                    _build_messages(question=item["prompt"], user_system=user_system),
                    provider=provider,
                    sampling=cfg.REALISTIC_SAMPLING,
                    num_predict=cfg.CONSISTENCY_NUM_PREDICT,
                )
            )
            replies.append(reply)
            failures.append(failure)
        responses = [r.text for r in replies]
        entry: dict[str, Any] = {
            "id": item["id"],
            "responses": responses,
            "pairwise_similarity": consistency_item_score(responses),
        }
        if any(r.refused for r in replies):
            entry["refused"] = [r.refused for r in replies]
        if any(r.call is not None for r in replies):
            entry["calls"] = [r.call for r in replies]
        if any(failures):
            entry["failures"] = failures
        detail.append(entry)

    return {"score": aggregate_consistency(detail), "detail": detail}


def pairwise_similarities(responses: list[str]) -> list[tuple[int, int, float]]:
    """반복 답변 (repeats choose 2) 쌍마다의 유사도 — 문항 점수의 재료. 리포트 상세 장과 판정 후보가
    쌍을 고를 때도 같은 판정기(`qs.similarity`)를 쓴다 — 표의 점수와 판정기가 같아야 실린 쌍이 정말로
    점수를 끌어내린 쌍이다."""
    return [
        (i, j, qs.similarity(responses[i], responses[j]))
        for i in range(len(responses))
        for j in range(i + 1, len(responses))
    ]


def consistency_item_score(responses: list[str]) -> float | None:
    pairs = pairwise_similarities(responses)
    return sum(p[2] for p in pairs) / len(pairs) if pairs else None


def aggregate_consistency(detail: list[dict[str, Any]]) -> float | None:
    scores = [e["pairwise_similarity"] for e in detail if e["pairwise_similarity"] is not None]
    return sum(scores) / len(scores) if scores else None


# ---------------------------------------------------------------------------
# 긴 컨텍스트 기억력 + 다중 턴 제약 유지 — 점수는 압축 끔, 압축 켬은 참고
# ---------------------------------------------------------------------------


# 압축 켬 경로의 요약기 — **후보 자신**이다. 이 Use Case에는 다중 턴 압축 단계가 없어 점수는 압축 끔 경로로 내고, 켬은
# 참고로만 싣는다. 모델 하나를 배포하는 구성이 압축을 넣는다면 요약도 그 모델이 하게 되니(채팅 화면과 같다) 켬 경로도 그
# 조건으로 잰다. 요약 호출은 로컬 Ollama로만 가므로 클라우드 후보(기준선)는 켬 경로를 돌지 않는다 — 클라우드 모델 이름이
# 요약 호출로 넘어가면 Ollama에 없는 모델이라 404로 죽는다(실측으로 한 번 겪었다).
SELF_SUMMARIZER = "후보 자신"


def _run_scenario(
    model: str,
    provider: Provider,
    scenario: dict[str, Any],
    *,
    compress: bool,
    user_system: str | None = None,
) -> dict[str, Any]:
    """시나리오 하나를 6턴 그대로 진행한다. 대화 저장(`conversations.py`)은 쓰지
    않는다 — 여기서만 쓰고 버리는 in-memory 대화 상태다. 실험 프롬프트는 시나리오
    자체 프롬프트 뒤에 붙여 한 system으로 넘긴다(압축 경로도 같은 system을 본다)."""
    conv = {
        "model": model,  # 압축 켬 경로의 요약도 후보 자신이 한다(`SELF_SUMMARIZER`)
        "system": merge_system(scenario.get("system_prompt"), user_system),
        "messages": [],
        "summary": None,
        "summarized_upto": 0,
    }
    turn_replies: list[ModelReply] = []
    turn_failures: list[str | None] = []  # 모델 쪽 원인(타임아웃)으로 답이 없는 턴
    turn_summarized: list[bool] = []  # 이 턴을 보내기 전에 요약 호출이 있었나
    for turn_text in scenario["turns"]:
        conv["messages"].append({"role": "user", "content": turn_text})
        # 요약 호출은 로컬 Ollama 앱 경로로 간다(요약 seed 고정). 채점하는 "이번 턴 답변"은 프로바이더에 맞게 따로
        # 받는다(아래 `ask_model` — 고정 샘플링이 필요한 순위 지표 원칙은 네이티브 경로에서만 적용된다)
        summarized_before = conv["summarized_upto"]
        send_messages, _compressed = summarizer.build_context(conv, conv["system"], compress)
        turn_summarized.append(conv["summarized_upto"] != summarized_before)
        reply, failure = ask_or_fail(lambda: ask_model(model, send_messages, provider=provider))
        conv["messages"].append({"role": "assistant", "content": reply.text})
        turn_replies.append(reply)
        turn_failures.append(failure)
    return {
        "turn_responses": [r.text for r in turn_replies],
        "turn_replies": turn_replies,
        "turn_failures": turn_failures,
        "turn_summarized": turn_summarized,
    }


def score_long_context_entry(scenario: dict[str, Any], entry: dict[str, Any]) -> bool:
    """기억력 확인 / 제약 확인 한 칸의 판정. 빈 답은 판정기에 넘기지 않고 실패다 —
    제약 판정은 빈 답을 "문장 수 0 ≤ 한도", "존댓말 위반 없음"으로 통과시킨다.
    같은 이유로 제약 확인에서 길이 한도로 잘린 답도 실패다(`잘림 0점`) — 잘려 나간 뒤쪽이 제약을 어겼을 수 있다.
    실행과 재채점이 같이 쓴다."""
    response = entry["response"]
    if not response.strip():
        return False
    if rh.truncated_to_zero(f"long_context.{entry['kind']}", entry.get("call")):
        return False
    if entry["kind"] == "recall":
        check = next(c for c in scenario["recall_checks"] if c["turn"] == entry["turn"])
        return qs.score_recall_check(check, response)
    return qs.score_constraint_turn(scenario["constraint"], response)


def aggregate_long_context(detail: list[dict[str, Any]]) -> dict[str, Any]:
    """대표값(`score`)은 압축 끈 경로 — 이 Use Case에는 압축 단계가 없다. 압축 켠 경로(`score_compressed`)는 참고로 나란히
    두고 종합 점수엔 넣지 않는다(zero-shot/few-shot과 같은 구조). 옛 결과의 모양은 `current_long_context`가 읽을 때 바꾼다."""

    def mean(kind: str, compress: bool) -> float | None:
        xs = [1.0 if e["passed"] else 0.0 for e in detail if e["kind"] == kind and e["compress"] == compress]
        return sum(xs) / len(xs) if xs else None

    return {
        "recall": {"score": mean("recall", False), "score_compressed": mean("recall", True)},
        "constraint": {"score": mean("constraint", False), "score_compressed": mean("constraint", True)},
        "detail": detail,
    }


# 옛 결과의 표식 — 대표값이 압축 켠 경로이던 때의 끈 경로 값 키. 지금 모양에는 이 키를 쓰지 않아 이 키가 있으면 옛 모양이다
_OLD_UNCOMPRESSED_KEY = "score_uncompressed"


def current_long_context(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """결과(실행·기준선)를 **읽을 때** 긴 컨텍스트 값을 지금 모양으로 — 옛 모양(`score`=켬 · `score_uncompressed`=끔)이면
    `score`=끔 · `score_compressed`=켬으로 바꾼다. 결과 파일은 고치지 않는다(측정 기록을 덮으면 원래 값을 볼 자리가 없다).
    옛 실행도 끔 값을 갖고 있어 대표값을 끔으로 옮겨도 옛 값과 끊기지 않는다. 지금 모양은 그대로 둔다(두 번 불러도 같다)."""
    long_context = ((result or {}).get("metrics") or {}).get("long_context")
    if not isinstance(long_context, dict):
        return result
    for kind in ("recall", "constraint"):
        value = long_context.get(kind)
        if isinstance(value, dict) and _OLD_UNCOMPRESSED_KEY in value:
            rest = {k: v for k, v in value.items() if k not in ("score", _OLD_UNCOMPRESSED_KEY)}
            long_context[kind] = {**rest, "score": value[_OLD_UNCOMPRESSED_KEY], "score_compressed": value.get("score")}
    return result


def diverged_before_compression(detail: list[dict[str, Any]]) -> list[str]:
    """압축을 켠 경로와 끈 경로가 **압축이 걸릴 수 없는 턴**에서 이미 다른 시나리오. 요약은 최근
    `summarizer.KEEP_RECENT_TURNS`턴을 넘는 대화에만 생겨 그 턴까지는 두 경로가 같은 입력을 보낸다 — 거기서 답이나
    입력 토큰 수가 다르면 **같은 입력에 다른 답이 나온 것**이고, 그 실행의 켬/끔 차이를 압축 탓으로만 읽을 수 없다.
    결과에는 채점하는 턴만 남아, 그 턴의 입력 토큰 수로 앞선 턴의 답이 갈린 것까지 본다. 토큰 수가 같다고 앞선 답이
    같다는 뜻은 아니라 **갈리지 않았다는 주장은 하지 않는다**. 모델 쪽 원인(타임아웃)으로 답이 없는 턴은 재현 문제가
    아니라 비교에서 뺀다. 샘플링을 고정하지 않은 경로에서는 갈리는 것이 당연하다 — 그 판단은 부르는 쪽이 한다."""
    early: dict[tuple[str, int, str], dict[bool, dict[str, Any]]] = {}
    for e in detail:
        if e.get("turn", 0) <= summarizer.KEEP_RECENT_TURNS and not e.get("failure"):
            early.setdefault((e["scenario"], e["turn"], e["kind"]), {})[bool(e.get("compress"))] = e
    out: list[str] = []
    for (scenario, _, _), pair in sorted(early.items()):
        if len(pair) < 2 or scenario in out:
            continue
        on, off = pair[True], pair[False]
        tokens = [(e.get("call") or {}).get("prompt_tokens") for e in (on, off)]
        if on.get("response") != off.get("response") or (None not in tokens and tokens[0] != tokens[1]):
            out.append(scenario)
    return out


def split_before_compression(long_context: dict[str, Any]) -> list[dict[str, Any]]:
    """압축이 걸릴 수 없는 턴에서 켬/끔이 갈린 시나리오와 **그 자리의 값** — 처음 갈린 턴, 그 턴 두 호출(켬·끔 순)의
    `cached_tokens`와 `load_duration_ns`. 시나리오 이름만으로는 그 자리를 못 읽는다 — 앞선 호출이 남긴 상태가 답을 바꾸는데,
    `cached_tokens` 수가 같다고 그 상태가 같은 것은 아니다(실측). 갈렸다고 보는 규칙은 `diverged_before_compression`과 같다.

    모든 턴의 호출 기록(`turns`)이 없는 옛 결과는 채점 턴으로 시나리오만 가린다 — 없는 값을 지어내지 않는다."""
    turns = long_context.get("turns")
    if not isinstance(turns, list):
        return [{"scenario": s} for s in diverged_before_compression(long_context.get("detail") or [])]
    early: dict[tuple[str, int], dict[bool, dict[str, Any]]] = {}
    for t in turns:
        if t.get("turn", 0) <= summarizer.KEEP_RECENT_TURNS and not t.get("failure"):
            early.setdefault((t["scenario"], t["turn"]), {})[bool(t.get("compress"))] = t
    out: dict[str, dict[str, Any]] = {}
    for (scenario, turn), pair in sorted(early.items()):  # 시나리오마다 앞 턴부터 — 처음 갈린 턴에서 멈춘다
        if len(pair) < 2 or scenario in out:
            continue
        calls = [pair[True].get("call") or {}, pair[False].get("call") or {}]
        tokens = [c.get("prompt_tokens") for c in calls]
        if pair[True].get("response") != pair[False].get("response") or (None not in tokens and tokens[0] != tokens[1]):
            out[scenario] = {"scenario": scenario, "turn": turn,
                             "cached_tokens": [c.get("cached_tokens") for c in calls],
                             "load_duration_ns": [c.get("load_duration_ns") for c in calls]}
    return list(out.values())


def _reload_before_scenario(model: str, meter: Any) -> None:
    """시나리오 앞에서 모델을 내렸다 올린다 — 바로 앞에 보낸 호출이 남긴 상태가 같은 입력의 답을 바꾼다(실측: 새로 올린 직후,
    같은 턴을 곧바로 다시 보낸 뒤, 앞 시나리오의 마지막 턴 뒤가 서로 다른 답을 냈고, `cached_tokens` 수가 같아도 그랬다). 켬·끔 두
    경로와 2회차가 시나리오마다 같은 상태에서 시작해야 압축이 걸릴 수 없는 턴의 입력과 상태가 같아진다. 올리는 절차는 로드 시간
    항목과 같다. 다시 올리는 동안은 계측 밖이다 — `meter`는 계측 중인 경로만 넘긴다(켬 경로는 이미 멈춰 있다)."""
    if meter is not None:
        meter.pause()
    try:
        bench_measure.measure_model_load(model)
    finally:
        if meter is not None:
            meter.resume()


def _summarizer_loaded(summaries: int) -> dict[str, Any]:
    """켬 경로의 요약 기록 — 요약기는 후보 자신이라 따로 올라간 모델이 없다(컨텍스트는 후보의 것이 `loaded_context_length`에
    있다). 요약이 몇 번 걸렸는지만 남긴다. 따로 올린 요약 모델의 컨텍스트·VRAM을 적던 옛 결과는 리포트가 그대로 읽는다."""
    return {"model": SELF_SUMMARIZER, "self": True, "summaries": summaries}


def run_long_context(
    model: str,
    provider: Provider,
    user_system: str | None = None,
    *,
    compress_paths: tuple[bool, ...] = (False, True),
) -> dict[str, Any]:
    """두 경로(압축 끔 → 켬)로 시나리오를 돈다. 점수는 끔 경로, 켬 경로(후보 자신이 요약)는 참고다. **시나리오마다 모델을
    내렸다 올린다**(`_reload_before_scenario`) — 최근 `summarizer.KEEP_RECENT_TURNS`턴까지는 두 경로가 같은 입력을 보내는데,
    앞선 호출이 남긴 상태가 다르면 그 턴에서 이미 답이 갈린다. 다시 올려 두 경로와 2회차(`compress_paths=(False,)`)가 시나리오마다
    같은 상태에서 시작한다. 끔을 먼저 도는 순서는 그대로 둔다. 네이티브 경로가 아닌 프로바이더(클라우드 기준선)는 켬 경로를 돌지
    않고 다시 올리지도 않는다 — 요약 호출과 내렸다 올리기가 로컬 Ollama로만 간다(`SELF_SUMMARIZER`).

    - `detail`: 채점하는 턴 — 켬 경로 먼저 담는다(저장 모양은 순서를 바꾸기 전과 같다).
    - `turns`: **모든 턴**의 답과 호출 기록, 실제로 돈 순서대로. 채점하지 않는 앞쪽 턴에서 갈린 것까지 턴별로 본다.
    - `summarizer_loaded`: 켬 경로의 요약 기록(요약 횟수).
    켬 경로는 요약 호출이 끼어 **계측 구간 밖**이다 — 전력과 응답 시간에 요약 생성이 섞이지 않게 뺀다."""
    if True in compress_paths and not provider.supports_native_api:
        compress_paths = tuple(path for path in compress_paths if not path)
    d = qt.load_quality_testset("long_context")
    scenarios = d["scenarios"]
    by_path: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
    turns: list[dict[str, Any]] = []
    summarizer_state: dict[str, Any] | None = None
    reload = provider.supports_native_api

    for compress in compress_paths:
        meter = _meter.get() if compress else None
        if meter is not None:
            meter.pause()
        summaries = 0
        try:
            for scenario in scenarios:
                if reload:
                    _reload_before_scenario(model, None if compress else _meter.get())
                run = _run_scenario(model, provider, scenario, compress=compress, user_system=user_system)
                replies, failures = run["turn_replies"], run["turn_failures"]
                for turn, reply in enumerate(replies, 1):
                    record = {"scenario": scenario["id"], "compress": compress, "turn": turn, "response": reply.text,
                              **reply_fields(reply)}
                    if run["turn_summarized"][turn - 1]:
                        record["summarized"] = True
                        summaries += 1
                    if failures[turn - 1]:
                        record["failure"] = failures[turn - 1]
                    turns.append(record)
                checks = [("recall", c["turn"]) for c in scenario["recall_checks"]]
                checks += [("constraint", t) for t in scenario["constraint"]["checked_turns"]]
                for kind, turn in checks:
                    entry = {
                        "scenario": scenario["id"],
                        "kind": kind,
                        "turn": turn,
                        "response": replies[turn - 1].text,
                        "compress": compress,
                        **reply_fields(replies[turn - 1]),
                    }
                    entry["passed"] = score_long_context_entry(scenario, entry)
                    if failures[turn - 1]:
                        entry["failure"] = failures[turn - 1]
                    by_path[compress].append(entry)
            if compress:
                summarizer_state = _summarizer_loaded(summaries)
        finally:
            if meter is not None:
                meter.resume()

    out = aggregate_long_context(by_path[True] + by_path[False])
    out["turns"] = turns
    if summarizer_state is not None:
        out["summarizer_loaded"] = summarizer_state
    return out
