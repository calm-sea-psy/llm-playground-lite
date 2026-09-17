"""대화 컨텍스트 압축 — 오래된 구간을 요약으로 치환해 모델에 보낼 messages를 만든다.

임계값(문자 수 기준)은 지금 단계에서 실제 테스트해볼 수 있는 수준으로 잡은
placeholder다. 실제 값은 `num_ctx`(16384)가 확정된 뒤 토큰 기준으로
재조정한다. 문자 수를 쓰는 이유는 로컬에서 토큰 수를 세려면
모델 호출이 필요해서고, 문자 수도 "임계값" 기준으로는 허용되는 방식이다.

`conv["messages"]`에는 도구 호출 턴(`role: assistant`, `tool_calls`
있음, `content: null`)과 도구 결과 턴(`role: tool`)이 섞인다. 저장 형태를 모델에 보낼
모양으로 바꾸는 일은 `to_wire()`가 압축 켬/끔 두 경로 모두에 한 번만 한다("문서 두 벌 안 만들기"와
같은 이유로, 변환 로직도 한 곳에만 둔다).

요약 호출은 네이티브 `/api/chat`으로, 대화 모델과 같은 컨텍스트로 부른다(`ollama_client.app_chat`). 컨텍스트가 다르면
같은 모델을 요약과 대화가 번갈아 부를 때마다 모델이 다시 올라간다.
"""

from typing import Any

import ollama_client

SUMMARY_TRIGGER_CHARS = 2000
KEEP_RECENT_TURNS = 3  # 최근 N턴(사용자+어시스턴트 쌍)은 원본 그대로 보낸다
# 요약 호출의 온도 — seed는 보내지 않는다. 요약이 끼는 경로는 그래서 고정 샘플링이 아니다(측정 조건에 이 값을 적는다)
SUMMARY_TEMPERATURE = 0.3

_SUMMARY_PROMPT = """다음은 지금까지의 대화 요약이다(없으면 "(없음)"):
{prior_summary}

아래는 그 이후 새로 오간 대화다. 기존 요약과 새 대화를 합쳐, 핵심 사실과 \
맥락을 잃지 않도록 간결한 서술형 요약 하나로 다시 써라. 대화체로 옮기지 말고, \
있었던 일과 정해진 내용 위주로 적어라.

{transcript}"""


def _char_len(messages: list[dict[str, Any]]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def _describe_tool_call(call: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in call.get("arguments", {}).items())
    return f"{call['name']}({args})"


def _transcript(messages: list[dict[str, Any]]) -> str:
    """요약 프롬프트용 대화록. 도구 호출/결과 턴은 텍스트로 풀어서 적는다
    — content가 원래 null이라 그대로 두면
    요약 프롬프트에 "assistant: None"처럼 찍혀 의미가 사라진다."""
    lines = []
    for m in messages:
        if m.get("tool_calls"):
            calls = ", ".join(_describe_tool_call(c) for c in m["tool_calls"])
            lines.append(f"assistant: [도구 호출: {calls}]")
        elif m["role"] == "tool":
            lines.append(f"tool: [도구 결과: {m['content']}]")
        else:
            lines.append(f"{m['role']}: {m['content']}")
    return "\n".join(lines)


def to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """저장 형태 -> 네이티브 `/api/chat`의 메시지. 네이티브는 `arguments`를 JSON 문자열이 아니라 객체로 받는다.
    압축 켬/끔 두 경로 모두 모델에 보내기 직전 이 함수를 한 번 거친다. 실측으로 OpenAI 호환 `/v1`에 문자열로 보낸
    같은 대화와 입력 토큰 수·답이 같았다."""
    out = []
    for m in messages:
        if m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c.get("arguments", {})},
                        }
                        for c in m["tool_calls"]
                    ],
                }
            )
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _summarize(model: str, prior_summary: str | None, turns: list[dict[str, Any]]) -> str:
    """같은 로컬 모델에게 별도 호출로 요약을 생성시킨다."""
    prompt = _SUMMARY_PROMPT.format(
        prior_summary=prior_summary or "(없음)",
        transcript=_transcript(turns),
    )
    parts = [
        chunk.get("message", {}).get("content", "")
        for chunk in ollama_client.app_chat(model, [{"role": "user", "content": prompt}], temperature=SUMMARY_TEMPERATURE)
    ]
    return "".join(parts).strip()


def build_context(
    conv: dict[str, Any],
    system: str | None,
    compress: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Ollama에 실제로 보낼 messages를 만든다.

    압축이 이번 턴에 새로 적용되면 `conv`의 `summary`/`summarized_upto`를
    그 자리에서 갱신한다(호출자가 이후 저장한다). 반환하는 두 번째 값은
    "이 턴의 결과에 요약이 관여했는가"이고, 압축을 켰든 껐든 이전에 만들어둔
    요약이 있으면 켠 경로에서는 True가 된다.

    `compress=False`는 품질 지표의 대조군 경로다 — 저장된 요약이 있어도 쓰지 않고
    원본 메시지 전체를 보낸다. 이러면 "정보가 사라진 원인이 모델인지 압축
    로직인지"를 압축 켠 결과와 나란히 비교해 가려낼 수 있다.
    """
    messages = conv["messages"]

    if not compress:
        send = to_wire(messages)
        if system:
            send = [{"role": "system", "content": system}, *send]
        return send, False

    summarized_upto = conv.get("summarized_upto", 0)
    unsummarized = messages[summarized_upto:]
    keep_count = KEEP_RECENT_TURNS * 2  # 사용자+어시스턴트 = 1턴당 메시지 2개

    if len(unsummarized) > keep_count and _char_len(unsummarized) > SUMMARY_TRIGGER_CHARS:
        to_summarize = unsummarized[:-keep_count]
        recent = unsummarized[-keep_count:]
        conv["summary"] = _summarize(conv["model"], conv.get("summary"), to_summarize)
        conv["summarized_upto"] = summarized_upto + len(to_summarize)
    else:
        recent = unsummarized

    send: list[dict[str, Any]] = []
    if system:
        send.append({"role": "system", "content": system})
    if conv.get("summary"):
        send.append({"role": "system", "content": f"[이전 대화 요약]\n{conv['summary']}"})
    send.extend(to_wire(recent))
    return send, bool(conv.get("summary"))
