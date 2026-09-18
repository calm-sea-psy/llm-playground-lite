// 지표 재실행의 이유 — 재실행 요청에 싣고, 합친 결과에서 그 지표가 온 출처 옆에 적는다.
// 리포트는 측정 조건 상세의 혼합 실행 줄에 같은 말로 적는다.

/** 보낼 이유 — 줄바꿈·겹친 공백을 한 칸으로 접는다(리포트의 한 줄에 실린다). 비면 null이고 서버도 받지 않는다. */
export function rerunReasonText(text) {
  const folded = String(text ?? '').split(/\s+/).filter(Boolean).join(' ')
  return folded || null
}

/** 출처 옆에 붙는 이유 — 이유 기록 전 재실행은 없다고 적는다(빈칸이면 이유가 없어 다시 잰 것으로 읽힌다). */
export function rerunReasonLabel(reason) {
  return reason ? `이유: ${reason}` : '이유 기록 없음'
}
