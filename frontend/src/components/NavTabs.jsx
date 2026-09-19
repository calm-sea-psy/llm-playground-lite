import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { fetchTestsetReadiness } from '../api'
import { useFeatures } from '../features'

// 일하는 차례대로 놓는다 — 세트 준비 → 성능 테스트 → 결과 비교 → 도구 → 일반 대화.
// 채팅은 측정과 독립이라 끝에 둔다.
//
// **측정과 비교는 세트가 실제로 돌 수 있을 때만 연다.** 잣대는 "준비 화면을 밟았는가"가 아니라
// `GET /api/testsets/readiness` — 세트 파일과 그 세트가 가리키는 문서가 다 있는가다. 공개 샘플 세트로
// 받은 그대로 한 사이클을 도는 길을 막지 않으려는 것이다. 막을 때는 **숨기지 않고 까닭을 붙여 비활성**으로
// 둔다: 잠긴 탭만 보이면 사람이 할 수 있는 일이 없다.
const CLASS = ({ isActive }) => `nav-tab${isActive ? ' active' : ''}`

// `readiness`를 받으면 그 값을 쓴다 — 세트 준비 화면은 이미 들고 있어서, 마지막 단계를 지나자마자 잠금이 풀린다.
// 받지 않은 화면에서는 열릴 때 한 번 묻는다.
export default function NavTabs({ readiness: given }) {
  const features = useFeatures()
  const [fetched, setFetched] = useState(null)
  const readiness = given ?? fetched

  useEffect(() => {
    if (!features.testset_tools || given) return undefined
    let alive = true
    fetchTestsetReadiness()
      .then((result) => alive && setFetched(result))
      .catch(() => alive && setFetched(null))
    return () => {
      alive = false
    }
  }, [features.testset_tools, given])

  // 준비 상태를 볼 수 없는 판(공개본·조회 실패)에서는 막지 않는다 — 모르는 것을 막는 것으로 읽지 않는다
  const notReady = readiness && !readiness.ready
  // 세트를 새로 만드는 중이면 놓인 세트는 갈아 끼우기 전의 것이다 — 돌려 둔 회차가 하나도 없으면 막는다
  const making = readiness?.making && !readiness.runs
  const blocked = notReady || making
  const why = notReady
    ? `세트가 아직 돌 수 있는 상태가 아니다 — ${readiness.steps
        .filter((step) => !step.done)
        .map((step) => step.label)
        .join(' · ')}. \`세트 준비\`에서 마저 채운다.`
    : making
      ? '세트를 만드는 중이다 — `세트 준비`의 마지막 단계까지 마치면 열린다.'
      : undefined

  return (
    <nav className="nav-tabs">
      {features.testset_tools && (
        <NavLink to="/testset" className={CLASS}>
          세트 준비
        </NavLink>
      )}
      {blocked ? (
        <>
          <span className="nav-tab disabled" title={why}>
            성능 테스트
          </span>
          <span className="nav-tab disabled" title={why}>
            결과 비교
          </span>
        </>
      ) : (
        <>
          <NavLink to="/benchmark" className={CLASS}>
            성능 테스트
          </NavLink>
          <NavLink to="/compare" className={CLASS}>
            결과 비교
          </NavLink>
        </>
      )}
      <NavLink to="/tools" className={CLASS}>
        도구
      </NavLink>
      <NavLink to="/chat" className={CLASS}>
        일반 대화
      </NavLink>
    </nav>
  )
}
