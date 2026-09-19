import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useModels } from '../model-context'
import {
  cancelTestRun,
  fetchActiveTest,
  fetchBaseline,
  fetchSystemPrompts,
  fetchTestConfig,
  fetchTestEstimate,
  fetchTestResult,
  fetchTestResults,
  fetchTestRun,
  fetchTestSuites,
  fetchTools,
  rerunTestItems,
  startBaselineRun,
  startTestRun,
} from '../api'
import { diffRunConditions } from '../runDiff'
import { STATUS_LABEL } from '../metrics'
import { RUN_TYPE, RUN_TYPE_LABEL, isPromptExperiment } from '../scoring'
import ModelList from '../components/ModelList'
import ModelCard from '../components/ModelCard'
import NavTabs from '../components/NavTabs'
import RunSummaryPanel from '../components/RunSummaryPanel'
import RunMetricsBody from '../components/RunMetricsBody'
import RunItemList from '../components/RunItemList'
import BaselinePanel from '../components/BaselinePanel'
import SystemPromptPicker from '../components/SystemPromptPicker'
import { promptIdentity } from '../promptIdentity'
import { toolAvailabilityWarning, useFeatures } from '../features'

const POLL_MS = 2000 // SSE보다 단순하고 이 용도엔 2초 해상도로 충분
// 조회가 몇 번 이어서 실패하면 그만둔다. 한 번 실패에 그만두면(예전 동작) 서버가 잠깐 바쁜 사이
// 폴링이 죽어 실행이 끝나도 화면은 계속 `실행 중`이고 딤드 창이 안 걷힌다 — 실제로 그렇게 걸렸다
const POLL_FAILURES_BEFORE_STOP = 5

// 성능 테스트 페이지. 좌측은 모델 하나를 고르는 목록 +
// "기준선" 항목 + 그 모델의 지난 실행 이력이고, 우측은 rightPanelMode에 따라
// 선택된 모델의 작업대("model") 또는 베이스라인 전용 패널("baseline")을 보여준다.
// 여러 결과를 나란히 놓고 비교하는 화면은 D(신규 `/compare`)로 옮겨갔다 — 여기는
// "모델 하나"만 다룬다.
export default function BenchmarkPage() {
  const { selectedId, setSelectedId, error: modelsError } = useModels()
  const [searchParams, setSearchParams] = useSearchParams()
  const [prompts, setPrompts] = useState([])
  // 실행 종류 — 선정용 실행은 시스템 프롬프트를 쓰지 않는다. 프롬프트를 거는 것은 실험 모드다
  const [runType, setRunType] = useState(RUN_TYPE.SELECTION)
  const [promptSelection, setPromptSelection] = useState('')
  const [promptContent, setPromptContent] = useState('')
  const [run, setRun] = useState(null)
  const [results, setResults] = useState([])
  const [detail, setDetail] = useState(null)
  const [baseline, setBaseline] = useState(null) // {run, staleness}
  const [rightPanelMode, setRightPanelMode] = useState('model') // 'model' | 'baseline'
  const [config, setConfig] = useState(null) // bench_config 스냅샷
  const [suites, setSuites] = useState([])
  const [estimate, setEstimate] = useState(null) // 예상 소요 시간
  const [tools, setTools] = useState([]) // 모델에게 주지 못하는 도구가 있으면 실행 전에 경고한다
  const [error, setError] = useState('')
  const features = useFeatures()

  const pollRef = useRef(null)

  const refreshResults = useCallback(() => {
    fetchTestResults()
      .then(setResults)
      .catch((e) => setError(String(e.message || e)))
  }, [])

  const refreshBaseline = useCallback(() => {
    fetchBaseline()
      .then(setBaseline)
      .catch((e) => setError(String(e.message || e)))
  }, [])

  const refreshEstimate = useCallback((model) => {
    if (!model) return
    fetchTestEstimate(model, 'full')
      .then(setEstimate)
      .catch(() => setEstimate(null)) // 예상 시간은 참고용이라 실패해도 화면을 막지 않는다
  }, [])

  const pollRun = useCallback(
    (runId) => {
      clearInterval(pollRef.current)
      let failures = 0
      const tick = async () => {
        try {
          const updated = await fetchTestRun(runId)
          failures = 0
          setRun(updated)
          if (updated.status !== 'running' && updated.status !== 'cancelling') {
            clearInterval(pollRef.current)
            if (updated.scope === 'baseline') {
              refreshBaseline()
            } else {
              refreshResults()
              refreshEstimate(updated.model)
              // 지표 재실행이 끝나면 부모 실행의 합친 뷰를 다시 받아 출처가 붙은 값을 보여준다
              if (updated.kind === 'metric_rerun') {
                fetchTestResult(updated.parent_run_id)
                  .then(setDetail)
                  .catch((e) => setError(String(e.message || e)))
              }
            }
          }
        } catch (e) {
          // 한 번 걸러도 계속 묻는다 — 측정 중에는 서버가 바빠 조회 하나가 늦거나 끊길 수 있다
          failures += 1
          if (failures >= POLL_FAILURES_BEFORE_STOP) {
            clearInterval(pollRef.current)
            setError(String(e.message || e))
          }
        }
      }
      tick() // 2초를 기다리지 않고 상태를 바로 한 번 받아온다
      pollRef.current = setInterval(tick, POLL_MS)
    },
    [refreshResults, refreshBaseline, refreshEstimate],
  )

  // 첫 로드: 프롬프트 목록, 과거 결과, 베이스라인, 측정 조건·항목(공용
  // 데이터 — 모델이 바뀌어도 안 바뀌므로 한 번만), 그리고 다른 화면(또는 새로고침
  // 전)에서 시작해둔 실행이 아직 돌고 있으면 그것도 이어서 구독한다.
  const refreshPrompts = useCallback(
    () =>
      fetchSystemPrompts()
        .then(setPrompts)
        .catch((e) => setError(String(e.message || e))),
    [],
  )

  useEffect(() => {
    refreshPrompts()
    fetchTestConfig()
      .then(setConfig)
      .catch(() => {})
    fetchTestSuites()
      .then(setSuites)
      .catch(() => {})
    fetchTools()
      .then(setTools)
      .catch(() => {})
    refreshResults()
    refreshBaseline()
    fetchActiveTest()
      .then((r) => {
        if (r.run_id) pollRun(r.run_id)
      })
      .catch(() => {})
    return () => clearInterval(pollRef.current)
  }, [refreshResults, refreshBaseline, refreshPrompts, pollRun])

  // 화면으로 돌아왔을 때 한 번 맞춘다 — 폴링이 죽었거나(탭을 오래 비웠거나 조회가 끊겼거나) 다른 화면에서
  // 실행이 끝났을 수 있다. 이 동기화가 없으면 실행은 끝났는데 딤드 창이 걷히지 않고, 사람은 멈춘 줄 안다.
  // (`busy`는 아래에서 만든다 — 여기서 쓰면 선언 전 참조라 같은 판단을 이 자리에서 다시 한다)
  const running = run && (run.status === 'running' || run.status === 'cancelling')
  useEffect(() => {
    if (!running) return undefined
    const resync = () => {
      if (document.visibilityState === 'hidden') return
      fetchActiveTest()
        .then((active) => {
          if (active.run_id && active.run_id !== run.id) {
            pollRun(active.run_id) // 다른 실행이 돌고 있다 — 그쪽으로 옮겨 붙는다
            return null
          }
          if (active.run_id) return null // 폴링이 살아 있으면 그쪽이 곧 갱신한다
          return fetchTestRun(run.id).then((updated) => {
            setRun(updated) // 끝난 실행 — 마지막 상태로 맞춰 딤드 창을 걷는다
            refreshResults()
          })
        })
        .catch(() => {}) // 되맞추기 실패는 화면을 막지 않는다 — 폴링이 계속 시도한다
    }
    document.addEventListener('visibilitychange', resync)
    window.addEventListener('focus', resync)
    return () => {
      document.removeEventListener('visibilitychange', resync)
      window.removeEventListener('focus', resync)
    }
  }, [running, run?.id, pollRun, refreshResults])

  // 모델을 바꾸면 우측 전체가 그 모델의 작업대로 바뀐다 — 다른 모델의
  // 상세를 계속 보여주거나 베이스라인 패널에 머물러 있으면 안 된다. 렌더 도중
  // state를 조정하는 패턴(effect 안에서 setState하지 않는다 — React 문서의
  // "prop이 바뀔 때 state를 조정" 권장 방식)을 쓴다. detail은 무조건 지우지
  // 않고 "지금 모델의 것이 아니면" 지운다 — D에서 넘어온 딥링크(아래 효과)가
  // selectedId·detail을 같은 모델로 함께 맞춰 두면 여기서 다시 지우면 안 된다.
  const [modelForPanel, setModelForPanel] = useState(selectedId)
  if (selectedId !== modelForPanel) {
    setModelForPanel(selectedId)
    setRightPanelMode('model')
    if (detail && detail.model !== selectedId) setDetail(null)
  }

  // 예상 소요 시간 조회는 네트워크 호출(외부 시스템과의 동기화)이라 effect가 맞다.
  useEffect(() => {
    refreshEstimate(selectedId)
  }, [selectedId, refreshEstimate])

  // D(/compare)의 "상세 보기"가 `?resultId=`로 넘어온다 — 그 실행의 모델로
  // 전환하고 상세를 바로 띄운다(같은 tick에 selectedId·detail을 함께 맞춰야
  // 위 render-time 조정에서 지워지지 않는다).
  useEffect(() => {
    const resultId = searchParams.get('resultId')
    if (!resultId) return
    fetchTestResult(resultId)
      .then((d) => {
        setSelectedId(d.model)
        setDetail(d)
        setRightPanelMode('model')
        setSearchParams({}, { replace: true })
      })
      .catch((e) => setError(String(e.message || e)))
  }, [searchParams, setSearchParams, setSelectedId])

  // 프롬프트 실험은 프롬프트를 거는 것이 목적이다 — 고른 것이 없으면 첫 프롬프트로 시작한다
  function chooseRunType(next) {
    setRunType(next)
    if (next === RUN_TYPE.EXPERIMENT && !promptSelection && prompts.length > 0) {
      setPromptSelection(`saved:${prompts[0].name}`)
      setPromptContent(prompts[0].content ?? '')
    }
  }

  async function handleStart() {
    setError('')
    setDetail(null)
    try {
      const experiment = runType === RUN_TYPE.EXPERIMENT
      if (experiment && !promptId.name) {
        // 백엔드가 이름으로 파일을 읽어 스냅숏한다 — 저장되지 않은(또는 고친) 본문으로는 실험을 시작하지 않는다
        throw new Error('프롬프트 실험은 저장된 시스템 프롬프트로만 시작합니다 — 고른 뒤 수정했다면 먼저 저장하세요')
      }
      const started = await startTestRun({
        model: selectedId,
        runType,
        systemPromptName: experiment ? promptId.name : null,
      })
      setRun(started)
      pollRun(started.id)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  async function handleStartBaseline() {
    setError('')
    try {
      const started = await startBaselineRun()
      setRun(started)
      pollRun(started.id)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  async function handleRerun(itemId, reason) {
    if (!detail) return
    setError('')
    try {
      const started = await rerunTestItems(detail.id, [itemId], reason)
      setRun(started)
      pollRun(started.id)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  async function handleCancel() {
    if (!run) return
    try {
      await cancelTestRun(run.id)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  async function viewResult(id) {
    setError('')
    try {
      const d = await fetchTestResult(id)
      setDetail(d)
      setRightPanelMode('model')
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  const busy = running
  const modelRunActive = busy && run.scope !== 'baseline'
  const baselineRunActive = busy && run.scope === 'baseline'

  const modelHistory = useMemo(
    () => results.filter((r) => r.model === selectedId),
    [results, selectedId],
  )

  const promptId = promptIdentity(prompts, promptSelection, promptContent)
  const selectedPrompt = runType === RUN_TYPE.EXPERIMENT ? promptId.saved : null
  const promptSha = selectedPrompt && !promptId.dirty ? selectedPrompt.sha256 : null

  const diff = useMemo(() => {
    const lastForModel = modelHistory[0]
    if (!lastForModel) return null
    return diffRunConditions(lastForModel, {
      run_type: runType,
      system_prompt_meta: promptSha ? { sha256: promptSha } : null,
      config,
    })
  }, [modelHistory, runType, promptSha, config])

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>LLM Playground</h1>
        <NavTabs />

        <ModelList />

        <div className="bench-baseline-entry">
          <button
            type="button"
            className={`bench-baseline-btn${rightPanelMode === 'baseline' ? ' selected' : ''}`}
            onClick={() => setRightPanelMode('baseline')}
          >
            기준선
            {baseline?.staleness?.stale && <span className="baseline-stale-dot" title="낡음" />}
          </button>
        </div>

        <div className="bench-history field">
          <span>지난 실행 이력</span>
          {modelHistory.length === 0 ? (
            <div className="model-empty">아직 이 모델을 실행한 적 없음</div>
          ) : (
            <ul className="result-list">
              {modelHistory.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={`result-select${detail?.id === r.id ? ' selected' : ''}`}
                    onClick={() => viewResult(r.id)}
                  >
                    <span className="result-meta">
                      {STATUS_LABEL[r.status] ?? r.status} · {new Date(r.started_at).toLocaleString()}
                      {isPromptExperiment(r) && <span className="run-type-badge">{RUN_TYPE_LABEL.prompt_experiment}</span>}
                      {r.run_type === RUN_TYPE.ASSIGNMENT && <span className="run-type-badge">{RUN_TYPE_LABEL.assignment}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <main className="bench-main">
        {(modelsError || error) && <div className="error">{modelsError || error}</div>}

        {rightPanelMode === 'baseline' ? (
          <BaselinePanel
            baseline={baseline}
            busy={Boolean(busy)}
            activeRun={baselineRunActive ? run : null}
            onMeasure={handleStartBaseline}
          />
        ) : (
          <>
            {/* 무엇을 어떤 종류로 재는지는 아래를 훑는 동안에도 보여야 한다 — 이 두 칸만 위에 붙여 둔다 */}
            <div className="bench-pinned">
            <ModelCard />

            {/* 예전 시스템 프롬프트 칸 자리 — 실행 종류를 먼저 고르고, 실험일 때만 프롬프트가 열린다.
                프롬프트 실험이 없는 서버에서는 선정만 있으니 고를 것이 없다 */}
            {features.prompt_experiment && (
            <fieldset className="run-type-control" disabled={Boolean(modelRunActive)}>
              <legend>실행 종류</legend>
              {[RUN_TYPE.SELECTION, RUN_TYPE.EXPERIMENT].map((t) => (
                <label key={t} className="field-inline">
                  <input type="radio" name="run-type" value={t} checked={runType === t} onChange={() => chooseRunType(t)} />
                  <span>{RUN_TYPE_LABEL[t]}</span>
                </label>
              ))}
              <p className="run-type-note">
                {runType === RUN_TYPE.SELECTION
                  ? '모델 선정용 — 시스템 프롬프트 없이 모델 자체를 잽니다.'
                  : '프롬프트 효과를 봅니다 — 모든 항목(속도 탐침 포함)에 걸고, 결과는 모델 선정 비교군에서 기본으로 빠집니다.'}
              </p>
            </fieldset>
            )}
            </div>

            {runType === RUN_TYPE.EXPERIMENT && (
              <SystemPromptPicker
                prompts={prompts}
                selection={promptSelection}
                content={promptContent}
                allowNone={false}
                disabled={Boolean(modelRunActive)}
                onPromptsChanged={refreshPrompts}
                onChange={({ selection, content }) => {
                  setPromptSelection(selection)
                  setPromptContent(content)
                }}
              />
            )}

            <RunSummaryPanel
              runType={runType}
              prompt={selectedPrompt}
              promptDirty={promptId.dirty}
              config={config}
              suites={suites}
              estimate={estimate}
              toolWarning={toolAvailabilityWarning(tools, { fixed: config?.tool_responses === 'fixed' })}
              diff={diff}
              lastRun={modelHistory[0]}
            />

            <button onClick={handleStart} disabled={!selectedId || Boolean(busy)}>
              실행{estimate?.total_sec != null ? ` (약 ${Math.max(1, Math.round(estimate.total_sec / 60))}분)` : ''}
            </button>
            {baselineRunActive && <p className="bench-busy-note">베이스라인 실행 중이라 잠시 기다려야 합니다.</p>}

            {modelRunActive && (
              <section className="run-progress">
                <h2>{STATUS_LABEL[run.status] ?? run.status}</h2>
                <p>
                  {run.completed} / {run.total}
                  {run.current_item && ` — 진행 중: ${run.current_item}`}
                </p>
                <RunItemList items={run.items} />
                <RunMetricsBody run={run} />
              </section>
            )}

            {detail && (
              <section className="run-detail">
                <h2>실행 상세 — {detail.model}</h2>
                <p>
                  상태: {STATUS_LABEL[detail.status] ?? detail.status} ·{' '}
                  {new Date(detail.started_at).toLocaleString()}
                </p>
                {isPromptExperiment(detail) ? (
                  <details className="run-detail-prompt">
                    <summary>
                      {RUN_TYPE_LABEL.prompt_experiment} —{' '}
                      {detail.system_prompt_meta?.title ?? detail.system_prompt_meta?.name ?? '프롬프트'} (
                      {detail.system_prompt_meta?.chars ?? detail.system_prompt?.length ?? 0}자)
                    </summary>
                    <pre className="prompt-body">{detail.system_prompt}</pre>
                  </details>
                ) : (
                  <p>실행 종류: {RUN_TYPE_LABEL.selection} (시스템 프롬프트 없음)</p>
                )}
                {detail.mixed && (
                  <p className="run-mixed-note">
                    혼합 실행 — 일부 지표는 나중에 다시 잰 값입니다(항목 옆에 재실행 시각 표시).
                  </p>
                )}
                <RunItemList
                  items={detail.items}
                  provenance={detail.provenance}
                  parentRunId={detail.id}
                  onRerun={features.rerun ? handleRerun : undefined}
                  rerunDisabled={Boolean(busy)}
                />
                <RunMetricsBody run={detail} baselineRun={baseline?.run ?? undefined} />
              </section>
            )}
          </>
        )}
      </main>

      {modelRunActive && (
        <div className="run-overlay" role="dialog" aria-modal="true" aria-label="측정 진행 중">
          <div className="run-overlay-panel">
            <h2>{STATUS_LABEL[run.status] ?? run.status}</h2>
            <p>
              {run.completed} / {run.total}
              {run.current_item && ` — 진행 중: ${run.current_item}`}
            </p>
            <button className="stop" onClick={handleCancel} disabled={run.status === 'cancelling'}>
              {run.status === 'cancelling' ? '중단 처리 중…' : '중단하기'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
