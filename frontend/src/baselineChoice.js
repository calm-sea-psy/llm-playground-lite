import { documentLength } from './runDiff'

/** 기준선을 고르는 조건 — **후보와 같은 문서 길이**. 고른 후보가 없으면 다음 후보가 읽을 판(''), 후보의 판이 섞이면 null(고르지 않는다). */
export function baselineLengthKey(details) {
  const lengths = [...new Set(details.map((d) => documentLength(d.config)))]
  if (lengths.length > 1) return null
  return lengths[0] ?? ''
}

/** 판이 섞였을 때의 까닭 — 아무거나 채우면 후보와 다른 조건의 기준선이 열을 차지한다. */
export function mixedLengthsReason(details) {
  const lengths = [...new Set(details.map((d) => documentLength(d.config)))]
  return `후보의 문서 길이가 섞여 있어(${lengths.join(' · ')}) 기준선을 고르지 않았다`
}
