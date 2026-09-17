// 시스템 프롬프트 선택 상태 → 서버에 보낼 식별 정보. SystemPromptPicker와 두 페이지가 같이 쓴다.
//
// `selection`: '' (없음) | `saved:<name>` (저장된 프롬프트) | 'custom' (직접 입력)
// 저장된 것을 고른 뒤 본문을 고쳤으면 `dirty`다 — 저장하기 전까지는 그 이름으로 기록하지 않는다.
// 고친 본문에 옛 이름을 붙이면 조건 기록이 거짓이 된다.
export function promptIdentity(prompts, selection, content) {
  if (!selection.startsWith('saved:')) return { name: null, dirty: false, saved: null }
  const saved = prompts.find((p) => p.name === selection.slice('saved:'.length)) ?? null
  const dirty = saved ? content.trim() !== saved.content.trim() : true
  return { name: saved && !dirty ? saved.name : null, dirty, saved }
}
