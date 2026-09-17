// 비교 노트 인용 검증 표시 — 판정은 백엔드가 불릿마다 매기고, 여기서는 머리 줄과 딱지 문구만 만든다.

export const MARKERS = ['확인됨', '표와 불일치', '방향 불일치', '대조 불가']
const MARKER_CLASS = { 확인됨: 'verified', '표와 불일치': 'mismatch', '방향 불일치': 'direction', '대조 불가': 'unverifiable' }

/** 머리 줄 — 표식별 **불릿 수**. 한 불릿에 표식이 여럿일 수 있어 합이 불릿 수를 넘으므로 전체 불릿 수를 함께 적는다.
 * 전체 불릿 수가 없는 기록은 표식이 셋이던 옛 규칙의 것이라 null — 방향 불일치를 0으로 적지 않는다. */
export function verificationSummary(verification) {
  if (!verification?.counts || verification.total == null) return null
  const counts = MARKERS.map((m) => `${m} ${verification.counts[m] ?? 0}`)
  return `인용 검증 — 불릿 ${verification.total}개 · ${counts.join(' · ')}`
}

/** 불릿 하나의 딱지들 — 표식마다 하나(우선순위 없는 목록이라 틀린 값이 둘이면 딱지도 둘). 원인은 딱지에, 이유는 툴팁에. */
export function markerBadges(bullet) {
  return (bullet?.markers ?? []).map((m) => ({
    label: m.cause ? `${m.marker} · ${m.cause}` : m.marker,
    className: MARKER_CLASS[m.marker] ?? '',
    // 대조 불가의 이유는 판정기가 싣는다(수치 주장·비교 서술까지). 싣기 전 기록이면 인용에서 모은다
    title: m.detail || (m.marker === '대조 불가' ? unverifiableReason(bullet) : undefined),
  }))
}

function unverifiableReason(bullet) {
  const citations = bullet.citations ?? []
  if (citations.length === 0) return '인용한 수치 없음'
  return [...new Set(citations.map((c) => c.reason).filter(Boolean))].join('\n') || undefined
}
