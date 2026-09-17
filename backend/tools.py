"""도구 호출 루프.

실제 7개 도구(`agent_tools.py`)를 쓰는 tool-calling 루프다. 핵심 설계:

- **위험 도구(`destructive=True`, 지금은 `delete_model`)는 그 자리에서 실행하지
  않는다.** 확인 대기 상태의 assistant 메시지만 만들어 대화에 남기고 스트림을
  끝낸다. 프론트가 사용자 승인/거부를 받아 `POST /api/chat/confirm`으로 다시
  부르면 그때 실행한다("확인 단계 구현 방식" 절 — 스트리밍 하나로는 중간에
  멈춰 답을 기다릴 수 없어 응답을 끊고 다시 호출하는 방식을 쓴다).
- **도구 호출·결과 메시지는 전부 대화 파일에 저장한다**(`conversations.py`의
  `new_tool_call_message`/`new_tool_result_message`) — 화면에는 요약해 보여주되
  (프론트 몫) 모델에게는 원래 형태 그대로 다시 전달돼야 맥락이 안 끊긴다.
- 도구를 실행하는 동안 사용자에게 보일 텍스트가 없으므로, "🔧 도구 호출: ..."
  한 줄을 assistant 텍스트에 실어 보낸다 — 별도 상태 채널을 만드는 대신, 이
  줄 자체가 그대로 저장돼 나중에 대화를 다시 열어도 "무슨 도구를 왜 불렀는지"
  화면에 남는다.
- 한 사용자 요청 안에서 도구 호출이 5회(`MAX_TOOL_HOPS`)를 넘으면 루프를
  끊는다 — tool-calling을 잘 못하는 모델이 같은 도구를 반복 호출하는 경우를
  실측으로 확인했다.

모델은 네이티브 `/api/chat`으로 부른다(`ollama_client.app_chat` — 앱 채팅과 같은 경로·같은 값). 네이티브 스트림은
tool_call을 조각내지 않고 한 청크에 통째로 보내고, `arguments`가 JSON 문자열이 아니라 객체로 온다.
"""

import json
from collections.abc import Generator
from typing import Any

import agent_tools
import conversations
import ollama_client
import summarizer

MAX_TOOL_HOPS = 5


def _format_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _arguments(value: Any) -> dict[str, Any]:
    """네이티브는 객체로 주지만 문자열로 와도 풀어서 받는다. 풀 수 없으면 빈 인자다."""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_loop(
    model: str,
    current: list[dict[str, Any]],
    temperature: float,
    scope: str,
    hops: int,
) -> Generator[dict[str, Any], None, None]:
    """공용 루프 본체. `stream_chat_with_tools`(새 턴)와
    `resume_after_confirmation`(확인 후 재개) 둘 다 이걸 쓴다 — 도구를 실행하고
    다음 응답을 받는 로직은 시작점이 다를 뿐 완전히 같다."""
    tool_defs = [t.openai_spec() for t in agent_tools.list_ready_tools(scope)]

    while True:
        parsed_calls: list[dict[str, Any]] = []
        for chunk in ollama_client.app_chat(model, current, temperature=temperature, tools=tool_defs):
            message = chunk.get("message") or {}
            if message.get("content"):
                yield {"type": "text", "text": message["content"]}
            for tc in message.get("tool_calls") or []:
                function = tc.get("function") or {}
                parsed_calls.append({
                    # id가 없으면 만든다 — 도구 결과를 어느 호출에 붙일지 대화 파일이 이 값으로 잇는다
                    "id": tc.get("id") or f"call_{len(parsed_calls) + 1}",
                    "name": function.get("name") or "",
                    "arguments": _arguments(function.get("arguments")),
                })

        if not parsed_calls:
            return  # 자연어 응답으로 끝 — 정상 종료

        if hops >= MAX_TOOL_HOPS:
            yield {"type": "text", "text": f"\n\n*(도구 호출이 {MAX_TOOL_HOPS}회를 넘어 중단했습니다.)*"}
            return
        hops += 1

        tool_call_msg = conversations.new_tool_call_message(
            [{"id": pc["id"], "name": pc["name"], "arguments": pc["arguments"]} for pc in parsed_calls]
        )

        destructive = any(
            (t := agent_tools.get_tool(pc["name"])) is not None and t.destructive for pc in parsed_calls
        )
        if destructive:
            # 이 턴에 안전한 도구가 섞여 있어도 전부 보류한다 — 실행 순서가
            # 뒤섞이면(위험한 것만 미루고 안전한 것만 먼저 실행) 재개 시점에
            # "이미 실행된 것과 아직 안 된 것"을 다시 가려내야 해서 더 복잡해진다.
            tool_call_msg["confirmation"] = "pending"
            yield {"type": "confirm_required", "message": tool_call_msg}
            return

        yield {"type": "tool_messages", "messages": [tool_call_msg]}
        current.append(summarizer.to_wire([tool_call_msg])[0])

        result_messages = []
        for pc in parsed_calls:
            yield {"type": "text", "text": f"\n\n> 🔧 도구 호출: {pc['name']}({_format_args(pc['arguments'])})\n\n"}
            result = agent_tools.run_tool(pc["name"], pc["arguments"])
            result_msg = conversations.new_tool_result_message(pc["id"], result)
            result_messages.append(result_msg)
            current.append({"role": "tool", "tool_call_id": pc["id"], "content": result})
        yield {"type": "tool_messages", "messages": result_messages}
        # 루프 계속 — 도구 결과를 반영한 다음 응답(또는 다음 tool_call)을 받는다


def stream_chat_with_tools(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    *,
    scope: str = "chat",
) -> Generator[dict[str, Any], None, None]:
    """새 사용자 턴에서 도구 루프를 시작한다. 이벤트 3종을 yield한다:
    `{"type":"text","text":...}`(표시+저장할 텍스트), `{"type":"tool_messages",
    "messages":[...]}`(즉시 실행된 도구 호출/결과 — 저장만 하면 됨),
    `{"type":"confirm_required","message":...}`(위험 도구 — 저장하고 여기서
    멈춤). 호출자(`main.py`)가 이 셋을 받아 스트리밍 응답과 대화 저장을 만든다.
    """
    yield from _run_loop(model, list(messages), temperature, scope, hops=0)


def resume_after_confirmation(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    *,
    pending_calls: list[dict[str, Any]],
    approved: bool,
    scope: str = "chat",
) -> Generator[dict[str, Any], None, None]:
    """확인 대기 중이던 호출(들)을 승인/거부로 매듭짓고 루프를 이어간다.

    `messages`는 이미 확인 대기 assistant 메시지까지 포함한 wire-format
    히스토리여야 한다(`summarizer.build_context`가 만들어준 것을 그대로 받는다).
    승인/거부는 그 턴에 있던 호출 전부에 같은 결정을 적용한다 — 하나씩
    따로 확인받는 UI는 만들지 않는다(완료조건에 없고, 위험 도구가 한 턴에
    여러 개 섞이는 경우는 실제로 드물다).
    """
    current = list(messages)
    result_messages = []
    for pc in pending_calls:
        if approved:
            result = agent_tools.run_tool(pc["name"], pc["arguments"])
        else:
            result = json.dumps(
                {"status": "declined", "message": "사용자가 이 작업을 거부했습니다."}, ensure_ascii=False
            )
        result_msg = conversations.new_tool_result_message(pc["id"], result)
        result_messages.append(result_msg)
        current.append({"role": "tool", "tool_call_id": pc["id"], "content": result})
    yield {"type": "tool_messages", "messages": result_messages}

    yield from _run_loop(model, current, temperature, scope, hops=1)
