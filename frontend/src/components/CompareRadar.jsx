import { useEffect, useMemo, useState } from 'react'
import RadarChart, { RadarLegend } from './RadarChart'
import DotPlot, { DotPlotLegend } from './DotPlot'
import {
  BASELINE_ROW_ID,
  CATEGORIES,
  METRICS,
  PRESETS,
  categoryFraction,
  categoryScore,
  categoryWeightTotals,
  consistencyGateLines,
  gatedWeights,
  flipGroups,
  normalize,
  presetFlips,
  rankOrder,
  rankWithTies,
  rowsByRank,
  sameOrder,
  setCategoryWeight,
  TIE_LABEL,
  TIE_OMITTED_NOTE,
  toRow,
  usageMetricDirections,
  weightedScore,
} from '../scoring'

// 두 프리셋의 순위(정렬 순서)가 값 있는 실행들 사이에서 같은지 — 같으면
// "가중치와 무관하게 우세"(실제 능력 차이), 다르면 "가중치 선택에 좌우됨"이라는
// 해석 문장을 붙이고, 뒤집히는 모델 묶음은 순위 열에 `동률`로 적는다. 판단 로직은
// scoring.js에 있다 — 리포트의 종합 순위·가중치 민감도 장이 같은 함수를 쓰므로 갈라지면 안 된다.

// 정규화·가중치·종합 점수·레이더 차트. compareIds로
// 고른 실행들(raw 표는 BenchmarkPage가 이미 그린다)을 받아 여기서 정규화하고,
// 카테고리 슬라이더로 가중치를 조절하면 종합 점수와 레이더가 실시간으로 바뀐다.
export default function CompareRadar({ rows, baselineEntry, baselineStaleness, demotion, onWeightsChange }) {
  const [presetName, setPresetName] = useState('usage')
  const [weights, setWeights] = useState(() => PRESETS.usage.build())
  const [includeBaseline, setIncludeBaseline] = useState(true)

  // 슬라이더를 만지면 값이 프리셋에서 벗어난다 — 그 상태로 리포트 표지에
  // "적용 가중치: 실사용 중심"이라고 적으면 거짓말이 된다. 지금 가중치가 그
  // 프리셋 그대로일 때만 이름을 인정하고, 벗어났으면 이름 없음(= 커스텀)이다.
  const activePreset = useMemo(() => {
    const built = PRESETS[presetName]?.build()
    if (!built) return null
    return METRICS.every((m) => (built[m.key] ?? 0) === (weights[m.key] ?? 0)) ? presetName : null
  }, [presetName, weights])

  // 리포트 내보내기가 "지금 보고 있던 가중치"를 그대로 담아야 하는데,
  // 그 상태(weights)는 여기서만 산다. 슬라이더 UI는 그대로 이 컴포넌트가
  // 소유하고, 부모(ComparePage)에는 값만 흘려보낸다 — 외부 시스템(리포트 payload
  // 조립)에 동기화하는 것이라 effect가 맞다. 프리셋 이름도 같이 넘긴다 —
  // 리포트 표지가 "적용 가중치: 실사용 중심"처럼 이름으로 적어야 하는데,
  // 가중치 숫자만 보고는 그게 프리셋인지 슬라이더로 만진 값인지 알 수 없다.
  useEffect(() => {
    onWeightsChange?.({ presetName: activePreset, weights })
  }, [activePreset, weights, onWeightsChange])

  // 정규화 비교군 = 지금 비교 중인 로컬 실행들 + (토글이 켜져 있으면) 베이스라인.
  // 기본은 켬 — 베이스라인이
  // 대부분의 품질 지표에서 최고값을 가져가 각 지표가 "기준선 대비 몇 %"로
  // 읽힌다. 로컬 점수가 전반적으로 낮아 보이지만, 한 지표 안에서 모든 값이
  // 같은 수(베이스라인)로 나뉘므로 로컬끼리의 상대 비교는 죽지 않는다.
  const normRows = useMemo(() => {
    const base = rows.map((r) => toRow(r))
    if (includeBaseline && baselineEntry) {
      base.push(toRow(baselineEntry, BASELINE_ROW_ID))
    }
    return base
  }, [rows, includeBaseline, baselineEntry])

  const normalized = useMemo(() => normalize(normRows), [normRows])

  const categoryTotals = categoryWeightTotals(weights)

  function applyPreset(name) {
    setPresetName(name)
    setWeights(PRESETS[name].build())
  }

  function onCategorySlider(categoryId, value) {
    setWeights((w) => setCategoryWeight(w, categoryId, Number(value)))
  }

  // 베이스라인은 항상 참고선으로 그린다(정규화 비교군 포함 여부와 무관하게) —
  // 포함 토글은 "다른 실행들의 정규화 기준에 베이스라인도 넣을지"만 결정한다.
  // 레이더·점 그래프 둘 다 같은 baseNorm을 써서 두 차트의 기준선이 항상 같은 값을 보이게 한다.
  const baseNorm = useMemo(() => {
    if (!baselineEntry) return null
    return includeBaseline
      ? normalized
      : normalize([...normRows, toRow(baselineEntry, BASELINE_ROW_ID)])
  }, [baselineEntry, includeBaseline, normalized, normRows])

  const baselineSeries = baselineEntry &&
    baseNorm && {
      label: baselineEntry.model,
      values: CATEGORIES.map((c) => categoryScore(baseNorm, BASELINE_ROW_ID, c.id)),
    }

  const chartRows = rows.map((r) => ({
    id: r.id,
    label: r.model,
    values: CATEGORIES.map((c) => categoryScore(normalized, r.id, c.id)),
  }))

  // 점 그래프 — 레이더 대신 기본으로 쓴다. 지표를 세로로
  // 쌓고 그 줄에 모델 수만큼 점을 찍는다. METRICS는 scoring.js의 단일 소스라
  // 카테고리 슬라이더·프리셋과 별개로 25개 지표 각각의 정규화 값을 그대로 쓴다.
  const dotMetricRows = METRICS.map((m) => ({
    label: m.label,
    values: rows.map((r) => normalized[m.key]?.[r.id] ?? null),
  }))
  const dotBaselineValues = baseNorm ? METRICS.map((m) => baseNorm[m.key]?.[BASELINE_ROW_ID] ?? null) : null

  // 두 프리셋의 종합 점수는 지금 슬라이더로 뭘 만졌든 상관없이 항상 나란히
  // 보여준다 — "이 프리셋이면 이렇게 나온다"는 항상 참고 가능해야 한다.
  // 일관성이 순위에서 빠졌으면 슬라이더 값과 무관하게 세 가중치 모두 일관성 0으로 계산한다(세트 단위).
  const neutralWeights = useMemo(() => gatedWeights(PRESETS.neutral.build(), demotion), [demotion])
  const usageWeights = useMemo(() => gatedWeights(PRESETS.usage.build(), demotion), [demotion])
  const scoringWeights = gatedWeights(weights, demotion)
  const currentScores = Object.fromEntries(rows.map((r) => [r.id, weightedScore(normalized, scoringWeights, r.id)]))
  const neutralScores = Object.fromEntries(rows.map((r) => [r.id, weightedScore(normalized, neutralWeights, r.id)]))
  const usageScores = Object.fromEntries(rows.map((r) => [r.id, weightedScore(normalized, usageWeights, r.id)]))
  const flips = presetFlips(rows, neutralScores, usageScores)
  const rankingAgrees = flips.neutralOrder.length >= 2 && sameOrder(flips.neutralOrder, flips.usageOrder)
  const rankingDiffers = flips.neutralOrder.length >= 2 && !sameOrder(flips.neutralOrder, flips.usageOrder)
  const order = rankOrder(rows, (id) => currentScores[id])
  const { ranks, tied, omitted } = rankWithTies(order, flipGroups(flips.pairs))
  const tableRows = rowsByRank(rows, order)
  const gate = consistencyGateLines(demotion)

  const { up, down } = usageMetricDirections()

  return (
    <section className="compare-radar">
      <div className="compare-radar-head">
        <h2>정규화·가중치 비교</h2>
        <div className="preset-buttons">
          {Object.entries(PRESETS).map(([name, p]) => (
            <button
              key={name}
              type="button"
              className={presetName === name ? 'preset-active' : 'ghost'}
              onClick={() => applyPreset(name)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <p className="preset-description">{PRESETS[presetName].description}</p>

      {presetName === 'usage' && (up.length > 0 || down.length > 0) && (
        <div className="preset-directions">
          {up.length > 0 && (
            <p>
              <span className="preset-dir-up">↑ 올림</span> {up.join(', ')}
            </p>
          )}
          {down.length > 0 && (
            <p>
              <span className="preset-dir-down">↓ 내림</span> {down.join(', ')}
            </p>
          )}
        </div>
      )}

      {baselineEntry && (
        <div className="baseline-controls">
          <label className="field field-inline">
            <input type="checkbox" checked={includeBaseline} onChange={(e) => setIncludeBaseline(e.target.checked)} />
            <span>베이스라인을 정규화 비교군에 포함</span>
          </label>
          {baselineStaleness?.stale && (
            <div className="baseline-stale-banner">
              베이스라인이 현재 세트와 다릅니다 — 다시 측정이 필요합니다 (측정일:{' '}
              {baselineStaleness.measured_at ? new Date(baselineStaleness.measured_at).toLocaleDateString() : '알 수 없음'})
            </div>
          )}
        </div>
      )}

      <div className="category-sliders">
        {CATEGORIES.map((c) => (
          <label key={c.id} className="category-slider">
            <span>
              {c.label}: {categoryTotals[c.id].toFixed(1)}
            </span>
            <input
              type="range"
              min="0"
              max="10"
              step="0.1"
              value={categoryTotals[c.id]}
              onChange={(e) => onCategorySlider(c.id, e.target.value)}
            />
          </label>
        ))}
      </div>

      <div className="dotplot-wrap">
        <DotPlot metricRows={dotMetricRows} rows={rows.map((r) => ({ id: r.id, label: r.model }))} baselineValues={dotBaselineValues} />
        <DotPlotLegend rows={rows.map((r) => ({ id: r.id, label: r.model }))} hasBaseline={Boolean(baselineEntry)} />
      </div>

      <details className="radar-secondary">
        <summary>레이더 (보조) — 판단 근거로는 점 그래프를 우선한다</summary>
        <div className="radar-wrap">
          <RadarChart axisLabels={CATEGORIES.map((c) => c.label)} rows={chartRows} baselineRow={baselineSeries} />
          <RadarLegend rows={chartRows} baselineRow={baselineSeries} />
        </div>
      </details>

      {(gate.lines.length > 0 || gate.watcher) && (
        <div className="consistency-gate">
          {gate.lines.map((line) => (
            <p key={line} className={demotion?.status === 'demoted' ? 'consistency-gate-demoted' : undefined}>
              {line}
              {demotion?.status === 'demoted' && ' — 일관성 점수는 그대로 보이지만 세 종합 점수 모두 일관성 가중치 0으로 계산한다.'}
            </p>
          ))}
          {gate.watcher && <p className="consistency-gate-watcher">{gate.watcher}</p>}
        </div>
      )}

      <table className="score-table">
        <thead>
          <tr>
            <th>순위 (현재 가중치)</th>
            <th>모델</th>
            <th>종합 점수 (현재 가중치)</th>
            <th>{PRESETS.neutral.label}</th>
            <th>{PRESETS.usage.label}</th>
            {CATEGORIES.map((c) => (
              <th key={c.id}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableRows.map((r) => {
            const score = currentScores[r.id]
            return (
              <tr key={r.id}>
                <td>
                  {ranks[r.id] ?? '—'}
                  {tied.has(r.id) && <span className="score-tie"> {TIE_LABEL}</span>}
                </td>
                <td>{r.model}</td>
                <td>{score == null ? '측정 안 됨' : score.toFixed(3)}</td>
                <td>{neutralScores[r.id] == null ? '측정 안 됨' : neutralScores[r.id].toFixed(3)}</td>
                <td>{usageScores[r.id] == null ? '측정 안 됨' : usageScores[r.id].toFixed(3)}</td>
                {CATEGORIES.map((c) => {
                  const frac = categoryFraction(normalized, r.id, c.id)
                  return (
                    <td key={c.id}>
                      {frac.have}/{frac.total}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>

      {rankingAgrees && (
        <p className="score-rank-note score-rank-agree">
          두 프리셋의 순위가 같습니다 — 가중치 선택과 무관하게 우세한 차이로 보입니다.
        </p>
      )}
      {rankingDiffers && (
        <p className="score-rank-note score-rank-differ">
          두 프리셋의 순위가 다릅니다 — 이 순위는 가중치 선택에 좌우됩니다.
          {omitted && ` ${TIE_OMITTED_NOTE}`}
        </p>
      )}

      <p className="score-note">
        종합 점수는 확정된 순위가 아니라 참고용 정렬 도구다 — 최종 선택은 실사용 별점과 느낌을 기준으로 한다.
      </p>
    </section>
  )
}
