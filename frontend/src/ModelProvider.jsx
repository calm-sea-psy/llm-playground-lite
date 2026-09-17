import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchModelDetails } from './api'
import { ModelContext, isEmbeddingOnly } from './model-context'

const CACHE_KEY = 'llmplg.modelDetails'

function readCache() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CACHE_KEY))
    return Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

function writeCache(list) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(list))
  } catch {
    // 용량 초과 등 — 캐시는 최적화일 뿐이라 실패해도 무시한다.
  }
}

export function ModelProvider({ children }) {
  const cached = readCache()
  const [models, setModels] = useState(() => cached || [])
  // 사용자가 고르기 전까지는 빈 문자열. 실제 선택은 selectedId || 첫 모델로 파생한다.
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(!cached)

  const load = useCallback(async (signal) => {
    try {
      const list = await fetchModelDetails()
      if (signal?.aborted) return
      setModels(list)
      writeCache(list)
      setError('')
    } catch (e) {
      if (!signal?.aborted) setError(String(e.message || e))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  // 첫 로드: 캐시로 즉시 그리고, 서버에서 새로 받아 갱신한다.
  // 서버 요청은 effect가 맞다("외부 시스템과 동기화"). setState는 전부 await 뒤에 있다.
  useEffect(() => {
    const ac = new AbortController()
    // oxlint-disable-next-line react/set-state-in-effect -- 마운트 시 서버 fetch, setState는 모두 await 이후
    load(ac.signal)
    return () => ac.abort()
  }, [load])

  // 모델 설치/삭제가 성공했을 때 캐시를 무효화하고 다시 받는다.
  const refresh = useCallback(() => {
    setLoading(true)
    return load()
  }, [load])

  // 임베딩 전용 모델(평가 도구용 bge-m3 등)은 대화도 측정도 못 하므로 목록에서 뺀다 — 목록에 있으면
  // 이름순으로 맨 앞에 와서 기본 선택이 된다. 설치 정보(캐시)는 그대로 두고 보여주는 목록만 거른다.
  const chatModels = useMemo(() => models.filter((m) => !isEmbeddingOnly(m)), [models])
  const effectiveId = selectedId || chatModels[0]?.id || ''
  const selected = useMemo(
    () => chatModels.find((m) => m.id === effectiveId) || null,
    [chatModels, effectiveId],
  )

  const value = useMemo(
    () => ({
      models: chatModels,
      selectedId: effectiveId,
      setSelectedId,
      selected,
      error,
      loading,
      refresh,
    }),
    [chatModels, effectiveId, selected, error, loading, refresh],
  )

  return <ModelContext.Provider value={value}>{children}</ModelContext.Provider>
}
