import { CONTEXT_STAGES } from '../metrics'
import { formatRawCell } from '../scoring'

// 지표 원본값 나열 + 베이스라인 병기 — baselineRun을 넘기면 같은
// 정의로 베이스라인 값도 뽑아 나란히 보여준다. baselineInScope=false인 배열
// (속도·리소스·tool-calling)은 애초에 베이스라인이 재지 않는 지표라 "기준선 없음"으로
// 고정한다. 값 칸은 전부 `formatRawCell`을 거친다 — 능력 부재·실행 실패를
// "측정 안 됨"과 구분해 보여주고, 비교 제외 지표의 기준선 칸은 `— 비교 제외`로 찍는다.
export function MetricsList({ run, defs, title, baselineRun, baselineInScope = false }) {
  if (!run?.metrics || Object.keys(run.metrics).length === 0) return null
  const showBaseline = baselineRun !== undefined
  return (
    <>
      {title && <h3 className="metrics-heading">{title}</h3>}
      <dl className="metrics-list">
        {defs.map((def) => (
          <div key={def.key} className="metrics-row">
            <dt>{def.label}</dt>
            <dd>
              {formatRawCell(run, def)}
              {showBaseline && (
                <span className="metrics-baseline">
                  {' '}
                  ·{' '}
                  {/* 기준선이 없거나 애초에 안 재는 배열이면 `기준선 없음` 한 덩어리 — 앞에 "기준선"을
                      또 붙이면 "기준선 기준선 없음"이 된다 */}
                  {baselineRun && baselineInScope && def.baselineInScope !== false
                    ? `기준선 ${formatRawCell(baselineRun, def, { baseline: true, inScope: true })}`
                    : '기준선 없음'}
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </>
  )
}

export function ContextStageTable({ metrics }) {
  const hasAny = CONTEXT_STAGES.some((t) => metrics?.[`context_${t}`])
  if (!hasAny) return null
  return (
    <table className="stage-table">
      <thead>
        <tr>
          <th>컨텍스트</th>
          <th>TTFT</th>
          <th>prefill tok/s</th>
          <th>생성 tok/s</th>
          <th>완결</th>
        </tr>
      </thead>
      <tbody>
        {CONTEXT_STAGES.map((t) => {
          const s = metrics[`context_${t}`]
          return (
            <tr key={t}>
              <td>{t.toLocaleString()}토큰</td>
              <td>{s ? `${s.ttft_sec.toFixed(3)}초` : '—'}</td>
              <td>{s?.prefill_tok_per_sec != null ? s.prefill_tok_per_sec.toFixed(0) : '—'}</td>
              <td>{s?.tok_per_sec != null ? s.tok_per_sec.toFixed(1) : '—'}</td>
              <td>{s ? (s.complete ? 'O' : 'X') : '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
