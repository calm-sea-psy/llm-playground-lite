import { useModels } from '../model-context'

// 모델 선택 라디오 목록만(카드 없음). 각 항목에 파라미터 크기·양자화를 함께
// 보여줘서 다른 모델과 바로 비교된다. `ModelPanel`은 이 목록 + `ModelCard`를
// 한데 붙인 것이고, BenchmarkPage처럼 카드를 다른 자리(메인 영역
// 위쪽)에 두고 싶은 페이지를 위해 목록만 따로 뺐다.
export default function ModelList() {
  const { models, selectedId, setSelectedId, loading } = useModels()

  return (
    <div className="field">
      <span>모델</span>

      {models.length === 0 ? (
        <div className="model-empty">
          {loading ? '불러오는 중…' : '설치된 모델이 없습니다'}
        </div>
      ) : (
        <div className="model-list" role="radiogroup" aria-label="모델 선택">
          {models.map((m) => (
            <label
              key={m.id}
              className={`model-item${m.id === selectedId ? ' selected' : ''}`}
            >
              <input
                type="radio"
                name="model"
                value={m.id}
                checked={m.id === selectedId}
                onChange={() => setSelectedId(m.id)}
              />
              <span className="model-name">{m.id}</span>
              <span className="model-meta">
                {[m.parameter_size, m.quantization].filter(Boolean).join(' · ')}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
