import { useEffect, useMemo, useState } from 'react'
import { fetchCompareNotesStatus, generateCompareNotes, rateCompareNote } from '../api'
import { markerBadges, verificationSummary } from '../noteVerify'
import { BASELINE_ROW_ID, gatedWeights, normalize, PRESETS, toRow, weightedScore } from '../scoring'
import RatingStars from './RatingStars'

// 비교 노트 — 선택된 실행들의 모델 각각에게 비교 표 전체(+베이스라인)를
// 주고 특이사항을 3~5개 불릿으로 쓰게 한다. 채점하지 않는 표시 정보라 종합
// 점수에는 안 들어가지만, 정렬은 종합 점수 순으로 한다 — "노트 품질 순이
// 아니라 점수 순"임을 화면에 명시한다.
//
// 정렬 점수는 CompareRadar의 슬라이더 상태(수시로 바뀜)에 매달지 않고 usage
// 프리셋으로 고정 계산한다 — D의 목록 미리보기 점수와 같은 이유로, 슬라이더를
// 만질 때마다 노트 순서가 흔들리면 산만하다. 베이스라인도 정규화 풀에 넣는다
// — CompareRadar의 "베이스라인을 정규화 비교군에 포함" 기본값이 켜짐(F)이라,
// 여기서 빼면 같은 화면에 "종합 점수" 두 개가 서로 다른 기준으로 보인다.
const BULLET_RE = /^\s*([*\-+•·]|\d+[.)])\s+/

/** 인용 검증 표식 — **실패한 불릿도 빼지 않고** 옆에 표식을 단다. 빼면 왜 사라졌는지
 * 알 수 없고 노트 길이가 달라져 모델 간 비교도 흔들린다. 검증은 불릿 순서대로 백엔드가 매겼다. */
function NoteText({ note, verification }) {
  const bullets = verification?.bullets ?? []
  if (bullets.length === 0) return <p className="compare-note-text">{note}</p>
  let next = 0
  const lines = (note ?? '').split('\n')
  const summary = verificationSummary(verification)
  return (
    <div className="compare-note-text">
      {summary && <p className="note-verify-summary">{summary}</p>}
      {lines.map((line, i) => {
        const isBullet = BULLET_RE.test(line) && next < bullets.length
        const badges = isBullet ? markerBadges(bullets[next++]) : []
        return (
          <div key={i} className="note-line">
            {line || ' '}
            {badges.map((badge, j) => (
              <span key={j} className={`note-verify ${badge.className}`} title={badge.title}>
                {badge.label}
              </span>
            ))}
          </div>
        )
      })}
    </div>
  )
}

export default function CompareNotes({ selectedDetails, baseline, demotion, tableText, refreshToken }) {
  const [notes, setNotes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // 캐시에 이미 있는 노트(화면 버튼으로 만들었든 리포트 내보내기에서 만들었든 같은 조합이면
  // 같은 노트)를 읽어온다 — 모델을 부르지 않으므로 "버튼으로만 생성" 규칙과 충돌하지 않는다.
  const runKey = selectedDetails.map((d) => d.id).join(',')
  useEffect(() => {
    const runIds = runKey ? runKey.split(',') : []
    // 표 텍스트가 없으면(별칭·상세를 불러오는 중) 캐시 키가 확정되지 않았다 — 조회하지 않는다
    if (runIds.length === 0 || !tableText) return
    let cancelled = false
    fetchCompareNotesStatus(runIds, tableText)
      .then((status) => {
        if (!cancelled) setNotes(Object.keys(status.notes ?? {}).length ? status : null)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [runKey, tableText, refreshToken])

  const sortScore = useMemo(() => {
    const rows = selectedDetails.map((d) => toRow(d))
    if (baseline?.run) rows.push(toRow(baseline.run, BASELINE_ROW_ID))
    const norm = normalize(rows)
    const weights = gatedWeights(PRESETS.usage.build(), demotion)
    return Object.fromEntries(
      selectedDetails.map((d) => [d.id, weightedScore(norm, weights, d.id)]),
    )
  }, [selectedDetails, baseline, demotion])

  async function handleGenerate() {
    setBusy(true)
    setError('')
    try {
      const runIds = selectedDetails.map((d) => d.id)
      const data = await generateCompareNotes(runIds, tableText)
      setNotes(data)
      // 한 모델이 실패해도 나머지 노트는 남는다 — 실패한 모델은 다음 생성에서 다시 시도된다
      const failed = Object.keys(data.failed ?? {})
      if (failed.length) {
        const names = failed.map((id) => selectedDetails.find((d) => d.id === id)?.model ?? id)
        setError(`노트를 만들지 못한 모델: ${names.join(', ')} — 다시 생성하면 이 모델만 다시 시도합니다`)
      }
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const sortedIds = notes
    ? Object.keys(notes.notes).sort((a, b) => (sortScore[b] ?? -1) - (sortScore[a] ?? -1))
    : []

  return (
    <section className="compare-notes">
      <div className="compare-notes-head">
        <h2>비교 노트</h2>
        <button type="button" className="ghost" onClick={handleGenerate} disabled={busy || !tableText}>
          {busy ? '생성 중…' : notes ? '다시 생성' : '노트 생성'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {notes && (
        <>
          <p className="compare-notes-order-note">
            정렬은 노트 품질이 아니라 종합 점수(품질·도구 가중치) 순입니다.
          </p>
          <ul className="compare-notes-list">
            {sortedIds.map((runId) => {
              const entry = notes.notes[runId]
              const score = sortScore[runId]
              return (
                <li key={runId} className="compare-note-card">
                  <div className="compare-note-head">
                    <span className="compare-note-model">{entry.model}</span>
                    <span className="compare-note-score">
                      종합 {score == null ? '측정 안 됨' : score.toFixed(3)}
                    </span>
                  </div>
                  <NoteText note={entry.note} verification={entry.verification} />
                  <RatingStars
                    rating={entry.rating}
                    ratingNote={entry.rating_note}
                    onSubmit={(nextRating, nextNote) =>
                      rateCompareNote(notes.key, runId, { rating: nextRating, note: nextNote }).then(setNotes)
                    }
                  />
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
