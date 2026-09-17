import { STATUS_LABEL, QUALITY_METRICS } from '../metrics'
import { MetricsList } from './MetricsDisplay'
import QualityDetail from './QualityDetail'
import RunItemList from './RunItemList'

// 기준선 전용 우측 패널 — 좌측 사이드바의 "기준선" 항목을 누르면 뜬다.
// 후보 모델과 섞이지 않도록 구분된 자리다(후보처럼 보이지
// 않게). 속도·리소스·Tool-calling은 베이스라인 적용 범위 밖이라 여기서는 아예
// 나열하지 않는다 — 병기(E)는 반대로 /benchmark 실행 상세 쪽에서 이 값들을
// 끌어다 보여주는 것이라(RunMetricsBody), 여기서 또 보여줄 필요가 없다.
export default function BaselinePanel({ baseline, busy, activeRun, onMeasure }) {
  const entry = baseline?.run
  const staleness = baseline?.staleness

  return (
    <section className="baseline-panel">
      <h2>기준선 — {entry?.model || 'gpt-5.6-luna'} (클라우드)</h2>

      {entry ? (
        <p className="baseline-meta">
          마지막 측정: {new Date(entry.measured_at).toLocaleString()}
          {staleness?.stale && <span className="baseline-stale"> · 낡음 — 다시 측정이 필요합니다</span>}
        </p>
      ) : (
        <p className="model-empty">아직 측정한 적이 없습니다</p>
      )}

      {/* 무엇이 달라졌는지까지 짚는다 — 세트를 안 고쳤는데 낡음이 뜨면 이유를 알 수 없다 */}
      {staleness?.reasons?.length > 0 && (
        <ul className="baseline-stale-reasons">
          {staleness.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      <button type="button" className="ghost" onClick={onMeasure} disabled={busy}>
        {entry ? '다시 측정' : '측정'}
      </button>

      {activeRun && (
        <section className="run-progress">
          <h2>{STATUS_LABEL[activeRun.status] ?? activeRun.status}</h2>
          <p>
            {activeRun.completed} / {activeRun.total}
            {activeRun.current_item && ` — 진행 중: ${activeRun.current_item}`}
          </p>
          <RunItemList items={activeRun.items} />
        </section>
      )}

      {entry && (
        <>
          <h3 className="metrics-heading">적용 지표 ({entry.items.length}개)</h3>
          <ul className="run-summary-items">
            {entry.items.map((it) => (
              <li key={it.id}>{it.label}</li>
            ))}
          </ul>
          <MetricsList run={entry} defs={QUALITY_METRICS} title="품질·보안 지표" />
          <QualityDetail metrics={entry.metrics} />
        </>
      )}
    </section>
  )
}
