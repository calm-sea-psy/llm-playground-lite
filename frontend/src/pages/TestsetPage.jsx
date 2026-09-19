import { useEffect, useState } from 'react'
import {
  buildSourceSkeleton,
  deriveTestsets,
  fetchFactCandidates,
  fetchTestsetReadiness,
  removeTestsetDocument,
  saveTestsetSource,
  uploadTestsetDocuments,
} from '../api'
import NavTabs from '../components/NavTabs'
import { DOC_EXAMPLE, FACT_EXAMPLE, GUIDES } from '../testsetExample'
import { blanksByStep, carryOver, cellPreview, factWarning, stepsDone } from '../testsetSource'

// 세트 준비 화면(개발 기능) — **한 번에 한 단계만** 보인다.
// 문서 전체에서 적어도 이만큼은 골라야 한다 — 한 칸의 무게가 1/n이라, 칸이 적으면 문항 하나가 지표를 뒤집는다
const PICK_MIN = 10

// 단계마다 `확인`을 눌러 통과해야 `다음`이 열린다: 앞 단계가 어긋난 채로 뒤를 채우면, 몇 시간짜리 측정을
// 시작한 뒤에야 그 사실을 알게 된다. 되돌아가는 것은 언제든 되고 앞으로 건너뛰는 것만 막는다.
//
// 규칙으로 되는 것(문서 자리·사실 후보·정답 표기·뼈대·검사)은 코드가 하고, 질문 문장·문서에 없는 사실·
// 지어냄을 잡는 정규식·시나리오는 사람이 쓴다 — 문항을 모델이 만들면 재는 대상이 문제를 알게 된다.
// 올릴 때는 **한 벌만** 받는다 — 실행은 문서를 두 자리(짧은·긴)에서 찾지만, 길이를 나눠 재는 실험을 하지
// 않으면 두 자리가 다를 까닭이 없다. 앱이 같은 글을 두 자리에 둔다.
const GROUPS = ['문서', '인젝션 문서']

const STAGES = [
  { id: 'upload', title: '문서 올리기', guide: GUIDES.upload },
  { id: 'pick', title: '사실 고르기', guide: GUIDES.process },
  { id: 'ask', title: '질문과 변형', guide: GUIDES.ask },
  { id: 'forms', title: '정답 표기', guide: GUIDES.forms },
  { id: 'absent', title: '문서에 없는 사실', guide: GUIDES.absent },
  { id: 'scenario', title: '요약 질문과 시나리오', guide: GUIDES.scenario },
  { id: 'derive', title: '세트 파일 만들기', guide: GUIDES.derive },
]

function Lines({ value, onChange, rows = 2 }) {
  return (
    <textarea
      className="testset-lines"
      rows={rows}
      value={(value ?? []).join('\n')}
      onChange={(e) => onChange(e.target.value.split('\n'))}
    />
  )
}

// 굵게(**…**)와 이름(`…`) 표시를 그대로 그린다 — 안내는 읽히라고 있는 것이다
function marked(line) {
  const names = (text, key) =>
    text.split('`').map((part, i) => (i % 2 ? <code key={`${key}.${i}`}>{part}</code> : part))
  return line.split('**').map((part, i) => (i % 2 ? <strong key={i}>{names(part, i)}</strong> : names(part, i)))
}

function Guide({ lines }) {
  // 글은 항목으로, `{ title }`은 소제목, `{ table }`은 작은 표, `{ code }`는 그대로 베껴 쓸 모양 —
  // 안내가 길어지면 어디까지 읽었는지를 잃는다
  const blocks = []
  for (const item of lines) {
    if (typeof item === 'string') {
      if (blocks.at(-1)?.lines) blocks.at(-1).lines.push(item)
      else blocks.push({ lines: [item] })
    } else {
      blocks.push(item)
    }
  }
  return (
    <div className="testset-guide">
      {blocks.map((block, i) => {
        if (block.title) return <h4 key={i}>{block.title}</h4>
        if (block.code) {
          return (
            <pre className="testset-lines" key={i}>
              {block.code}
            </pre>
          )
        }
        if (block.table) {
          return (
            <table className="testset-table" key={i}>
              <thead>
                <tr>
                  {block.table.head.map((cell) => (
                    <th key={cell}>{cell}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.table.rows.map((row) => (
                  <tr key={row[0]}>
                    {row.map((cell) => (
                      <td key={cell}>{marked(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        return (
          <ul key={i}>
            {block.lines.map((line) => (
              <li key={line}>{marked(line)}</li>
            ))}
          </ul>
        )
      })}
    </div>
  )
}

function Example({ lines }) {
  return (
    <p className="testset-quote">
      <strong>예시</strong> — {lines.join(' / ')}
    </p>
  )
}

export default function TestsetPage() {
  const [data, setData] = useState(null)
  const [ready, setReady] = useState(null)
  const [source, setSource] = useState(null)
  const [picked, setPicked] = useState([])
  const [edition, setEdition] = useState(GROUPS[0])
  const [files, setFiles] = useState([])
  const [chosen, setChosen] = useState([]) // 지울 문서 — 체크해서 고른다
  const [stage, setStage] = useState(0)
  const [passed, setPassed] = useState({})
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    // 화면이 사라진 뒤 도착한 응답은 버린다(다른 탭으로 옮겨 간 뒤 상태를 건드리지 않게)
    let alive = true
    Promise.all([fetchFactCandidates(), fetchTestsetReadiness()])
      .then(([facts, readiness]) => {
        if (!alive) return
        setData(facts)
        setReady(readiness)
        if (!facts.source?.facts) return
        setSource(facts.source)
        setPicked(facts.source.facts.map((fact) => fact._from?.id).filter(Boolean))
        const done = stepsDone({ source: facts.source, ready: readiness, minPicks: PICK_MIN,
                                 documents: facts.documents?.length ?? 0, order: STAGES.map((one) => one.id) })
        setPassed(done)
        const open = STAGES.findIndex((one) => !done[one.id]?.ok)
        setStage(open === -1 ? STAGES.length - 1 : open) // 마지막으로 지난 단계의 다음 자리
      })
      .catch((err) => alive && setError(err.message))
    return () => {
      alive = false
    }
  }, [])

  async function run(action, ...args) {
    setError(null)
    try {
      return await action(...args)
    } catch (err) {
      setError(err.message)
      return null
    }
  }

  const current = STAGES[stage]

  function edit(change) {
    setSource((old) => ({ ...old, ...change }))
    setPassed((old) => ({ ...old, [current.id]: undefined })) // 고치면 그 단계는 다시 확인해야 한다
    setResult(null)
  }

  const editFact = (id, change) => edit({ facts: source.facts.map((f) => (f.id === id ? { ...f, ...change } : f)) })
  const editDoc = (path, change) =>
    edit({ documents: source.documents.map((d) => (d.path === path ? { ...d, ...change } : d)) })

  const blanks = source ? blanksByStep(source) : {}

  // 고른 것이 바뀌면 이 단계는 다시 확인해야 한다 — 뼈대가 옛 선택으로 남아 있으면 안 된다
  function pick(id, on) {
    setPicked((old) => (on ? [...old, id] : old.filter((v) => v !== id)))
    setPassed((old) => ({ ...old, pick: undefined }))
  }

  function clearPicked() {
    setPicked([])
    setPassed((old) => ({ ...old, pick: undefined }))
  }
  // 주의가 붙은 줄은 뒤로 — 그대로 쓸 수 있는 줄이 먼저 보여야 한다(무리 안에서는 문서 순서 그대로다)
  const candidates = data ? [...data.candidates].sort((a, b) => Number(!!factWarning(a)) - Number(!!factWarning(b))) : []
  const injectionEditions = data?.documents?.filter((d) => d.editions['인젝션 · 짧은 문서']).map((d) => d.name) ?? []
  const check = passed[current.id]

  // 단계마다의 `확인` — 통과해야 `다음`이 열린다. 서버가 봐야 하는 것(문서·세트)은 서버에 묻는다
  async function verify() {
    setResult(null)
    if (current.id === 'upload') {
      const [facts, readiness] = await Promise.all([run(fetchFactCandidates), run(fetchTestsetReadiness)])
      if (!readiness) return
      if (facts) setData(facts)
      setReady(readiness)
      const none = (facts ?? data)?.documents?.length ? [] : ['문서가 하나도 없다 — 먼저 올린다']
      const problems = [...none, ...readiness.steps[0].problems, ...readiness.steps[1].problems]
      setPassed((old) => ({ ...old, upload: { ok: problems.length === 0, problems } }))
      return
    }
    if (current.id === 'pick') {
      if (picked.length < PICK_MIN) {
        const problems = [`사실 ${picked.length}개를 골랐다 — 적어도 ${PICK_MIN}개를 골라야 한다`]
        setPassed((old) => ({ ...old, pick: { ok: false, problems } }))
        return
      }
      const built = await run(buildSourceSkeleton, picked)
      if (!built) return
      const carried = { ...carryOver(built, source), _step: 'pick' } // 다시 짜도 사람이 써 둔 칸은 남긴다
      setSource(carried)
      await run(saveTestsetSource, carried)
      setPassed((old) => ({ ...old, pick: { ok: true, problems: [] } }))
      return
    }
    if (current.id === 'derive') {
      const derived = await run(deriveTestsets, source, true)
      if (!derived) return
      setResult(derived)
      const errors = derived.checks.filter((c) => c.level === 'error')
      if (errors.length === 0) {
        const marked = { ...source, _step: 'derive' } // 다 지났다는 표시 — 측정 화면의 잠금이 이 값을 본다
        setSource(marked)
        await run(saveTestsetSource, marked)
      }
      const readiness = await run(fetchTestsetReadiness)
      if (readiness) setReady(readiness)
      setPassed((old) => ({
        ...old,
        derive: { ok: errors.length === 0, problems: errors.map((c) => `${c.where} — ${c.message}`) },
      }))
      return
    }
    const problems = [...(blanks[current.id] ?? []), ...(current.id === 'ask' ? (blanks.meta ?? []) : [])]
    // 지난 단계만 파일에 남긴다 — 어디까지 `확인`을 지났는지(`_step`)도 같이 적어 두어야 새로고침 뒤에 그 자리로 돌아온다
    if (problems.length === 0) {
      const marked = { ...source, _step: current.id }
      setSource(marked)
      await run(saveTestsetSource, marked)
    }
    setPassed((old) => ({ ...old, [current.id]: { ok: problems.length === 0, problems } }))
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>LLM Playground</h1>
        <NavTabs readiness={ready} />
        <p className="tools-sidebar-note">
          측정에 쓸 문서와 문항을 만드는 자리입니다. <strong>한 번에 한 단계</strong>만 열리고, 단계마다 `확인`을
          지나야 다음으로 갑니다 — 앞 단계가 어긋난 채로 뒤를 채우면 측정을 시작한 뒤에야 알게 됩니다.
        </p>
        <ol className="testset-steplist">
          {STAGES.map((one, i) => (
            <li key={one.id}>
              {/* 앞 단계로 돌아갔다가 지나온 단계로 곧장 돌아올 수 있어야 한다 — 아직 지나지 않은 단계만 잠근다 */}
              <button
                type="button"
                className={`testset-step${i === stage ? ' active' : ''}${passed[one.id]?.ok ? ' done' : ''}`}
                disabled={i > stage && !passed[one.id]?.ok}
                onClick={() => setStage(i)}
              >
                <span className="testset-step-no">{i + 1}</span>
                {one.title}
                {passed[one.id]?.ok && <span className="testset-step-mark">확인됨</span>}
              </button>
            </li>
          ))}
        </ol>
      </aside>

      <main className="testset-main">
        <h2>
          {stage + 1}단계 · {current.title}
        </h2>
        {error && <p className="testset-error">{error}</p>}
        <Guide lines={current.guide} />

        {current.id === 'upload' && (
          <section>
            <div className="testset-uploadbox">
              <div className="testset-upload">
                <div className="testset-editions stacked">
                  {GROUPS.map((name) => (
                    <label key={name} className="testset-edition">
                      <input
                        type="radio"
                        name="group"
                        value={name}
                        checked={edition === name}
                        onChange={() => setEdition(name)}
                      />
                      {name}
                    </label>
                  ))}
                </div>
                <input
                  type="file"
                  accept=".md,text/markdown"
                  multiple
                  onChange={async (e) => {
                    const chosen = Array.from(e.target.files || [])
                    setFiles(await Promise.all(chosen.map(async (f) => ({ name: f.name, text: await f.text() }))))
                  }}
                />
              </div>
              <div className="testset-columns">
                <div className="testset-column">
                  <h3>올릴 파일 {files.length > 0 ? `(${files.length}개)` : ''}</h3>
                  <div className="testset-column-body">
                    {files.length === 0 ? (
                      <p className="testset-muted">선택된 파일이 없습니다.</p>
                    ) : (
                      <ul className="testset-filelist">
                        {files.map((file) => (
                          <li key={file.name}>
                            <code>{file.name}</code>{' '}
                            <span className="testset-muted">{file.text.length.toLocaleString()}자</span>
                            <button type="button" onClick={() => setFiles(files.filter((f) => f.name !== file.name))}>
                              빼기
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>

                <div className="testset-between">
                  <button
                    type="button"
                    disabled={files.length === 0}
                    onClick={async () => {
                      const saved = await run(uploadTestsetDocuments, edition, files)
                      if (saved) {
                        setFiles([])
                        setData((old) => ({ ...old, ...saved }))
                        setPassed((old) => ({ ...old, upload: undefined }))
                      }
                    }}
                  >
                    {edition}로 올리기{files.length > 0 ? ` (${files.length}개)` : ''} →
                  </button>
                </div>

                <div className="testset-column">
                  <h3>올라간 문서 {data ? `(${data.documents.length}개)` : ''}</h3>

                  <div className="testset-column-body">
                    {data?.documents.length ? (
                      <table className="testset-table">
                      <thead>
                        <tr>
                          <th>
                            <input
                              type="checkbox"
                              checked={chosen.length === data.documents.length && chosen.length > 0}
                              onChange={(e) => setChosen(e.target.checked ? data.documents.map((d) => d.name) : [])}
                              title="전체 선택"
                            />
                          </th>
                          <th>문서</th>
                          <th>묶음</th>
                          <th>글자 수</th>
                          <th>문서 폴더</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.documents.map((doc) => {
                          const injection = doc.editions['인젝션 · 짧은 문서'] || doc.editions['인젝션 · 긴 문서']
                          const both = injection
                            ? doc.editions['인젝션 · 짧은 문서'] && doc.editions['인젝션 · 긴 문서']
                            : doc.editions['짧은 문서'] && doc.editions['긴 문서']
                          const chars = (injection || doc.editions['긴 문서'] || doc.editions['짧은 문서'])?.chars ?? 0
                          return (
                            <tr key={doc.name}>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={chosen.includes(doc.name)}
                                  onChange={(e) =>
                                    setChosen((old) =>
                                      e.target.checked ? [...old, doc.name] : old.filter((v) => v !== doc.name),
                                    )
                                  }
                                />
                              </td>
                              <td>
                                <code>{doc.name}</code>
                              </td>
                              <td>{injection ? '인젝션 문서' : '문서'}</td>
                              <td>{chars.toLocaleString()}자</td>
                              <td className={both ? '' : 'testset-risk'}>{both ? '두 폴더' : '한 폴더뿐'}</td>
                            </tr>
                          )
                        })}
                        </tbody>
                      </table>
                    ) : (
                      <p className="testset-muted">아직 올라간 문서가 없습니다.</p>
                    )}
                  </div>
                  {data?.documents.length > 0 && (
                    <div className="testset-column-foot">
                      <button
                        type="button"
                        disabled={chosen.length === 0}
                        onClick={async () => {
                          // 고른 것을 하나씩 지운다 — 한 자리만 남기지 않으려고 문서 단위로 지운다
                          let left = null
                          for (const name of chosen) {
                            left = (await run(removeTestsetDocument, name)) ?? left
                          }
                          if (left) {
                            setData((old) => ({ ...old, ...left }))
                            setChosen([])
                            setPassed((old) => ({ ...old, upload: undefined }))
                          }
                        }}
                      >
                        문서 삭제{chosen.length > 0 ? ` (${chosen.length}개)` : ''}
                      </button>
                    </div>
                  )}
                </div>
              </div>

            </div>
          </section>
        )}

        {current.id === 'pick' && data && (
          <section>
            {source && (
              <p className="testset-status">
                뼈대를 만들었다 (사실 {source.facts.length}개 · 문서 {source.documents.length}개). 고른 사실을 바꾸고
                확인을 다시 누르면 뼈대를 새로 짠다. 이미 써 둔 질문은 그대로 남는다.
              </p>
            )}
            <div className="testset-upload">
              <strong className={picked.length < PICK_MIN ? 'testset-risk' : ''}>
                선택 {picked.length}/{PICK_MIN}
              </strong>
              <button type="button" className="testset-right" disabled={picked.length === 0} onClick={() => clearPicked()}>
                전체 해제
              </button>
            </div>
            <div className="testset-scroll">
              <table className="testset-table">
                <thead>
                  <tr>
                    <th />
                    <th>문서</th>
                    <th>절</th>
                    <th>값</th>
                    <th>줄</th>
                    <th>주의</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={picked.includes(row.id)}
                          onChange={(e) => pick(row.id, e.target.checked)}
                        />
                      </td>
                      <td>{row.doc.replace('documents/', '').replace('.md', '')}</td>
                      <td>{row.section}</td>
                      <td>
                        <code>{row.value}</code>
                      </td>
                      <td className="testset-line">{row.line}</td>
                      <td className="testset-risk">{factWarning(row) ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {current.id === 'ask' && source && (
          <section>
            {/* 아래 질문 칸과 같은 자리·같은 너비에 둔다 — 같은 종류의 입력이 줄마다 다른 모양이면 눈이 헤맨다 */}
            <div className="testset-card">
              <label>
                주제
                <textarea
                  className="testset-lines"
                  rows={2}
                  value={source.topic ?? ''}
                  onChange={(e) => edit({ topic: e.target.value })}
                />
              </label>
              <label>
                무엇이 만들었나
                <textarea
                  className="testset-lines"
                  rows={2}
                  value={source.generated_by ?? ''}
                  onChange={(e) => edit({ generated_by: e.target.value })}
                  placeholder="후보·기준선 모델이 아니어야 한다"
                />
              </label>
            </div>
            {source.facts.map((fact) => (
              <div key={fact.id} className="testset-card">
                <div className="testset-card-head">
                  <strong>{fact.id}</strong> <code>{fact.doc.replace('documents/', '')}</code> · {fact.label}
                  {factWarning(fact._from) && <span className="testset-risk"> ▲ {factWarning(fact._from)}</span>}
                </div>
                <p className="testset-quote">{fact._from?.line}</p>
                <Lines value={fact.ask} onChange={(ask) => editFact(fact.id, { ask })} />
              </div>
            ))}
          </section>
        )}

        {current.id === 'forms' && source && (
          <section>
            <Example lines={FACT_EXAMPLE.answer_forms} />
            {source.facts.map((fact) => (
              <div key={fact.id} className="testset-card">
                <div className="testset-card-head">
                  <strong>{fact.id}</strong> · {fact.label}
                </div>
                <p className="testset-quote">{fact.ask?.[0]}</p>
                <Lines
                  value={fact.answer_forms}
                  rows={3}
                  onChange={(answer_forms) => editFact(fact.id, { answer_forms })}
                />
              </div>
            ))}
          </section>
        )}

        {current.id === 'absent' && source && (
          <section>
            {source.facts.map((fact) => (
              <div key={fact.id} className="testset-card">
                <div className="testset-card-head">
                  <strong>{fact.id}</strong> · {fact.label}
                </div>
                <label>
                  {/* 이름은 한 덩이로 둔다 — `label`이 세로 flex라 조각이 나뉘면 낱말마다 줄이 바뀐다 */}
                  <span>
                    문서에 <strong>없는</strong> 사실을 묻는 질문 2줄 (비우면 이 문항은 안 만든다)
                  </span>
                  <Lines
                    value={fact.absent?.ask}
                    onChange={(ask) => editFact(fact.id, { absent: { ...fact.absent, ask } })}
                  />
                </label>
                <label>
                  지어냄을 잡는 정규식 (한 줄에 하나)
                  <Lines
                    value={fact.absent?.fabrication_patterns}
                    onChange={(fabrication_patterns) =>
                      editFact(fact.id, { absent: { ...fact.absent, fabrication_patterns } })
                    }
                  />
                </label>
              </div>
            ))}
          </section>
        )}

        {current.id === 'scenario' && source && (
          <section>
            <Example lines={DOC_EXAMPLE.summary_ask} />
            {source.documents.map((doc) => {
              const chosen = typeof doc.injected === 'string' ? [doc.injected] : (doc.injected ?? [])
              return (
                <div key={doc.path} className="testset-card">
                  <div className="testset-card-head">
                    <code>{doc.path.replace('documents/', '')}</code>
                  </div>
                  <label>
                    요약 질문 2줄 (비우면 이 문서는 요약 문항을 안 만든다)
                    <Lines value={doc.summary_ask} onChange={(summary_ask) => editDoc(doc.path, { summary_ask })} />
                  </label>
                  <div>
                    <span className="testset-card-head">지시문을 심은 문서 (여러 개를 걸면 공격 문구가 여럿이 된다)</span>
                    <div className="testset-editions">
                      {injectionEditions.map((name) => {
                        const path = `documents/injection/${name}`
                        return (
                          <label key={name} className="testset-edition">
                            <input
                              type="checkbox"
                              checked={chosen.includes(path)}
                              onChange={(e) =>
                                editDoc(doc.path, {
                                  injected: e.target.checked ? [...chosen, path] : chosen.filter((v) => v !== path),
                                })
                              }
                            />
                            {name}
                          </label>
                        )
                      })}
                    </div>
                  </div>
                  <label>
                    task_keywords (요약을 실제로 했으면 자연히 나올 말 — 비면 늘 실패한다)
                    <Lines
                      value={doc.task_keywords}
                      onChange={(task_keywords) => editDoc(doc.path, { task_keywords })}
                    />
                  </label>
                </div>
              )
            })}
            <div className="testset-card">
              <div className="testset-card-head">긴 컨텍스트 시나리오</div>
              <textarea
                className="testset-lines"
                rows={10}
                value={JSON.stringify(source.scenarios ?? [], null, 2)}
                onChange={(e) => {
                  try {
                    edit({ scenarios: JSON.parse(e.target.value) })
                  } catch {
                    /* 쓰는 중에는 깨진 JSON이 정상이다 — 확인할 때 걸린다 */
                  }
                }}
              />
            </div>
          </section>
        )}

        {current.id === 'derive' && source && (
          <section>
            <p className="testset-muted">
              {cellPreview(source).map((row, i) => (
                <span key={row.label}>
                  {i > 0 && ' · '}
                  {row.label} {row.cells}칸
                  {row.cells > 0 && ` (한 칸 ${(100 / row.cells).toFixed(1)}%p)`}
                  {/* 칸이 모자란 축은 눈에 띄어야 한다 — `칸 수부터 본다`는 안내가 실제로 지켜지려면 여기 보여야 한다 */}
                  {row.least && row.cells < row.least && (
                    <span className="testset-risk"> ▲ 권장선 미달({row.least}칸)</span>
                  )}
                </span>
              ))}
            </p>
            {result && (
              <>
                <p className="testset-muted">
                  {Object.entries(result.cells)
                    .map(([name, n]) => `${name.replace('.json', '')} ${n}칸`)
                    .join(' · ')}
                </p>
                {result.version && (
                  <p className="testset-status">
                    새 판을 냈다 — {result.version} (세트 {result.written.length}개 · 판 {result.versions}개). 측정은 이
                    판을 읽는다.
                  </p>
                )}
                {result.checks.filter((c) => c.level === 'warn').length > 0 && (
                  <ul className="testset-warning">
                    {result.checks
                      .filter((c) => c.level === 'warn')
                      .map((c) => (
                        <li key={`${c.where}${c.message}`}>
                          {c.where} — {c.message}
                        </li>
                      ))}
                  </ul>
                )}
              </>
            )}
            {ready?.steps.map((one) => (
              <p key={one.id} className={one.done ? 'testset-status' : 'testset-warning'}>
                {one.done ? '통과' : '막힘'} — {one.label} ({one.detail})
                {one.problems.slice(0, 5).map((line) => (
                  <span key={line}>{`\n· ${line}`}</span>
                ))}
              </p>
            ))}
            {ready?.ready && <p className="testset-status">준비가 끝났다 — `성능 테스트`에서 측정을 시작할 수 있다.</p>}
          </section>
        )}

        <div className="testset-upload">
          {stage > 0 && (
            <button type="button" onClick={() => setStage(stage - 1)}>
              이전
            </button>
          )}
          {/* 지나간 단계는 다시 누를 일이 없다 — 통과하면 단추가 상태를 말한다 */}
          <button type="button" disabled={check?.ok} onClick={verify}>
            {check?.ok ? '완료' : '확인'}
          </button>
          {stage < STAGES.length - 1 && (
            <button
              type="button"
              className="testset-right"
              disabled={!check?.ok}
              onClick={() => setStage(stage + 1)}
            >
              다음
            </button>
          )}
        </div>
        {check && !check.ok && (
          <ul className="testset-error">
            {check.problems.length ? (
              check.problems.map((line) => <li key={line}>{line}</li>)
            ) : (
              <li>확인하지 못했다</li>
            )}
          </ul>
        )}
      </main>
    </div>
  )
}
