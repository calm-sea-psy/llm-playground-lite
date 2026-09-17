import { useState } from 'react'

const STAR_VALUES = [1, 2, 3, 4, 5]

// 별점(1~5) + 선택적 메모. 저장 방식은 onSubmit(rating, note)
// 콜백으로 밖에서 주입한다 — 원래는 대화 메시지 전용(rateMessage 직접 호출)
// 이었는데, 비교 노트에서도 같은 UI를 재사용하려고 일반화했다.
// disabled는 옛날의 "conversationId/messageId가 없으면 숨김" 자리를 대신한다.
export default function RatingStars({ rating, ratingNote, onSubmit, disabled }) {
  const [noteDraft, setNoteDraft] = useState(ratingNote || '')
  const [noteOpen, setNoteOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // ratingNote는 서버가 준 값(다른 곳에서 매긴 별점을 이어서 보거나, 우리가 막
  // 저장한 값)이고 noteDraft는 입력창의 편집 중 값이다. 서버 값이 바뀌면 편집을
  // 다시 그 값으로 맞춘다 — effect가 아니라 렌더 중 조정(React 공식 패턴)으로 한다:
  // 이건 "prop이 바뀔 때 state를 맞추는" 경우라 useEffect로 하면 불필요한 리렌더가
  // 한 번 더 생긴다.
  const [prevRatingNote, setPrevRatingNote] = useState(ratingNote)
  if (ratingNote !== prevRatingNote) {
    setPrevRatingNote(ratingNote)
    setNoteDraft(ratingNote || '')
  }

  if (disabled) return null

  async function submit(nextRating, nextNote) {
    setBusy(true)
    setError('')
    try {
      await onSubmit(nextRating, nextNote)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  function handleStarClick(value) {
    if (busy) return
    // 이미 준 별점을 다시 누르면 취소(토글) — 잘못 눌렀을 때 되돌릴 방법이 필요하다.
    const next = value === rating ? null : value
    if (next === null) setNoteOpen(false)
    submit(next, next === null ? null : noteDraft || null)
  }

  function handleNoteBlur() {
    if (rating == null || busy) return
    submit(rating, noteDraft || null)
  }

  return (
    <div className="rating">
      <div className="rating-stars" role="group" aria-label="응답 평가">
        {STAR_VALUES.map((v) => (
          <button
            key={v}
            type="button"
            className={`star${rating != null && v <= rating ? ' filled' : ''}`}
            disabled={busy}
            onClick={() => handleStarClick(v)}
            aria-label={`${v}점`}
            title={v === rating ? '다시 누르면 취소' : `${v}점`}
          >
            ★
          </button>
        ))}
        {rating != null && (
          <button
            type="button"
            className="rating-note-toggle"
            onClick={() => setNoteOpen((v) => !v)}
          >
            메모{ratingNote ? ' ✓' : ''}
          </button>
        )}
      </div>
      {rating != null && noteOpen && (
        <input
          className="rating-note"
          type="text"
          placeholder="메모 (선택)"
          value={noteDraft}
          onChange={(e) => setNoteDraft(e.target.value)}
          onBlur={handleNoteBlur}
        />
      )}
      {error && <div className="rating-error">{error}</div>}
    </div>
  )
}
