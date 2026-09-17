import { useEffect, useState } from 'react'
import { deleteConversation, fetchConversations } from '../api'

// 선택된 모델의 저장된 대화 목록. 모델을 바꾸면 그 모델 것만 다시 불러온다.
// refreshKey가 바뀌면 다시 조회한다(턴이 끝나 제목/수정시각이 바뀔 때 ChatPage가 올린다).
export default function ConversationList({ model, activeId, refreshKey, onSelect, onDeleted }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // model이 바뀔 때마다(또는 턴이 끝나 refreshKey가 올라갈 때) 다시 불러온다.
  // 목록 조회는 "외부 시스템과 동기화"라 effect가 맞는 자리다.
  useEffect(() => {
    let alive = true
    // oxlint-disable-next-line react/set-state-in-effect -- model prop 변경에 따른 목록 전환, setState는 콜백(비동기) 안에서만
    setLoading(Boolean(model))
    if (!model) {
      setItems([])
      return undefined
    }
    fetchConversations(model)
      .then((list) => {
        if (alive) setItems(list)
      })
      .catch((e) => {
        if (alive) setError(String(e.message || e))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [model, refreshKey])

  async function handleDelete(e, id) {
    e.stopPropagation()
    if (!window.confirm('이 대화를 삭제할까요? 되돌릴 수 없습니다.')) return
    try {
      await deleteConversation(id)
      setItems((cur) => cur.filter((c) => c.id !== id))
      onDeleted?.(id)
    } catch (err) {
      setError(String(err.message || err))
    }
  }

  if (!model) return null

  return (
    <div className="field">
      <span>저장된 대화</span>
      {error && <div className="conv-error">{error}</div>}
      {items.length === 0 ? (
        <div className="model-empty">{loading ? '불러오는 중…' : '저장된 대화가 없습니다'}</div>
      ) : (
        <ul className="conv-list">
          {items.map((c) => (
            <li key={c.id} className={`conv-item${c.id === activeId ? ' selected' : ''}`}>
              <button type="button" className="conv-select" onClick={() => onSelect(c.id)}>
                <span className="conv-title">{c.title}</span>
              </button>
              <button
                type="button"
                className="conv-delete"
                onClick={(e) => handleDelete(e, c.id)}
                aria-label="대화 삭제"
                title="삭제"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
