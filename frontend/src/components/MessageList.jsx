import { useEffect, useRef } from 'react'
import { rateMessage } from '../api'
import { formatElapsed } from '../format'
import RatingStars from './RatingStars'

// 도구 호출 tool_calls를 "도구 호출: name(args), name2(args2)" 한 줄로 요약한다
// (화면에는 이 메시지들을 그대로 노출하지 않고 요약해 보여준다).
function formatToolCalls(toolCalls) {
  return toolCalls
    .map((tc) => {
      const args = Object.entries(tc.arguments || {})
        .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
        .join(', ')
      return `${tc.name}(${args})`
    })
    .join(', ')
}

const CONFIRMATION_LABEL = {
  approved: '(승인됨)',
  declined: '(거부됨)',
}

// 메시지 목록. 어시스턴트 응답 옆에 소요 시간을 분:초로 보여준다 —
// 스트리밍 중에는 실시간으로 올라가고, 끝나면 최종값으로 고정된다.
// conversationId + onMessageRated가 있으면 완료된 어시스턴트 응답마다 별점 UI를 붙인다.
// 아직 스트리밍 중인 응답에는 붙이지 않는다 — 내용이 확정되지 않았다.
//
// 도구 호출 메시지(role=assistant + tool_calls)는 원본 그대로 노출하지 않고
// "도구 호출: name(args)" 요약으로 보여준다. confirmation === "pending"인
// 마지막 메시지에는 승인/거부 버튼을 붙인다. 도구
// 결과 메시지(role=tool)는 화면에 아예 렌더링하지 않는다 — 모델에게 다시
// 전달하기 위해 대화 파일에는 저장되지만, 사람이 볼 필요는 없는 원본 JSON이다.
export default function MessageList({
  messages,
  busy,
  liveElapsed,
  conversationId,
  onMessageRated,
  onConfirm,
}) {
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages])

  return (
    <div className="messages" ref={scrollRef}>
      {messages.length === 0 && (
        <div className="empty">모델을 고르고 메시지를 입력해 보세요.</div>
      )}
      {messages.map((m, i) => {
        if (m.role === 'tool') return null

        const isLast = i === messages.length - 1

        // confirmation이 없는 tool_calls 메시지(위험하지 않아 그 자리에서 바로
        // 실행된 것)는 여기서 숨긴다 — 그 도구 호출은 이미 바로 다음 어시스턴트
        // 메시지의 "🔧 도구 호출: ..." 서두에서 자연스럽게 narration된다
        // (tools.py `_run_loop`). 여기서 또 요약을 보여주면 같은 호출이 두 번
        // 나온다. confirmation이 있는 경우(pending/approved/declined)는 그
        // narration이 없는 유일한 자리라 계속 보여준다.
        if (m.role === 'assistant' && m.tool_calls?.length && !m.confirmation) return null

        if (m.role === 'assistant' && m.tool_calls?.length) {
          const pending = m.confirmation === 'pending'
          return (
            <div key={m.id || i} className="msg tool-call">
              <div className="role">
                <span>assistant</span>
              </div>
              <div className="content">
                <p className="tool-call-summary">
                  🔧 도구 호출: {formatToolCalls(m.tool_calls)}
                  {CONFIRMATION_LABEL[m.confirmation] && (
                    <span className="tool-call-status">{CONFIRMATION_LABEL[m.confirmation]}</span>
                  )}
                </p>
                {pending && isLast && (
                  <div className="tool-confirm">
                    <p>정말 실행할까요?</p>
                    <div className="tool-confirm-actions">
                      <button
                        className="tool-confirm-approve"
                        disabled={busy}
                        onClick={() => onConfirm?.(true)}
                      >
                        승인
                      </button>
                      <button
                        className="tool-confirm-decline"
                        disabled={busy}
                        onClick={() => onConfirm?.(false)}
                      >
                        거부
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )
        }

        const live = m.role === 'assistant' && isLast && busy
        const time = live ? liveElapsed : m.elapsedMs
        return (
          <div key={m.id || i} className={`msg ${m.role}`}>
            <div className="role">
              <span>{m.role}</span>
              {m.role === 'assistant' && time != null && (
                <span className={`elapsed${live ? ' live' : ''}`}>{formatElapsed(time)}</span>
              )}
            </div>
            <div className="content">{m.content || (busy && isLast ? '…' : '')}</div>
            {m.role === 'assistant' && !live && m.id && (
              <RatingStars
                rating={m.rating}
                ratingNote={m.rating_note}
                disabled={!conversationId || !m.id}
                onSubmit={(nextRating, nextNote) =>
                  rateMessage(conversationId, m.id, { rating: nextRating, note: nextNote }).then((conv) => {
                    const msg = conv.messages.find((mm) => mm.id === m.id)
                    if (msg) onMessageRated?.(msg)
                  })
                }
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
