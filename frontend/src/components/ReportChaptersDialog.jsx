import { useState } from 'react'
import { ALWAYS_CHAPTERS, OPTIONAL_CHAPTERS } from '../reportChapters'

// 내보내기 전에 무엇을 실을지 고른다 — 열네 장을 다 싣는 판은 서른 쪽이 넘어 읽는 사람이 고를 자리가 없었다.
// 아무것도 고르지 않으면 기본 다섯 장만 나온다(비어 있는 것이 곧 기본이다 — 따로 `기본` 단추를 두지 않는다).
export default function ReportChaptersDialog({ chosen, onConfirm, onCancel }) {
  const [ids, setIds] = useState(chosen)
  const toggle = (id) => setIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  return (
    <div className="chapter-overlay" role="dialog" aria-modal="true" aria-label="리포트에 실을 장 고르기">
      <div className="chapter-panel">
        <h2>리포트에 실을 장</h2>
        <p className="chapter-always">
          늘 실린다 — {ALWAYS_CHAPTERS.join(' · ')}
        </p>
        <div className="chapter-list">
          {OPTIONAL_CHAPTERS.map((c) => (
            <label key={c.id} className="chapter-item">
              <input type="checkbox" checked={ids.includes(c.id)} onChange={() => toggle(c.id)} />
              <span className="chapter-label">{c.label}</span>
              <span className="chapter-note">{c.note}</span>
            </label>
          ))}
        </div>
        <div className="chapter-actions">
          <span className="chapter-count">
            {ids.length ? `고른 장 ${ids.length}개` : '고른 장 없음 — 기본 다섯 장만 나옵니다'}
          </span>
          <button type="button" className="ghost" onClick={onCancel}>
            취소
          </button>
          <button type="button" onClick={() => onConfirm(ids)}>
            확인
          </button>
        </div>
      </div>
    </div>
  )
}
