import { useEffect, useState } from 'react'
import { fetchFeatures } from './api'

// 공개본에 없을 수 있는 기능 — 서버가 그 파일을 가졌는지로 켜진다(`GET /api/features`).
// 받기 전이나 받지 못했을 때는 전부 끈다: 없는 기능의 버튼을 보여 주면 누른 뒤에야 404가 난다.
export const NO_FEATURES = { prompt_experiment: false, rerun: false, maintenance_cli: false }

let cached = null

export function useFeatures() {
  const [features, setFeatures] = useState(cached ?? NO_FEATURES)
  useEffect(() => {
    if (cached) return undefined
    let alive = true
    fetchFeatures()
      .then((f) => {
        cached = { ...NO_FEATURES, ...f }
        if (alive) setFeatures(cached)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  return features
}

/** 실행 전 경고 — 모델에게 주지 못하는 도구가 있으면 한 줄. 측정은 오류 없이 줄어든 도구로 재므로 여기서 보여야 한다.
 * 도구 화면은 키가 없어도 전부 나열하므로(배지만 붙는다) 수를 세려면 상태를 봐야 한다.
 * 도구 응답이 고정값이면(`fixed`) 고정값을 가진 도구는 키 없이도 기록된 응답으로 모델에게 간다 — 빠진 도구로 세지 않는다. */
export function toolAvailabilityWarning(tools, { fixed = false } = {}) {
  const measured = (tools ?? []).filter((t) => t.scope === 'test' || t.scope === 'both')
  const unready = measured.filter((t) => t.status !== 'ready' && !(fixed && t.fixture_backed))
  if (unready.length === 0) return null
  const reason = (t) => (t.status === 'missing_key' ? `키 없음(${t.requires_key})` : '비활성')
  return (
    `도구 ${measured.length}개 중 ${measured.length - unready.length}개만 모델에게 줍니다 — ` +
    `${unready.map((t) => `${t.name} ${reason(t)}`).join(' · ')}. 도구 호출 지표가 다른 구성으로 잰 값이 됩니다.`
  )
}
