import { useState } from 'react'
import { STATUS_LABEL } from '../metrics'
import { rerunReasonLabel, rerunReasonText } from '../rerun'
import { FAILURE_CAUSE_LABEL, OUTCOME, OUTCOME_LABEL } from '../scoring'

// 실행 항목 목록 — 진행 중 화면과 저장된 상세가 같이 쓴다. 상태 넷을 끝까지 구분해
// 보여준다: 능력 부재(못 함)와 실행 실패(아직 모름)는 둘 다 값이
// 없어 보이지만 후속 조치가 다르다 — 실패는 **재측정하라는 신호**라 가장 눈에
// 띄어야 하고, 품질·tool-calling 항목이면 그 자리에서 지표만 다시 잴 수 있다.
const RERUNNABLE = new Set([
  'instruction_following',
  'long_context',
  'consistency',
  'hallucination',
  'key_coverage',
  'closed_qa',
  'structured_output',
  'injection_direct',
  'injection_indirect',
  'prompt_leak',
  'over_refusal',
  'tool_calling',
  'injection_probe',
])

const AUX_ROLE_LABEL = { embedding: '임베딩' }

export default function RunItemList({ items, provenance, parentRunId, onRerun, rerunDisabled }) {
  // 이유를 받는 중인 항목 — 버튼을 누르면 그 자리에 입력칸이 열리고, 이유를 적어야 다시 잰다
  const [asking, setAsking] = useState(null)
  const [reason, setReason] = useState('')
  const reasonText = rerunReasonText(reason)

  function submit(event, itemId) {
    event.preventDefault()
    if (!reasonText || rerunDisabled) return
    onRerun(itemId, reasonText)
    setAsking(null)
    setReason('')
  }

  return (
    <ul className="run-items">
      {items.map((item) => {
        const outcome = item.outcome
        const labeled = outcome && outcome !== OUTCOME.MEASURED
        const source = provenance?.[item.id]
        const fromRerun = source && parentRunId && source.run_id !== parentRunId
        return (
          <li key={item.id} className={outcome === OUTCOME.FAILED ? 'run-item-failed' : undefined}>
            {item.label} — {labeled ? OUTCOME_LABEL[outcome] : (STATUS_LABEL[item.status] ?? item.status)}
            {item.failure?.cause && (
              <span className="item-error"> (원인: {FAILURE_CAUSE_LABEL[item.failure.cause] ?? item.failure.cause})</span>
            )}
            {/* 채점 대상이 아닌 보조 모델을 실제로 부른 항목 — 같은 지표라도 값의 뜻이 달라진다 */}
            {(item.aux_models ?? []).map((aux) => (
              <span
                key={`${aux.role}:${aux.model}`}
                className="run-item-aux"
                title={`${AUX_ROLE_LABEL[aux.role] ?? aux.role} 모델 ${aux.model}${aux.digest ? ` · digest ${aux.digest.slice(0, 12)}` : ''}`}
              >
                {aux.model} 사용
              </span>
            ))}
            {item.error && <span className="item-error"> {item.error}</span>}
            {fromRerun && (
              <span className="run-item-source">
                {' '}
                · 재실행 {new Date(source.started_at).toLocaleString()} ({rerunReasonLabel(source.reason)})
              </span>
            )}
            {/* 버튼은 저장 상태로 판단한다 — 능력 부재는 다시 재도 능력 부재다. outcome이 없는 옛 결과만 status로 본다 */}
            {onRerun && (outcome ? outcome === OUTCOME.FAILED : item.status === 'failed') && RERUNNABLE.has(item.id) && (
              asking === item.id ? (
                <form className="run-item-reason" onSubmit={(event) => submit(event, item.id)}>
                  <input
                    type="text"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="다시 재는 이유 — 리포트의 혼합 실행 줄에 실린다"
                    aria-label={`${item.label} 재실행 이유`}
                    autoFocus
                  />
                  <button type="submit" className="ghost" disabled={!reasonText || rerunDisabled}>
                    다시 재기
                  </button>
                  <button type="button" className="ghost" onClick={() => setAsking(null)}>
                    취소
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  className="ghost run-item-rerun"
                  onClick={() => {
                    setAsking(item.id)
                    setReason('')
                  }}
                  disabled={rerunDisabled}
                >
                  이 지표 다시 재기
                </button>
              )
            )}
          </li>
        )
      })}
    </ul>
  )
}
