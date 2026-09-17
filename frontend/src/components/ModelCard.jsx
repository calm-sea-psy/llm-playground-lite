import { useModels } from '../model-context'
import { formatBytes, formatContext, formatDate } from '../format'

// 선택된 모델의 상세 카드. 데이터는 Context(→ localStorage 캐시)에서 읽고
// 모델을 바꿔도 서버를 다시 호출하지 않는다.
export default function ModelCard() {
  const { selected } = useModels()
  if (!selected) return null

  const rows = [
    ['계열', selected.family],
    ['파라미터', selected.parameter_size],
    ['양자화', selected.quantization],
    ['컨텍스트', formatContext(selected.context_length)],
    ['크기', formatBytes(selected.size)],
    ['수정', formatDate(selected.modified_at)],
    ['라이선스', selected.license],
  ].filter(([, v]) => v != null)

  const caps = selected.capabilities || []

  return (
    <div className="model-card">
      {caps.length > 0 && (
        <div className="model-card-caps">
          {caps.map((c) => (
            <span key={c} className={`cap${c === 'tools' ? ' cap-tools' : ''}`}>
              {c}
            </span>
          ))}
        </div>
      )}
      <dl className="model-card-rows">
        {rows.map(([k, v]) => (
          <div key={k} className="model-card-row">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
