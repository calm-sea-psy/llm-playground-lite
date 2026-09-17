import { useState } from 'react'
import { deleteSystemPrompt, saveSystemPrompt } from '../api'
import { promptIdentity } from '../promptIdentity'

// 시스템 프롬프트 고르기·보기·편집 — 일반 대화와 성능 테스트(프롬프트 실험)가 같이 쓴다.
// 목록·편집·저장이 한 곳이라 두 페이지가 갈라지지 않는다.
//
// `selection`: '' (없음) | `saved:<name>` (저장된 프롬프트) | 'custom' (직접 입력 — allowCustom일 때만)
// `content`: 지금 적용될 본문. 저장된 것을 고른 뒤 고쳤으면 `dirty`다 — 부모가 `promptIdentity`로 판단한다.

export default function SystemPromptPicker({
  prompts,
  selection,
  content,
  onChange,
  onPromptsChanged,
  allowCustom = false,
  disabled = false,
  label = '시스템 프롬프트',
}) {
  const { saved, dirty } = promptIdentity(prompts, selection, content)
  const [open, setOpen] = useState(false)
  const [draftName, setDraftName] = useState(saved?.name ?? '')
  const [draftTitle, setDraftTitle] = useState(saved?.title ?? '')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  // 선택이 바깥에서 바뀌면(대화 불러오기 등) 저장용 이름·제목 칸을 그 프롬프트로 맞춘다 — 렌더 중 조정 패턴
  const [seenSelection, setSeenSelection] = useState(selection)
  if (seenSelection !== selection) {
    setSeenSelection(selection)
    setDraftName(saved?.name ?? '')
    setDraftTitle(saved?.title ?? '')
    setStatus('')
  }

  function select(value) {
    if (value.startsWith('saved:')) {
      const p = prompts.find((x) => x.name === value.slice('saved:'.length))
      onChange({ selection: value, content: p?.content ?? '' })
    } else if (value === 'custom') {
      onChange({ selection: value, content: saved ? saved.content : content })
      setOpen(true)
    } else {
      onChange({ selection: '', content: '' })
    }
  }

  async function save() {
    const name = draftName.trim()
    setBusy(true)
    setStatus('')
    try {
      const entry = await saveSystemPrompt(name, { title: draftTitle.trim() || name, content })
      await onPromptsChanged?.()
      onChange({ selection: `saved:${entry.name}`, content: entry.content })
      setStatus('저장했습니다 — 두 페이지 목록에 함께 올라갑니다')
    } catch (e) {
      setStatus(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!saved) return
    // 결과 파일·대화는 본문을 스냅숏해 두므로 지워도 과거 기록의 해석은 남는다
    if (!window.confirm(`"${saved.title}" 프롬프트 파일을 지웁니다. 과거 결과·대화에 저장된 본문은 그대로 남습니다.`)) return
    setBusy(true)
    try {
      await deleteSystemPrompt(saved.name)
      await onPromptsChanged?.()
      onChange({ selection: '', content: '' })
      setStatus('')
    } catch (e) {
      setStatus(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const showBody = selection !== ''
  return (
    <section className="prompt-picker">
      <div className="prompt-picker-row">
        <label className="prompt-picker-label" htmlFor="prompt-picker-select">
          {label}
        </label>
        <select
          id="prompt-picker-select"
          value={selection}
          onChange={(e) => select(e.target.value)}
          disabled={disabled}
        >
          <option value="">없음 — 시스템 프롬프트 없이 실행됩니다</option>
          {prompts.map((p) => (
            <option key={p.name} value={`saved:${p.name}`}>
              {p.title}
            </option>
          ))}
          {allowCustom && <option value="custom">직접 입력</option>}
        </select>
        {showBody && (
          <button type="button" className="ghost" onClick={() => setOpen((v) => !v)}>
            {open ? '접기' : '본문 보기·편집'}
          </button>
        )}
      </div>
      {showBody && open && (
        <div className="prompt-editor">
          <textarea
            rows={5}
            value={content}
            disabled={disabled}
            placeholder="모델에게 가는 본문 — 제목은 여기가 아니라 아래 제목 칸에 적는다"
            onChange={(e) => onChange({ selection, content: e.target.value })}
          />
          <div className="prompt-editor-actions">
            <input
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              placeholder="이름 (영문 소문자·숫자·-)"
              disabled={disabled || busy}
            />
            <input
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              placeholder="제목 (화면에 보이는 이름)"
              disabled={disabled || busy}
            />
            <button type="button" onClick={save} disabled={disabled || busy || !draftName.trim() || !content.trim()}>
              저장
            </button>
            {saved && (
              <button type="button" className="ghost" onClick={remove} disabled={disabled || busy}>
                삭제
              </button>
            )}
          </div>
          {saved && dirty && (
            <p className="prompt-dirty-note">
              저장하지 않은 수정이 있습니다 — 이대로 쓰면 "{saved.title}"이 아니라 직접 입력으로 기록됩니다.
            </p>
          )}
          {status && <p className="prompt-status">{status}</p>}
        </div>
      )}
    </section>
  )
}
