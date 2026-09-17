// 손으로 그리는 SVG 레이더 차트. 외부 차트 라이브러리 없이 비교 화면
// 전용으로 쓴다(축 6개 고정 — CATEGORIES). 정규화 값(0~1)만 받는다.
const SIZE = 280
const CENTER = SIZE / 2
const RADIUS = SIZE / 2 - 40
const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2']

function axisPoint(index, total, value) {
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2
  const r = RADIUS * Math.max(0, Math.min(1, value))
  return [CENTER + r * Math.cos(angle), CENTER + r * Math.sin(angle)]
}

function labelPoint(index, total) {
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2
  const r = RADIUS + 22
  return [CENTER + r * Math.cos(angle), CENTER + r * Math.sin(angle)]
}

// rows: [{ id, label, values: [0..1 또는 null, ...axes 순서대로] }]
// axisLabels: 축 이름 배열(길이 = values 배열 길이)
export default function RadarChart({ axisLabels, rows, baselineRow }) {
  const total = axisLabels.length
  const rings = [0.25, 0.5, 0.75, 1]

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="radar-chart" role="img" aria-label="카테고리별 정규화 점수 레이더 차트">
      {rings.map((ring) => {
        const pts = axisLabels.map((_, i) => axisPoint(i, total, ring).join(',')).join(' ')
        return <polygon key={ring} points={pts} className="radar-ring" />
      })}
      {axisLabels.map((_, i) => {
        const [x, y] = axisPoint(i, total, 1)
        return <line key={i} x1={CENTER} y1={CENTER} x2={x} y2={y} className="radar-axis" />
      })}
      {axisLabels.map((label, i) => {
        const [x, y] = labelPoint(i, total)
        return (
          <text key={label} x={x} y={y} className="radar-axis-label" textAnchor="middle" dominantBaseline="middle">
            {label}
          </text>
        )
      })}
      {baselineRow && (
        <polygon
          points={baselineRow.values.map((v, i) => axisPoint(i, total, v ?? 0).join(',')).join(' ')}
          className="radar-baseline"
        />
      )}
      {rows.map((row, ri) => {
        const color = COLORS[ri % COLORS.length]
        const pts = row.values.map((v, i) => axisPoint(i, total, v ?? 0).join(',')).join(' ')
        return (
          <g key={row.id}>
            <polygon points={pts} style={{ fill: color, fillOpacity: 0.12, stroke: color }} className="radar-series" />
            {row.values.map((v, i) =>
              v == null ? null : (
                <circle key={i} cx={axisPoint(i, total, v)[0]} cy={axisPoint(i, total, v)[1]} r={3} fill={color} />
              ),
            )}
          </g>
        )
      })}
    </svg>
  )
}

export function RadarLegend({ rows, baselineRow }) {
  return (
    <ul className="radar-legend">
      {rows.map((row, ri) => (
        <li key={row.id}>
          <span className="radar-swatch" style={{ background: COLORS[ri % COLORS.length] }} />
          {row.label}
        </li>
      ))}
      {baselineRow && (
        <li>
          <span className="radar-swatch radar-swatch-baseline" />
          {baselineRow.label} (참고선)
        </li>
      )}
    </ul>
  )
}
