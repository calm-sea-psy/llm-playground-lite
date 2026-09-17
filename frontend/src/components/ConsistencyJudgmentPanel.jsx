import { useEffect, useState } from 'react'
import {
  fetchConsistencyCoverage,
  fetchConsistencyJudgments,
  rebuildConsistencyJudgments,
  saveConsistencyVerdict,
} from '../api'
import { coverageWarning, rebuildConfirmText, rebuiltText } from '../judgmentRebuild'
import { useFeatures } from '../features'

// 일관성 대표 쌍 판정 — 모델마다 문항마다 가장 덜 비슷한 두 답을 나란히 읽고 셋 중 하나를 고른다.
// 판정은 골든 라벨을 거쳐 판정기 경계로 흘러가므로, 판정 대상 칸이 모두 끝날 때까지 모델 이름·유사도·
// 오염 표시를 보이지 않는다. 가림은 서버가 막는다(응답에 그 값이 없다) — 이 화면은 받은 것만 그린다.
// 판정은 비교 조합이 아니라 판정 파일의 칸에 저장되므로, 지금 고른 실행과 무관하게 같은 칸이 보인다 — 그래서 파일이
// 고른 실행을 덮지 못하면(새로 잰 실행을 골랐다) 알리고, 확인을 거쳐 그 실행 기준으로 칸을 다시 만들 수 있게 한다.
const VERDICTS = ['채점기 탓', '모델 탓', '섞임']
const COMPLETION_LABEL = { complete: '완료', length_limit: '길이 한도 도달', unknown: '기록 없음' }

function firstUnjudged(cells, after = -1) {
  const rest = cells.slice(after + 1).find((c) => !c.verdict)
  return rest ?? cells.find((c) => !c.verdict) ?? null
}

function RevealedMeta({ cell }) {
  const marks = cell.contamination?.length ? cell.contamination.join(', ') : '없음'
  const completion = (cell.completion ?? []).map((s) => COMPLETION_LABEL[s] ?? s).join(' / ')
  return (
    <p className="judgment-meta">
      {cell.alias} ({cell.model}) · 유사도 {Math.round((cell.char_similarity ?? 0) * 100)}% (v{cell.similarity_version}) · 오염{' '}
      {marks} · 답 상태 {completion}
      {cell.label && ` · 골든 라벨 ${cell.label}`}
      {cell.golden_excluded && ` · 골든 제외(재읽기로 판정 변경, 원래 ${cell.golden_excluded.라벨})`}
      {cell.edited_after_reveal && ' · 가림 해제 후 수정'}
      {cell.judged_after_previous_reveal && ' · 이전 판본 해제 후 판정'}
      {(cell.edit_history ?? []).map((h, i) => (
        <span key={i} className="judgment-edit">
          {' '}
          · 수정 {h.이전} → {h.이후}: {h.이유}
        </span>
      ))}
    </p>
  )
}

export default function ConsistencyJudgmentPanel({ selectedRunIds, onSaved }) {
  const features = useFeatures()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState(null)
  const [selectedToken, setSelectedToken] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reason, setReason] = useState('')
  // 대조 결과와 다시 만들기 미리보기는 그 값을 받은 선택 조합에 묶는다 — 조합이 바뀌면 읽을 때 버린다
  const selectionKey = selectedRunIds.join(',')
  const [coverageFor, setCoverageFor] = useState({ key: '', value: null })
  const [previewFor, setPreviewFor] = useState({ key: '', value: null })
  const coverage = selectionKey && coverageFor.key === selectionKey ? coverageFor.value : null
  const preview = previewFor.key === selectionKey ? previewFor.value : null

  function applyState(next, preferToken) {
    setState(next)
    const cells = next?.cells ?? []
    const keep = cells.find((c) => c.token === preferToken)
    setSelectedToken((keep ?? firstUnjudged(cells) ?? cells[0])?.token ?? null)
  }

  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetchConsistencyJudgments()
      .then((next) => {
        if (!cancelled) applyState(next)
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e))
      })
    return () => {
      cancelled = true
    }
  }, [open])

  // 닫혀 있어도 대조한다 — 리포트의 빈 판정칸이 이 패널을 가리키므로, 열기 전에 어긋남이 보여야 한다
  useEffect(() => {
    if (!selectionKey) return
    let cancelled = false
    fetchConsistencyCoverage(selectionKey.split(','))
      .then((value) => {
        if (!cancelled) setCoverageFor({ key: selectionKey, value })
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e))
      })
    return () => {
      cancelled = true
    }
  }, [selectionKey])

  async function handlePreviewRebuild() {
    const key = selectionKey
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const { preview: value } = await rebuildConsistencyJudgments(selectedRunIds, true)
      setPreviewFor({ key, value })
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function handleRebuild() {
    const key = selectionKey
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await rebuildConsistencyJudgments(selectedRunIds, false)
      applyState(next)
      setNotice(rebuiltText(next.rebuilt))
      setCoverageFor({ key, value: await fetchConsistencyCoverage(selectedRunIds) })
      // 칸이 바뀌면 일관성을 순위에서 뺄지도 다시 센다
      onSaved?.()
    } catch (e) {
      if (e.status === 409) {
        // 그새 칸이 다 생겼다 — 다시 만들지 않고 지금 상태를 받는다
        const [nextState, nextCoverage] = await Promise.all([
          fetchConsistencyJudgments().catch(() => null),
          fetchConsistencyCoverage(selectedRunIds).catch(() => null),
        ])
        if (nextState) applyState(nextState)
        setCoverageFor({ key, value: nextCoverage })
      }
      setError(String(e.message || e))
    } finally {
      setPreviewFor({ key: '', value: null })
      setBusy(false)
    }
  }

  async function handleVerdict(verdict) {
    if (!selectedToken) return
    const current = state?.cells?.find((c) => c.token === selectedToken)
    // 가림이 풀린 뒤 판정을 바꿀 때는 이유가 필요하다 — 서버도 거절하지만 먼저 알려준다
    if (state?.revealed && current?.verdict && current.verdict !== verdict && !reason.trim()) {
      setError('가림이 풀린 뒤 판정을 바꾸려면 이유를 한 줄 적어 주세요.')
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await saveConsistencyVerdict(selectedToken, verdict, reason.trim() || null)
      setReason('')
      const cells = next.cells ?? []
      const index = cells.findIndex((c) => c.token === selectedToken)
      // 드러나기 전에는 다음 미판정 칸으로 넘어가고, 판정을 고치는 중이면 그 칸에 머문다
      const following = firstUnjudged(cells, index)
      applyState(next, following?.token ?? selectedToken)
      // 판정이 바뀌면 일관성을 순위에서 뺄지도 다시 센다
      onSaved?.()
    } catch (e) {
      if (e.status === 409) {
        // 서버가 다시 떠 칸 토큰과 순서가 새로 발급됐다 — 저장된 판정은 파일에 있어 목록만 다시 받는다
        const next = await fetchConsistencyJudgments().catch(() => null)
        if (next) applyState(next)
        setNotice('서버가 다시 시작돼 칸 순서가 새로 섞였습니다. 보던 칸을 다시 골라 판정해 주세요.')
      } else {
        setError(String(e.message || e))
      }
    } finally {
      setBusy(false)
    }
  }

  const cells = state?.cells ?? []
  const selected = cells.find((c) => c.token === selectedToken) ?? null
  const progress = state?.progress
  const staleWarning = coverageWarning(coverage)

  return (
    <section className="judgment-panel">
      <div className="compare-notes-head">
        <h2>일관성 판정 — 대표 쌍 전수</h2>
        <button type="button" className="ghost" onClick={() => setOpen((v) => !v)}>
          {open ? '닫기' : '열기'}
        </button>
      </div>

      {!open && coverage?.applicable && !coverage.covered && (
        <div className="compare-warning">
          판정 파일에 지금 고른 실행의 칸이 없어 리포트의 판정칸이 비어 나옵니다 — 열어서 판정을 다시 시작할 수 있습니다.
        </div>
      )}

      {open && error && <div className="error">{error}</div>}
      {open && notice && <div className="compare-warning">{notice}</div>}

      {open && state && !state.available && (
        <div className="judgment-note">
          판정 후보 파일이 없습니다. 지금 고른 실행으로 칸을 만드세요
          {features.maintenance_cli && (
            <>
              {' '}— 또는 backend에서 <code>python maintenance.py extract-consistency-candidates</code>를 실행하세요(모델별
              최신 선정 실행 기준)
            </>
          )}
          .
          {coverage?.applicable && !preview && (
            <div className="export-confirm-actions judgment-rebuild-actions">
              <button type="button" disabled={busy} onClick={handlePreviewRebuild}>
                지금 고른 실행으로 판정 칸 만들기
              </button>
            </div>
          )}
        </div>
      )}

      {open && preview && (
        <div className="export-confirm">
          <p>{rebuildConfirmText(preview)}</p>
          <div className="export-confirm-actions">
            <button type="button" disabled={busy} onClick={handleRebuild}>
              판정 칸 다시 만들기
            </button>
            <button type="button" className="ghost" disabled={busy} onClick={() => setPreviewFor({ key: '', value: null })}>
              취소
            </button>
          </div>
        </div>
      )}

      {open && state?.available && (
        <>
          <p className="judgment-note">
            판정 {progress.judged} / {progress.targets}칸 · 판정 보류 {progress.held}칸(길이 한도 — 자동, 읽지 않음) · 전체{' '}
            {progress.total}칸
          </p>
          {state.revealed ? (
            <p className="judgment-note">
              가림 해제됨({new Date(state.revealed_at).toLocaleString()}) — 이후에 고친 판정에는 &lsquo;가림 해제 후
              수정&rsquo;이 남습니다.
              {state.golden &&
                ` 골든 양성 ${state.golden.counts.positive} · 음성 ${state.golden.counts.negative}` +
                  (state.golden.excluded ? ` (재읽기로 ${state.golden.excluded}쌍 제외 — 다시 채우지 않음)` : '')}
            </p>
          ) : (
            <p className="judgment-note">
              판정 대상 칸을 모두 판정할 때까지 모델 이름·유사도·오염 표시를 가립니다. 칸 순서는 섞여 있습니다.
            </p>
          )}
          {(state.previously_revealed_at ?? []).length > 0 && (
            <div className="compare-warning">
              이전 판본에서 가림이 해제된 적이 있습니다(
              {state.previously_revealed_at.map((t) => new Date(t).toLocaleString()).join(', ')}) — 모델을 한 번 본 뒤의
              판정이라 완전히 가려진 판정이 아닐 수 있고, 저장하는 칸마다 그 사실이 기록됩니다.
            </div>
          )}
          {staleWarning && (
            <div className="compare-warning">
              {staleWarning}
              {!preview && (
                <div className="export-confirm-actions judgment-rebuild-actions">
                  <button type="button" disabled={busy} onClick={handlePreviewRebuild}>
                    지금 고른 실행으로 판정 다시 시작
                  </button>
                </div>
              )}
            </div>
          )}
          {(state.error_count > 0 || state.warning_count > 0 || state.invalid_count > 0) && (
            <div className="compare-warning">
              {state.error_count > 0 && `판정 파일 오류 ${state.error_count}건 `}
              {state.warning_count > 0 && `라벨 어긋남 ${state.warning_count}건 `}
              {state.invalid_count > 0 && `무효로 옮긴 판정 ${state.invalid_count}건 `}
              {features.maintenance_cli && (
                <>
                  — 내용은 <code>python maintenance.py check-consistency-labels</code>로 확인하세요.
                </>
              )}
              {[...state.errors, ...state.warnings].length > 0 && (
                <ul>
                  {[...state.errors, ...state.warnings].map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {cells.length > 0 && (
            <div className="judgment-body">
              <ol className="judgment-cells">
                {cells.map((c, i) => (
                  <li key={c.token}>
                    <button
                      type="button"
                      className={`judgment-cell${c.token === selectedToken ? ' selected' : ''}${c.verdict ? ' done' : ''}`}
                      onClick={() => setSelectedToken(c.token)}
                    >
                      <span>
                        칸 {i + 1} · {c.item_id}
                      </span>
                      <span className="judgment-cell-verdict">{c.verdict ?? '미판정'}</span>
                    </button>
                  </li>
                ))}
              </ol>

              {selected && (
                <div className="judgment-detail">
                  <p className="judgment-question">
                    <strong>{selected.item_id}</strong> {selected.question}
                  </p>
                  {state.revealed && <RevealedMeta cell={selected} />}
                  {state.revealed && (
                    <input
                      type="text"
                      className="judgment-reason"
                      placeholder="판정을 바꾸는 이유 한 줄 (가림 해제 뒤 수정 — 필수)"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  )}
                  <div className="judgment-verdicts">
                    {VERDICTS.map((v) => (
                      <button
                        key={v}
                        type="button"
                        className={selected.verdict === v ? 'verdict-active' : 'ghost'}
                        disabled={busy}
                        onClick={() => handleVerdict(v)}
                      >
                        {v}
                      </button>
                    ))}
                    {selected.judged_at && (
                      <span className="judgment-note">판정 {new Date(selected.judged_at).toLocaleString()}</span>
                    )}
                  </div>
                  <div className="judgment-answers">
                    {[selected.a, selected.b].map((side) => (
                      <div key={side.index} className="judgment-answer">
                        <div className="judgment-answer-head">답 {side.index + 1}</div>
                        <div className="judgment-answer-text">{side.text}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {state.revealed && state.held_cells.length > 0 && (
            <div className="judgment-held">
              <h3>판정 보류 칸</h3>
              <ul>
                {state.held_cells.map((c) => (
                  <li key={c.pair_id}>
                    {c.item_id} · <RevealedMeta cell={c} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  )
}
