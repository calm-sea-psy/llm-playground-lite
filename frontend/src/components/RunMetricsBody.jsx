import { METRICS, QUALITY_METRICS, TOOL_CALLING_METRICS } from '../metrics'
import { MetricsList, ContextStageTable } from './MetricsDisplay'
import QualityDetail from './QualityDetail'

// 실행 중 진행 상황과 저장된 실행 상세가 공유하는 본문(지표 나열 + 문항별
// 드릴다운) — BenchmarkPage.jsx 하나에 있던 것을 분리했다.
// baselineRun을 주면(저장된 상세에서만 — 진행 중에는 비교 대상이 아직 없다)
// E의 병기 표시가 각 MetricsList에 켜진다. `run`은 `{metrics, items}` — 항목의
// 저장 상태(능력 부재·실행 실패)를 값 칸에 보여주려면 items가 함께 필요하다.
export default function RunMetricsBody({ run, baselineRun }) {
  const metrics = run?.metrics ?? {}
  const hasItem = (id) => (run?.items ?? []).some((it) => it.id === id)
  return (
    <>
      <MetricsList run={run} defs={METRICS} baselineRun={baselineRun} baselineInScope={false} />
      <ContextStageTable metrics={metrics} />
      {(metrics.instruction_following || hasItem('instruction_following')) && (
        <MetricsList
          run={run}
          defs={QUALITY_METRICS}
          title="품질·보안 지표"
          baselineRun={baselineRun}
          baselineInScope
        />
      )}
      {/* 능력 부재·실행 실패면 metrics.tool_calling이 없다 — 항목이 있으면 그 상태를 보여준다 */}
      {(metrics.tool_calling || hasItem('tool_calling')) && (
        <MetricsList
          run={run}
          defs={TOOL_CALLING_METRICS}
          title="Tool-calling 정확도"
          baselineRun={baselineRun}
          baselineInScope={false}
        />
      )}
      <QualityDetail metrics={metrics} />
    </>
  )
}
