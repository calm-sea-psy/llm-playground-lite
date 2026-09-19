// 리포트에 실을 장 — **다섯은 늘 싣고**, 나머지는 내보낼 때 고른다.
// 늘 싣는 다섯은 "무엇을 쟀고 그 값이 어디서 나왔나"를 이룬다: 표지는 결론 면이고, 지표별 비교와 측정값이 값,
// Local vs Cloud가 로컬을 쓰는 까닭, 부록이 그 값 뒤의 실제 답이다. 나머지는 읽는 사람에 따라 필요가 갈린다.
//
// `id`는 백엔드(`backend/report.py`의 `OPTIONAL_CHAPTERS`)와 **같은 글자**다 — 여기서 바꾸면 그쪽도 바꾼다.
// 순서는 리포트에 실리는 순서다(고른 순서가 아니라).

export const ALWAYS_CHAPTERS = [
  '표지 (결론 면)',
  '지표별 비교',
  '측정값',
  'Local vs Cloud',
  '부록 — 지표별 실패 사례',
]

export const OPTIONAL_CHAPTERS = [
  { id: 'requirements', label: '과제 요건 대응', note: '요건마다 이 리포트의 어느 장이 답하는지' },
  { id: 'selection_basis', label: '필수 통과 조건과 선정 근거', note: '표지의 결론을 단계별로 펼친 근거' },
  { id: 'ranking', label: '종합 순위', note: '종합 점수 막대 — 선정 근거가 아니라 정렬 도구다' },
  { id: 'weights', label: '가중치 민감도', note: '가중치를 바꾸면 순위가 뒤집히는지' },
  { id: 'breakdown', label: '점수 구성', note: '누가 어느 카테고리에서 벌고 잃었나' },
  { id: 'commercial', label: '상용 대비', note: '로컬로 충분한가 — 기준선과의 거리' },
  { id: 'assignment_questions', label: '과제 10문항 / Cloud 5문항', note: '문항마다의 답과 판정' },
  { id: 'variance', label: '변동 — 중앙값 ±표준편차 / 분포', note: '속도가 선정을 가른 판에만 내용이 있다' },
  { id: 'model_cards', label: '모델 카드와 식별값', note: '어떤 모델 파일을 쟀는지' },
  { id: 'conditions', label: '측정 조건 상세', note: '무엇을 어떤 설정으로 쟀나' },
  { id: 'scoring', label: '채점과 검증', note: '지표마다 무엇으로 채점했고 어긋남을 무엇이 막나' },
  { id: 'consistency', label: '일관성/재현성 상세', note: '고르면 응답 전문 파일도 함께 나온다' },
  { id: 'limits', label: '한계와 개선 과제, 운영 권고', note: '이 리포트가 말하지 못하는 것' },
]

const STORAGE_KEY = 'compare.report.chapters'
const KNOWN = new Set(OPTIONAL_CHAPTERS.map((c) => c.id))

/** 지난번에 고른 장. 기억이 없으면 `null`이 아니라 **빈 배열**이다 — 처음 여는 사람은 기본 다섯 장으로 시작한다.
 * 이 브라우저에만 남고 서버로 가지 않는다(사생활이 아니라 편의 기록이라 그렇다). 읽기·쓰기가 막힌 곳(사생활 창·
 * 저장소 차단)에서도 화면은 그대로 돈다 — 그때는 기억이 없는 것과 같다. */
export function loadChapterChoice() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const ids = raw ? JSON.parse(raw) : []
    return Array.isArray(ids) ? ids.filter((id) => KNOWN.has(id)) : []
  } catch {
    return []
  }
}

export function saveChapterChoice(ids) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids.filter((id) => KNOWN.has(id))))
  } catch {
    // 기억하지 못해도 이번 내보내기는 그대로 간다
  }
}
