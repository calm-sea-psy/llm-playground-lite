import { createContext, useContext } from 'react'

// 선택된 모델과 모델 상세 캐시. 여러 페이지가 함께 참조한다.
// (컴포넌트가 아닌 export는 별도 파일에 둔다 — oxlint react/only-export-components)
export const ModelContext = createContext(null)

/** 임베딩 전용 모델(예: 일관성 판정기 평가용 `bge-m3`) — 대화도 측정도 할 수 없어 선택하지 않는다.
 * capabilities가 비어 있으면(`/api/show` 실패) 모르는 것이지 임베딩 전용이 아니므로 막지 않는다. */
export function isEmbeddingOnly(model) {
  const caps = model?.capabilities ?? []
  return caps.length > 0 && !caps.includes('completion')
}

export function useModels() {
  const ctx = useContext(ModelContext)
  if (!ctx) throw new Error('useModels는 ModelProvider 안에서만 쓸 수 있다')
  return ctx
}
