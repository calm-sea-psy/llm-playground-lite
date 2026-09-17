// 손으로 그리는 SVG 점 그래프. 지표를 가로질러 비교하는 기본형.
// 레이더는 축 순서에 따라 모양이 달라지고
// 모델이 셋 이상 겹치면 못 읽는다 — 지표가 25개면 더 심하다. 대신 지표를
// 세로로 쌓고 그 줄 위에 모델을 점으로 찍으면 위에서 아래로 훑기만 해도
// 어디서 갈리고 어디서 붙는지 보인다.
const ROW_HEIGHT = 22
const LABEL_WIDTH = 190
const PLOT_WIDTH = 340
const PADDING = 16
const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2']

function x(v) {
  return LABEL_WIDTH + Math.max(0, Math.min(1, v)) * PLOT_WIDTH
}

// metricRows: [{ label, values: [0..1|null, ...] }] — values는 rows와 같은 순서.
// rows: [{ id, label }] — 색 배정 순서(RadarChart와 같은 팔레트를 써서 같은
// 화면 안에서 모델-색 대응이 흔들리지 않게 한다).
// baselineValues: [0..1|null, ...] | null — metricRows와 같은 길이, 세로 틱으로 표시
// (시리즈가 아니라 참조선이라 점과 모양을 다르게 한다 — 색 없이도 구분되게).
export default function DotPlot({ metricRows, rows, baselineValues }) {
  const width = LABEL_WIDTH + PLOT_WIDTH + PADDING
  const height = metricRows.length * ROW_HEIGHT + PADDING

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="dot-plot"
      role="img"
      aria-label="지표별 정규화 점수 점 그래프"
    >
      {metricRows.map((row, mi) => {
        const y = PADDING / 2 + mi * ROW_HEIGHT + ROW_HEIGHT / 2
        const baseVal = baselineValues?.[mi]
        return (
          <g key={row.label}>
            <text x={LABEL_WIDTH - 8} y={y} textAnchor="end" dominantBaseline="middle" className="dotplot-label">
              {row.label}
            </text>
            <line x1={LABEL_WIDTH} y1={y} x2={LABEL_WIDTH + PLOT_WIDTH} y2={y} className="dotplot-axis" />
            {baseVal != null && (
              <line
                x1={x(baseVal)}
                y1={y - 6}
                x2={x(baseVal)}
                y2={y + 6}
                className="dotplot-baseline-tick"
              />
            )}
            {row.values.map((v, ri) =>
              v == null ? null : (
                <circle key={rows[ri].id} cx={x(v)} cy={y} r={4} fill={COLORS[ri % COLORS.length]} />
              ),
            )}
          </g>
        )
      })}
    </svg>
  )
}

export function DotPlotLegend({ rows, hasBaseline }) {
  return (
    <ul className="radar-legend">
      {rows.map((row, ri) => (
        <li key={row.id}>
          <span className="radar-swatch" style={{ background: COLORS[ri % COLORS.length] }} />
          {row.label}
        </li>
      ))}
      {hasBaseline && (
        <li>
          <span className="dotplot-swatch-baseline" />
          기준선 (참조 틱)
        </li>
      )}
    </ul>
  )
}
