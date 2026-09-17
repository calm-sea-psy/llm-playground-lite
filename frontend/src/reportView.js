// 리포트 확인하기 — 화면이 보여 주는 PDF는 **내보낸 바로 그 파일**이다(화면용으로 따로 그리지 않는다).
// 내보낸 뒤 선택·가중치·판정이 바뀌면 그 파일은 지금 화면과 다른 조건이라, 내보낸 조건을 열쇠로 들고 다닌다.

/** 리포트 내용을 정하는 화면 조건 — 고른 실행, 가중치(프리셋 이름 포함), 판정 저장 횟수(일관성 게이트가 따라 바뀐다). */
export function exportKey({ selectedIds, weighting, judgmentRefresh }) {
  return JSON.stringify([selectedIds, weighting.presetName, weighting.weights, judgmentRefresh])
}

/** 지금 조건으로 내보낸 파일이 있으면 그것을 열고, 없으면 먼저 내보낸 뒤 연다. */
export function viewAction(lastExport, key) {
  return lastExport?.key === key ? 'open' : 'export'
}
