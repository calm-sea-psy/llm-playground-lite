import { NavLink } from 'react-router-dom'

// 일반 대화 / 성능 테스트 / 결과 비교 / 도구 페이지 전환. 예전의 "병행 비교"
// (실시간 나란히 채팅) 탭은 제거됐다 — 지금의 "결과 비교"는 같은 자리에 새로
// 붙인, 저장된 실행을 읽기 전용으로 비교하는 화면이다.
export default function NavTabs() {
  return (
    <nav className="nav-tabs">
      <NavLink to="/chat" className={({ isActive }) => `nav-tab${isActive ? ' active' : ''}`}>
        일반 대화
      </NavLink>
      <NavLink
        to="/benchmark"
        className={({ isActive }) => `nav-tab${isActive ? ' active' : ''}`}
      >
        성능 테스트
      </NavLink>
      <NavLink to="/compare" className={({ isActive }) => `nav-tab${isActive ? ' active' : ''}`}>
        결과 비교
      </NavLink>
      <NavLink to="/tools" className={({ isActive }) => `nav-tab${isActive ? ' active' : ''}`}>
        도구
      </NavLink>
    </nav>
  )
}
