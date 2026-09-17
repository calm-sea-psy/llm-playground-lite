import { useEffect, useState } from 'react'
import { fetchInjectionProbeTool, fetchTools, invokeTool } from '../api'
import NavTabs from '../components/NavTabs'

// 도구 목록 화면. `GET /api/tools`가 모델에 실제로
// 전달하는 정의를 그대로 내려주므로, 화면과 tool-calling 루프가 항상 같은
// 것을 본다 — 화면용 목록을 따로 하드코딩하지 않는다.
const STATUS_LABEL = {
  ready: '사용 가능',
  missing_key: '키 없음',
  disabled: '비활성',
}

function ParamsTable({ parameters }) {
  const props = parameters?.properties || {}
  const required = new Set(parameters?.required || [])
  const names = Object.keys(props)
  if (names.length === 0) return <p className="tool-no-params">인자 없음</p>
  return (
    <table className="tool-params">
      <thead>
        <tr>
          <th>이름</th>
          <th>타입</th>
          <th>필수</th>
          <th>설명</th>
        </tr>
      </thead>
      <tbody>
        {names.map((name) => (
          <tr key={name}>
            <td>
              <code>{name}</code>
            </td>
            <td>{props[name].type}</td>
            <td>{required.has(name) ? 'Y' : '—'}</td>
            <td>{props[name].description || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// 도구 하나를 모델 없이 직접 실행하는 폼. 도구
// 자체가 동작하는지와 모델이 잘 부르는지를 분리해서 확인하려는 목적이라,
// 인자를 폼으로 채워 백엔드가 그 도구만 실행한 원본 응답을 그대로 보여준다.
function InvokeForm({ tool }) {
  const props = tool.parameters?.properties || {}
  const names = Object.keys(props)
  const required = new Set(tool.parameters?.required || [])
  const [values, setValues] = useState({})
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const canRun = names.every((n) => !required.has(n) || String(values[n] ?? '').trim() !== '')

  async function run() {
    setBusy(true)
    setError('')
    setResult(null)
    const args = {}
    for (const n of names) {
      const raw = values[n]
      if (raw == null || raw === '') continue
      args[n] = props[n].type === 'integer' ? Number(raw) : raw
    }
    try {
      setResult(await invokeTool(tool.name, args))
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="tool-invoke">
      {names.length > 0 && (
        <div className="tool-invoke-fields">
          {names.map((n) => (
            <label key={n} className="tool-invoke-field">
              <span>
                {n}
                {required.has(n) ? ' *' : ''}
              </span>
              <input
                type={props[n].type === 'integer' ? 'number' : 'text'}
                value={values[n] ?? ''}
                placeholder={props[n].description}
                onChange={(e) => setValues((v) => ({ ...v, [n]: e.target.value }))}
              />
            </label>
          ))}
        </div>
      )}
      <button className="ghost" onClick={run} disabled={busy || !canRun}>
        {busy ? '실행 중…' : '직접 호출'}
      </button>
      {error && <div className="tool-invoke-error">{error}</div>}
      {result != null && <pre className="tool-invoke-result">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  )
}

function ToolCard({ tool }) {
  const { status } = tool
  return (
    <div className="tool-card">
      <div className="tool-card-head">
        <code className="tool-name">{tool.name}</code>
        <span className={`tool-badge tool-badge-${status}`}>{STATUS_LABEL[status] || status}</span>
        <span className="tool-badge tool-badge-scope">{tool.scope}</span>
        {tool.destructive && <span className="tool-badge tool-badge-destructive">되돌릴 수 없음</span>}
      </div>
      <p className="tool-desc">{tool.description}</p>
      <ParamsTable parameters={tool.parameters} />
      <p className="tool-returns">
        <strong>반환값</strong> {tool.returns}
      </p>
      {tool.requires_key && (
        <p className="tool-dep">
          외부 의존: <code>{tool.requires_key}</code>
          {tool.daily_limit != null && ` (일일 ${tool.daily_limit.toLocaleString()}회 한도)`}
        </p>
      )}
      {tool.destructive ? (
        <p className="tool-invoke-blocked">되돌릴 수 없는 도구라 직접 호출 버튼을 두지 않습니다.</p>
      ) : status === 'ready' ? (
        <InvokeForm tool={tool} />
      ) : (
        <p className="tool-invoke-blocked">
          {status === 'missing_key'
            ? `${tool.requires_key} 환경변수가 없어 이 도구를 쓸 수 없습니다 — 모델에도 등록되지 않습니다.`
            : '이 도구는 지금 비활성 상태입니다.'}
        </p>
      )}
    </div>
  )
}

export default function ToolsPage() {
  const [tools, setTools] = useState([])
  const [probeTool, setProbeTool] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    Promise.all([fetchTools(), fetchInjectionProbeTool()])
      .then(([t, p]) => {
        if (!alive) return
        setTools(t)
        setProbeTool(p)
      })
      .catch((e) => alive && setError(String(e.message || e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>LLM Playground</h1>
        <NavTabs />
        <p className="tools-sidebar-note">
          모델에 실제로 전달되는 도구 정의를 그대로 보여줍니다. 외부 API 키가
          없거나 잘못되면 그 도구는 등록에서 빠집니다 — tool-calling 점수가
          낮게 나온 이유가 모델 탓인지 키 탓인지 여기서 먼저 확인하세요.
        </p>
      </aside>

      <main className="tools-main">
        {loading && <p>불러오는 중…</p>}
        {error && <div className="error">{error}</div>}
        {!loading && !error && (
          <>
            <section>
              <h2>등록된 도구 ({tools.length}개)</h2>
              <div className="tool-grid">
                {tools.map((t) => (
                  <ToolCard key={t.name} tool={t} />
                ))}
              </div>
            </section>

            {probeTool && (
              <section>
                <h2>도구 결과 프롬프트 인젝션 테스트 전용</h2>
                <p className="tools-section-note">
                  정확도 테스트용 일곱 개에는 포함되지 않습니다 — 이 도구가 함께
                  등록되면 테스트마다 모델이 마주하는 도구 구성이 달라져 비교가
                  깨지기 때문입니다.
                </p>
                <div className="tool-grid">
                  <ToolCard tool={probeTool} />
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  )
}
