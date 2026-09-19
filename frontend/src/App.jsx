import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ModelProvider } from './ModelProvider'
import ChatPage from './pages/ChatPage'
import BenchmarkPage from './pages/BenchmarkPage'
import ComparePage from './pages/ComparePage'
import ToolsPage from './pages/ToolsPage'
import TestsetPage from './pages/TestsetPage'
import './App.css'

// 일반 대화 / 성능 테스트 / 결과 비교 / 도구 페이지로 나뉜다. 예전의 "병행 비교"
// (실시간 나란히 채팅)는 제거됐다 — 실제로 써보니 마찰이 예상만큼 크지 않았다.
// 지금의 `/compare`는 같은 경로에 새로 만든, 저장된 실행 결과를 읽기 전용으로
// 비교하는 화면이다(예전 것과 이름만 같고 용도는 다르다).
// 선택 모델·상세 캐시는 Provider가 공통으로 제공한다.
export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ModelProvider>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/benchmark" element={<BenchmarkPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/testset" element={<TestsetPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </ModelProvider>
    </BrowserRouter>
  )
}
