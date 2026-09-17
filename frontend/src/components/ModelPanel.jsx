import ModelList from './ModelList'
import ModelCard from './ModelCard'

// 라디오 목록 + 선택된 모델 카드를 합친 래퍼(ChatPage처럼 둘을 같은 자리에
// 두는 페이지용). 목록(ModelList)과 카드(ModelCard)를 분리했다 —
// BenchmarkPage는 목록은 사이드바에, 카드는 메인 영역 위쪽에 따로 둔다.
export default function ModelPanel() {
  return (
    <>
      <ModelList />
      <ModelCard />
    </>
  )
}
