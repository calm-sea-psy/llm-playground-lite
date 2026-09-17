// 판정 파일이 지금 고른 실행을 덮지 못할 때의 안내, 그리고 판정 칸을 다시 만들 때의 확인·결과 문장.
// 옛 실행과 새 실행은 모델 이름이 같아 이름만으로는 갈리지 않는다 — 시작 시각을 붙인다.

export function runLabel(run) {
  const when = run.started_at ? new Date(run.started_at).toLocaleString() : '시작 시각 기록 없음'
  return `${run.model} (${when})`
}

/** 리포트가 판정을 `답을 낸 실행 + 문항`으로 찾으므로, 파일에 없는 실행이 있으면 화면에 판정이 차 있어도 리포트 판정칸은 빈다. */
export function coverageWarning(coverage) {
  if (!coverage?.applicable || coverage.covered) return null
  return (
    `판정 파일은 다른 실행 기준입니다(${coverage.file_runs.map(runLabel).join(', ')}) — ` +
    `지금 고른 실행 중 ${coverage.missing.map(runLabel).join(', ')}의 칸이 없어, 여기서 판정해도 리포트의 판정칸은 비어 나옵니다.`
  )
}

/** 쓰기 전에 보여 주는 수 — 무엇이 이어지고 무엇이 옮겨지는지. 질문이 같아도 답이 다르면 잇지 않는다는 것을 함께 적는다. */
export function rebuildConfirmText(preview) {
  const parts = [`지금 고른 실행 ${preview.runs.length}개 기준으로 판정 칸을 다시 만듭니다.`]
  parts.push(
    preview.carried > 0
      ? `같은 칸에서 같은 답을 읽은 판정 ${preview.carried}칸은 이어 받습니다.`
      : '같은 답을 읽은 판정이 없어 이어 받는 칸은 없습니다 — 판정은 두 답의 글을 읽고 내린 것이라, 질문이 같아도 답이 다르면 잇지 않습니다.',
  )
  if (preview.moved_to_invalid > 0) {
    parts.push(`잇지 못한 판정·라벨 ${preview.moved_to_invalid}건은 지우지 않고 무효 기록으로 옮깁니다.`)
  }
  parts.push(
    `새로 판정할 칸은 ${preview.to_judge}칸입니다${preview.held > 0 ? `(판정 보류 ${preview.held}칸은 길이 한도라 자동으로 빠짐)` : ''}.`,
  )
  if (preview.blind) parts.push('판정이 끝날 때까지 모델 이름·유사도를 다시 가립니다.')
  return parts.join(' ')
}

export function rebuiltText(summary) {
  const extra = [
    summary.carried > 0 && `이어 받은 판정 ${summary.carried}칸`,
    summary.moved_to_invalid > 0 && `무효 기록으로 옮긴 판정 ${summary.moved_to_invalid}건`,
  ].filter(Boolean)
  return `판정 칸을 다시 만들었습니다 — 새로 판정할 칸 ${summary.to_judge}칸${extra.map((t) => ` · ${t}`).join('')}`
}
