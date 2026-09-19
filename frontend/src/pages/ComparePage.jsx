import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  fetchActiveTest,
  fetchBaseline,
  fetchCompareAliases,
  fetchCompareNotesStatus,
  fetchConsistencyDemotion,
  fetchTestResult,
  fetchRescorePlan,
  fetchTestResults,
  generateOneCompareNote,
  rescoreRuns,
} from '../api'
import { fileLabel, metricLine, rescoreNotices, rescoreSummary, runFilesText } from '../rescore'
import { conditionMismatchLines } from '../runDiff'
import {
  BASELINE_ROW_ID,
  comparableRuns,
  gatedWeights,
  formatRawCell,
  blockedByPrompt,
  isPromptExperiment,
  latestSelectionRun,
  normalize,
  PRESETS,
  RUN_TYPE_LABEL,
  toRow,
  weightedScore,
} from '../scoring'
import { buildReportPayload } from '../report'
import { exportKey, viewAction } from '../reportView'
import { baselineLengthKey, mixedLengthsReason } from '../baselineChoice'
import { STATUS_LABEL, METRICS, QUALITY_METRICS, TOOL_CALLING_METRICS } from '../metrics'
import NavTabs from '../components/NavTabs'
import ReportViewer from '../components/ReportViewer'
import CompareRadar from '../components/CompareRadar'
import CompareNotes from '../components/CompareNotes'
import ConsistencyJudgmentPanel from '../components/ConsistencyJudgmentPanel'
import { useFeatures } from '../features'

// 비교 노트에 넘길 표 텍스트 — 화면에 이미 그리는 compare-table과
// 정확히 같은 데이터(같은 get()/fmt()/inScope)로 마크다운 표를 만든다. 이
// 텍스트가 곧 모델에게 주는 "비교 표 전체"이자 캐시 키의 지문 재료다.
// 머리글은 **별칭**, 지표 이름에는 **방향(↑/↓)**을 붙인다 — 전체 이름이 노트에 나오면
// 리포트 본문과 다른 이름이 되고, 방향이 없으면 모델이 오탐률 100%를 좋은 값으로 읽는다.
function buildComparisonTableText(metricDefs, details, baseline, aliases) {
  const headers = ['지표', ...details.map((d) => aliases?.[d.id] ?? d.model)]
  if (baseline?.run) headers.push(`기준선 (${baseline.run.model})`)
  const lines = [`| ${headers.join(' | ')} |`, `|${headers.map(() => '---').join('|')}|`]
  for (const def of metricDefs) {
    // 표 칸은 화면 표와 같은 formatRawCell — 능력 부재·실행 실패·비교 제외가 노트 모델에게도 그대로 간다
    const cells = [`${def.label} ${def.direction === 'lower' ? '↓' : '↑'}`, ...details.map((d) => formatRawCell(d, def))]
    if (baseline?.run) cells.push(formatRawCell(baseline.run, def, { baseline: true, inScope: def.inScope }))
    lines.push(`| ${cells.join(' | ')} |`)
  }
  return lines.join('\n')
}

// 비교 표(화면·노트용 표 텍스트)가 쓰는 원본 표시 정의 — 기준선이 재는 범위는 품질·보안뿐이다.
const ALL_METRIC_DEFS = [
  ...METRICS.map((m) => ({ ...m, inScope: false })),
  ...QUALITY_METRICS.map((m) => ({ ...m, inScope: m.baselineInScope ?? true })),
  ...TOOL_CALLING_METRICS.map((m) => ({ ...m, inScope: false })),
]

/** 이 실행 조합에서 일관성을 종합 순위에서 뺄지 — 판정 기록으로 조합마다 따로 센다(비교에 든 실행의 칸만 근거다).
 * 불러오는 동안은 null이고, 실패하면 `error` 상태로 두어 화면이 일관성이 들어간 점수임을 알린다. */
function useConsistencyDemotion(runIds, refreshToken) {
  const key = runIds.join(',')
  const [loaded, setLoaded] = useState({ key: '', refreshToken: 0, demotion: null })
  useEffect(() => {
    if (!key) return
    let cancelled = false
    fetchConsistencyDemotion(key.split(','))
      .then((demotion) => {
        if (!cancelled) setLoaded({ key, refreshToken, demotion })
      })
      .catch(() => {
        if (!cancelled) setLoaded({ key, refreshToken, demotion: { status: 'error' } })
      })
    return () => {
      cancelled = true
    }
  }, [key, refreshToken])
  // 판정을 고친 직후에는 새 값이 올 때까지 이전 값을 그대로 보인다 — 조합이 바뀌었을 때만 비운다
  return loaded.key === key ? loaded.demotion : null
}

// 결과 비교 페이지 — 저장된 실행을
// 모델별로 묶어 보여준다. 비교의 단위는 실행이 아니라 모델이라, 평평한
// 목록 대신 접었다 펼 수 있는 모델 카드로 나열하고 체크박스는 그 모델의
// 최신 실행만 선택한다(펼치면 다른 실행도 따로 고를 수 있다). 실행 자체는
// 여기서 하지 않는다(읽기 전용 — 동시 실행 제어는 /benchmark 하나로 모아둔다).
export default function ComparePage() {
  const navigate = useNavigate()
  const features = useFeatures()
  const [results, setResults] = useState([])
  const [selectedIds, setSelectedIds] = useState([])
  const [expandedModels, setExpandedModels] = useState(() => new Set())
  const [detailsById, setDetailsById] = useState({})
  const [error, setError] = useState('')
  // 기준선은 **후보와 같은 문서 길이**로 불러온다 — 가장 최신을 쓰면 다른 판으로 잰 기준선이 조용히 열을 차지한다.
  // 후보의 판이 섞였거나 맞는 기준선이 없으면 열을 비우고 까닭(`unmatched`)을 적는다
  const [baselineFor, setBaselineFor] = useState({ key: undefined, value: null })
  const baselineCandidates = selectedIds.map((id) => detailsById[id]).filter(Boolean)
  const baselineKey = baselineLengthKey(baselineCandidates)
  const mixedReason = baselineKey === null ? mixedLengthsReason(baselineCandidates) : null
  const baseline = useMemo(() => {
    if (mixedReason) return { run: null, staleness: null, unmatched: mixedReason }
    return baselineFor.key === baselineKey ? baselineFor.value : null
  }, [mixedReason, baselineFor, baselineKey])
  useEffect(() => {
    if (baselineKey === null) return
    let cancelled = false
    fetchBaseline(baselineKey || null)
      .then((value) => {
        if (!cancelled) setBaselineFor({ key: baselineKey, value })
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e))
      })
    return () => {
      cancelled = true
    }
  }, [baselineKey])
  // 리포트가 "지금 보던 가중치"를 그대로 담게. 프리셋 이름까지 함께
  // 들고 있는 이유는 표지에 "적용 가중치: 실사용 중심"처럼 이름으로 적어야
  // 하는데, 숫자만 보고는 프리셋인지 슬라이더로 만든 값인지 알 수 없어서다.
  const [weighting, setWeighting] = useState(() => ({ presetName: 'usage', weights: PRESETS.usage.build() }))
  const [reportBusy, setReportBusy] = useState(false)
  // 리포트 내보내기의 노트 생성 확인 패널과 진행 단계(숨은 부작용이 되어서는 안 된다)
  const [exportPlan, setExportPlan] = useState(null) // {status, tableText}
  const [exportStage, setExportStage] = useState('')
  // 리포트 확인하기 — 마지막으로 내보낸 파일과 그 조건. 보는 것은 언제나 내보낸 파일 그 자체다
  const [lastExport, setLastExport] = useState(null)
  const [viewerOpen, setViewerOpen] = useState(false)
  const viewAfterExport = useRef(false)
  const [notesRefresh, setNotesRefresh] = useState(0)

  useEffect(() => {
    fetchTestResults()
      .then(setResults)
      .catch((e) => setError(String(e.message || e)))
  }, [])

  // 백엔드가 이미 started_at 내림차순으로 주므로(list_results()), 순서를
  // 보존한 채 모델별로 묶기만 하면 그룹도 각 그룹의 실행 목록도 자동으로
  // 최신순이 된다 — 추가 정렬이 필요 없다.
  const modelGroups = useMemo(() => {
    const order = []
    const byModel = new Map()
    for (const r of comparableRuns(results)) {
      if (!byModel.has(r.model)) {
        byModel.set(r.model, [])
        order.push(r.model)
      }
      byModel.get(r.model).push(r)
    }
    return order.map((model) => ({ model, runs: byModel.get(model) }))
  }, [results])

  // 접힌 상태에서도 종합 점수·기준선 대비를 보여주려면 모델별 최신 실행의
  // 전체 상세가 미리 있어야 한다. 이미 요청한 id는 requestedRef로 추적해
  // 중복 fetch를 막는다(ModelProvider.jsx의 마운트 fetch와 같은 패턴 —
  // effect 안에서 detailsById를 읽지 않아 exhaustive-deps 경고가 없다).
  const requestedRef = useRef(new Set())
  useEffect(() => {
    const toFetch = modelGroups
      .map((g) => latestSelectionRun(g.runs)?.id)
      .filter((id) => id && !requestedRef.current.has(id))
    toFetch.forEach((id) => requestedRef.current.add(id))
    if (toFetch.length === 0) return
    Promise.all(toFetch.map((id) => fetchTestResult(id).then((d) => [id, d])))
      .then((pairs) => setDetailsById((m) => ({ ...m, ...Object.fromEntries(pairs) })))
      .catch((e) => setError(String(e.message || e)))
  }, [modelGroups])

  // 미리보기 정규화 풀 = 모델별 최신 실행 + 베이스라인(사용자 확정). 아래
  // 상세 비교 섹션(체크한 것들끼리 비교하는 CompareRadar)과는 다른 질문에
  // 답하는 별개의 정규화라 섞지 않는다 — 가중치는 usage 프리셋으로 고정해
  // 체크 여부와 무관하게 항상 같은 값이 나오게 한다.
  const previewRows = useMemo(() => {
    const rows = modelGroups
      .map((g) => detailsById[latestSelectionRun(g.runs)?.id])
      .filter(Boolean)
      .map((d) => toRow(d))
    if (baseline?.run) rows.push(toRow(baseline.run, BASELINE_ROW_ID))
    return rows
  }, [modelGroups, detailsById, baseline])
  const previewNormalized = useMemo(() => normalize(previewRows), [previewRows])
  const [judgmentRefresh, setJudgmentRefresh] = useState(0)
  const onJudgmentSaved = useCallback(() => setJudgmentRefresh((n) => n + 1), [])
  const previewRunIds = useMemo(
    () => modelGroups.map((g) => latestSelectionRun(g.runs)?.id).filter(Boolean),
    [modelGroups],
  )
  const previewDemotion = useConsistencyDemotion(previewRunIds, judgmentRefresh)
  const previewWeights = useMemo(() => gatedWeights(PRESETS.usage.build(), previewDemotion), [previewDemotion])
  const baselinePreviewScore = baseline?.run ? weightedScore(previewNormalized, previewWeights, BASELINE_ROW_ID) : null

  function previewScoreFor(runId) {
    return weightedScore(previewNormalized, previewWeights, runId)
  }

  function toggleModelLatest(model, latestId) {
    setSelectedIds((prev) => (prev.includes(latestId) ? prev.filter((x) => x !== latestId) : [...prev, latestId]))
    if (!detailsById[latestId]) {
      fetchTestResult(latestId)
        .then((d) => setDetailsById((m) => ({ ...m, [latestId]: d })))
        .catch((e) => setError(String(e.message || e)))
    }
  }

  function toggleRun(id) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
    if (!detailsById[id]) {
      fetchTestResult(id)
        .then((d) => setDetailsById((m) => ({ ...m, [id]: d })))
        .catch((e) => setError(String(e.message || e)))
    }
  }

  function toggleExpand(model) {
    setExpandedModels((prev) => {
      const next = new Set(prev)
      if (next.has(model)) next.delete(model)
      else next.add(model)
      return next
    })
  }

  // 모델별 최신 실행이 전부 골라져 있으면 같은 버튼이 선택을 모두 푼다(따로 고른 실행까지)
  const allLatestSelected = previewRunIds.length > 0 && previewRunIds.every((id) => selectedIds.includes(id))

  function toggleAllLatest() {
    setSelectedIds(allLatestSelected ? [] : previewRunIds)
  }

  // 프롬프트가 다른 실험 회차끼리는 함께 고르지 못하게 막는다 — 섞이면 무엇의 점수인지 알 수 없다
  const selectedRuns = useMemo(
    () => results.filter((r) => selectedIds.includes(r.id)),
    [results, selectedIds],
  )

  const selectedDetails = selectedIds.map((id) => detailsById[id]).filter(Boolean)
  const demotion = useConsistencyDemotion(selectedIds, judgmentRefresh)

  // 별칭은 선택 조합이 정한다(겹침 해소가 같이 고른 모델에 따라 달라진다) — 백엔드 규칙 하나를 부른다
  const [aliases, setAliases] = useState({})
  const selectionKey = selectedIds.join(',')
  useEffect(() => {
    const ids = selectionKey ? selectionKey.split(',') : []
    if (ids.length === 0) return
    let cancelled = false
    fetchCompareAliases(ids)
      .then((a) => {
        if (!cancelled) setAliases(a)
      })
      .catch((e) => setError(String(e.message || e)))
    return () => {
      cancelled = true
    }
  }, [selectionKey])
  // 재채점 미리보기 — 고른 실행이 표시하는 값의 출처 파일마다 바뀌는 채점기 버전(파일을 고치지 않는다)
  const [rescore, setRescore] = useState({ key: '', plan: null })
  const [rescoreOpen, setRescoreOpen] = useState(false)
  const [rescoreBusy, setRescoreBusy] = useState(false)
  const [rescoreStage, setRescoreStage] = useState('')
  // 창으로 돌아올 때도 다시 읽는다 — CLI로 재채점하고 돌아와도 선택을 안 바꾸면 화면과 서버를 대조할 기회가 없다
  const [rescoreTick, setRescoreTick] = useState(0)
  useEffect(() => {
    const onFocus = () => setRescoreTick((n) => n + 1)
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])
  useEffect(() => {
    const ids = selectionKey ? selectionKey.split(',') : []
    if (ids.length === 0) return
    let cancelled = false
    fetchRescorePlan(ids)
      .then((plan) => {
        if (!cancelled) setRescore({ key: selectionKey, plan })
      })
      .catch((e) => setError(String(e.message || e)))
    return () => {
      cancelled = true
    }
  }, [selectionKey, rescoreTick])
  const rescorePlan = rescore.key === selectionKey ? rescore.plan : null
  const rescoreCounts = rescoreSummary(rescorePlan)
  // 리포트 표지와 같은 이름(별칭)으로 적는다
  // 서버가 읽은 실행·기준선을 화면이 들고 있는 상세·기준선과 대조한다 — 다르면 새로고침하라는 줄로 바뀐다
  const rescoreLines = useMemo(
    () =>
      rescoreNotices(rescorePlan, (run) => aliases[run.run_id] ?? run.model, { runs: detailsById, baseline }, {
        cli: features.maintenance_cli,
      }),
    [rescorePlan, aliases, detailsById, baseline, features.maintenance_cli],
  )

  async function confirmRescore() {
    setRescoreBusy(true)
    setError('')
    try {
      const ids = [...selectedIds]
      const out = await rescoreRuns(ids)
      // 점수가 바뀌었으니 고른 실행의 상세를 다시 읽는다 — 노트 입력 표·판정 게이트도 새 값으로 다시 계산된다
      const fresh = await Promise.all(ids.map((id) => fetchTestResult(id).then((d) => [id, d])))
      setDetailsById((m) => ({ ...m, ...Object.fromEntries(fresh) }))
      setRescore({ key: ids.join(','), plan: out.plan })
      setRescoreOpen(false)
      const changed = out.rescored.reduce((n, r) => n + r.changed.length, 0)
      setRescoreStage(
        out.rescored.length
          ? `재채점했습니다 — 파일 ${out.rescored.length}개 (버전이 바뀐 지표 ${changed}개)`
          : '재채점할 파일이 없었습니다 — 미리보기 뒤로 상태가 바뀌었습니다',
      )
      setNotesRefresh((n) => n + 1)
      setJudgmentRefresh((n) => n + 1)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setRescoreBusy(false)
    }
  }

  // 노트 입력 표는 별칭이 이 조합의 것으로 다 준비된 뒤에만 만든다 — 도중에 만들면 캐시 키가 흔들린다
  const aliasesReady = selectedIds.length > 0 && selectedIds.every((id) => aliases[id])
  const tableText =
    aliasesReady && selectedDetails.length === selectedIds.length
      ? buildComparisonTableText(ALL_METRIC_DEFS, selectedDetails, baseline, aliases)
      : null

  const currentExportKey = exportKey({ selectedIds, weighting, judgmentRefresh })

  async function renderReport(notesData) {
    const key = currentExportKey // payload를 만든 조건 — 보는 파일이 지금 화면과 같은 조건인지 가르는 열쇠
    setExportStage('리포트 렌더링 중')
    const payload = buildReportPayload({
      selectedDetails,
      baseline,
      weights: weighting.weights,
      presetName: weighting.presetName,
      demotion,
      notes: notesData && Object.keys(notesData.notes ?? {}).length ? notesData : null,
    })
    const res = await fetch('/api/compare/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(body?.detail || `리포트 생성 실패 (HTTP ${res.status})`)
    }
    // 브라우저로 내려받지 않는다 — 백엔드가 저장소의 report/ 폴더에 저장하고 위치를 돌려준다.
    // 응답 전문은 판정하는 사람의 작업 자료라 따로 나온다 — 없으면 그 까닭이 함께 온다
    return { ...(await res.json()), key }
  }

  function finishExport(saved, skipped = []) {
    setExportStage(savedText(saved, skipped))
    setLastExport(saved)
    if (viewAfterExport.current) {
      viewAfterExport.current = false
      setViewerOpen(true)
    }
  }

  function cancelExport() {
    viewAfterExport.current = false
    setExportPlan(null)
  }

  function savedText(saved, skipped = []) {
    const { saved_to: savedTo, transcripts_saved_to: transcripts, transcripts_note: note, cover_pages: coverPages } = saved
    // 없는 것과 안 만든 것을 구분해 적는다 — 파일 하나만 적고 마는 것은 빈칸을 0으로 채우는 것과 같다
    const second = transcripts ? ` · 응답 전문 ${transcripts}` : note ? ` · 응답 전문 없음: ${note}` : ''
    // 표지는 결론 면이라 한 쪽이어야 한다 — 넘친 것은 PDF를 열기 전에 알린다
    const overflow = coverPages > 1 ? ` · ▲ 표지가 ${coverPages}쪽으로 넘쳤습니다(무엇을 뺄지 정해야 합니다)` : ''
    return `리포트를 저장했습니다 — ${savedTo}${second}${overflow}${skipped.length ? ` · 빠진 노트: ${skipped.join(' / ')}` : ''}`
  }

  // 내보내기 = 사용자가 한 번 누르는 명시적 동작이라, 그 조합의 노트가 없으면 생성해서 싣는다.
  // 다만 모델을 후보 수만큼 부르므로 먼저 알리고 "노트 없이 바로 내보내기"를 나란히 둔다.
  async function handleExportReport() {
    setReportBusy(true)
    setError('')
    setExportStage('')
    try {
      if (!tableText) throw new Error('선택한 실행을 아직 불러오는 중입니다')
      // 불러오는 중은 실패가 아니다 — 곧 끝나는데 지금 찍으면 멀쩡한 리포트에서 종합 점수 장이 빠진다.
      // 조회 실패면 막지 않는다(리포트가 종합 점수에서 나온 장만 빼고 찍는다).
      if (!demotion) throw new Error('일관성 판정 게이트를 아직 불러오는 중입니다')
      const status = await fetchCompareNotesStatus(selectedIds, tableText)
      if (status.missing.length === 0) {
        finishExport(await renderReport(status))
        return
      }
      const active = await fetchActiveTest().catch(() => ({ run_id: null }))
      if (active.run_id) {
        // 측정 중에는 노트를 생성하지 않는다(측정 오염) — 오류가 아니라 노트 없이 내보내는 경로다
        setExportStage('성능 테스트가 도는 중이라 노트를 생성하지 않고 내보냅니다')
        finishExport(await renderReport(status), ['성능 테스트가 도는 중이라 노트를 생성하지 않았습니다'])
        return
      }
      setExportPlan({ status, tableText })
    } catch (e) {
      viewAfterExport.current = false
      setError(String(e.message || e))
    } finally {
      setReportBusy(false)
    }
  }

  async function confirmExport(withNotes) {
    const { status, tableText } = exportPlan
    setExportPlan(null)
    setReportBusy(true)
    setError('')
    let latest = status
    const skipped = []
    try {
      if (withNotes) {
        for (const [i, runId] of status.missing.entries()) {
          setExportStage(`비교 노트 생성 중 (${i + 1}/${status.missing.length})`)
          try {
            latest = await generateOneCompareNote(selectedIds, tableText, runId)
          } catch (e) {
            if (e.status === 409) {
              skipped.push('성능 테스트가 시작돼 남은 노트는 생성하지 않았습니다')
              break
            }
            // 노트 생성 실패는 리포트 실패가 아니다 — 그 모델의 노트만 빠진다
            skipped.push(`${detailsById[runId]?.model ?? runId}: ${e.message}`)
          }
        }
        setNotesRefresh((n) => n + 1)
      }
      finishExport(await renderReport(latest), skipped)
    } catch (e) {
      viewAfterExport.current = false
      setError(String(e.message || e))
      setExportStage('')
    } finally {
      setReportBusy(false)
    }
  }

  // 화면용으로 따로 그리지 않는다 — 지금 조건으로 내보낸 파일이 없으면 같은 내보내기를 거친 뒤 그 파일을 연다
  function handleViewReport() {
    if (viewAction(lastExport, currentExportKey) === 'open') {
      setViewerOpen(true)
      return
    }
    viewAfterExport.current = true
    handleExportReport()
  }

  function estimateText(status) {
    // 생성 시간을 기록한 적이 없으면 숫자로 적지 않는다 — "몇 분"은 근거 없는 값이다
    if (status.avg_duration_sec == null) return '시간이 걸립니다'
    return `실측 평균으로 약 ${Math.max(1, Math.round((status.avg_duration_sec * status.missing.length) / 60))}분`
  }

  // 선택한 실행들의 측정 조건이 서로 다르면 비교 자체가 성립하지 않을 수 있다 — 리포트 표지와 같은 함수로
  // 지표마다 그 값을 실제로 잰 실행의 조건끼리, 그리고 한 실행 안의 재실행 조건을 대조한다.
  // 경고가 아닌 실행 안 섞임(의도한 전용 상한 등)은 경고 칸 밖에 흐린 사실로 둔다 — 칸 안에 두면 경고로 읽힌다.
  const { warnings, conditionFacts } = useMemo(() => {
    const names = Object.fromEntries(selectedDetails.map((d) => [d.id, d.model]))
    const lines = conditionMismatchLines(selectedDetails, {
      hasBaseline: Boolean(baseline?.run),
      nameOf: (runId) => names[runId] ?? runId,
    })
    return {
      warnings: [...lines.warnings, ...lines.footnotes.map((f) => `※ ${f}`)],
      conditionFacts: lines.facts.map((f) => `${names[f.runId] ?? f.runId}: ${f.label} — ${f.text}`),
    }
  }, [selectedDetails, baseline])

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>LLM Playground</h1>
        <NavTabs />
        <p className="compare-sidebar-note">
          저장된 실행을 모델별로 골라 나란히 비교합니다. 실행 자체는 성능 테스트 화면에서 합니다.
        </p>
      </aside>

      <main className="bench-main">
        {error && <div className="error">{error}</div>}

        <section className="compare-list-wrap">
          <div className="compare-list-header">
            <h2>저장된 실행 ({modelGroups.length}개 모델)</h2>
            {modelGroups.length > 0 && (
              <button type="button" className="ghost" onClick={toggleAllLatest}>
                {allLatestSelected ? '전체 해제' : '모델별 기본 테스트 전체 선택'}
              </button>
            )}
          </div>

          {modelGroups.length === 0 ? (
            <div className="model-empty">아직 실행한 테스트가 없습니다</div>
          ) : (
            <ul className="compare-model-groups">
              {modelGroups.map(({ model, runs }) => {
                const latest = latestSelectionRun(runs)
                const expanded = expandedModels.has(model)
                const selectedCount = runs.filter((r) => selectedIds.includes(r.id)).length
                const score = latest ? previewScoreFor(latest.id) : null
                const baselineRatio =
                  !baseline?.run
                    ? '기준선 없음'
                    : score == null || baselinePreviewScore == null
                      ? '측정 안 됨'
                      : `${Math.round((score / baselinePreviewScore) * 100)}%`

                return (
                  <li key={model} className="compare-model-group">
                    <div className="compare-model-head">
                      <label className="result-check">
                        <input
                          type="checkbox"
                          checked={Boolean(latest) && selectedIds.includes(latest.id)}
                          disabled={!latest}
                          onChange={() => latest && toggleModelLatest(model, latest.id)}
                        />
                      </label>
                      <button type="button" className="compare-model-toggle" onClick={() => toggleExpand(model)}>
                        <span className="compare-model-toggle-top">
                          <span className="compare-model-name">{model}</span>
                          {selectedCount >= 2 && <span className="compare-model-badge">{selectedCount}개 선택됨</span>}
                          <span className="compare-model-arrow">{expanded ? '▲' : '▼'}</span>
                        </span>
                        <span className="compare-model-summary">
                          {latest
                            ? `${new Date(latest.started_at).toLocaleString()} · 종합 ${score == null ? '측정 안 됨' : score.toFixed(3)} · 기준선 대비 ${baselineRatio}`
                            : '선정용 실행 없음 — 펼쳐서 프롬프트 실험 실행을 직접 고를 수 있습니다'}
                        </span>
                      </button>
                    </div>

                    {expanded && (
                      <ul className="compare-run-list">
                        {runs.map((r) => (
                          <li key={r.id} className="compare-run-row">
                            <label className="result-check" title={blockedByPrompt(r, selectedRuns) ?? undefined}>
                              <input
                                type="checkbox"
                                checked={selectedIds.includes(r.id)}
                                disabled={!selectedIds.includes(r.id) && Boolean(blockedByPrompt(r, selectedRuns))}
                                onChange={() => toggleRun(r.id)}
                              />
                            </label>
                            <span className="compare-row-info">
                              <span className="result-meta">
                                {STATUS_LABEL[r.status] ?? r.status} · {new Date(r.started_at).toLocaleString()}
                              </span>
                              <span className={`compare-run-badge${isPromptExperiment(r) ? ' experiment' : ''}`}>
                                {isPromptExperiment(r)
                                  ? `${RUN_TYPE_LABEL.prompt_experiment}: ${r.system_prompt_meta?.title ?? r.system_prompt_meta?.name ?? '프롬프트'}`
                                  : RUN_TYPE_LABEL.selection}
                              </span>
                            </span>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() => navigate(`/benchmark?resultId=${encodeURIComponent(r.id)}`)}
                            >
                              상세 보기
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {selectedIds.length > 0 && (
          <section className="compare-table-wrap">
            <div className="compare-header">
              <h2>결과 비교 ({selectedIds.length}개)</h2>
              {baseline?.unmatched && <p className="compare-sidebar-note">기준선 열 없음 — {baseline.unmatched}</p>}
              <div className="compare-header-actions">
                <button
                  type="button"
                  className="ghost"
                  onClick={handleExportReport}
                  disabled={reportBusy || Boolean(exportPlan) || !tableText}
                >
                  {reportBusy ? '리포트 생성 중…' : '리포트 내보내기 (PDF)'}
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={handleViewReport}
                  disabled={reportBusy || Boolean(exportPlan) || !tableText}
                  title={
                    viewAction(lastExport, currentExportKey) === 'open'
                      ? `${lastExport.saved_to}을 엽니다`
                      : '지금 조건으로 내보낸 리포트가 없어 먼저 내보낸 뒤 엽니다'
                  }
                >
                  리포트 확인하기
                </button>
                {rescoreCounts.files > 0 && (
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      setRescoreStage('')
                      setRescoreOpen(true)
                    }}
                    disabled={rescoreBusy || rescoreOpen}
                  >
                    재채점…
                  </button>
                )}
              </div>
            </div>

            {exportPlan && (
              <div className="export-confirm">
                <p>
                  이 조합의 비교 노트 {exportPlan.status.missing.length}개가 아직 없습니다. 내보내기에 포함하려면 후보
                  모델을 {exportPlan.status.missing.length}번 호출합니다 — {estimateText(exportPlan.status)}.
                </p>
                <div className="export-confirm-actions">
                  <button type="button" onClick={() => confirmExport(true)}>
                    노트 생성 후 내보내기
                  </button>
                  <button type="button" className="ghost" onClick={() => confirmExport(false)}>
                    노트 없이 바로 내보내기
                  </button>
                  <button type="button" className="ghost" onClick={cancelExport}>
                    취소
                  </button>
                </div>
              </div>
            )}
            {exportStage && <p className="export-stage">{exportStage}</p>}
            {viewerOpen && lastExport && (
              <ReportViewer
                saved={lastExport}
                stale={lastExport.key !== currentExportKey}
                onClose={() => setViewerOpen(false)}
              />
            )}

            {rescoreLines.length > 0 && !rescoreOpen && (
              <ul className="rescore-notice">
                {rescoreLines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
            {rescoreOpen && rescorePlan && (
              <div className="export-confirm rescore-confirm">
                <p>
                  고른 실행이 표시하는 값의 출처 파일을 지금 채점기로 다시 채점합니다. 응답 원문은 그대로이고 점수와
                  채점기 버전만 다시 씁니다.
                  {features.maintenance_cli && (
                    <>
                      {' '}모든 결과를 한꺼번에 다시 채점하려면 <code>maintenance.py rescore</code>를 씁니다.
                    </>
                  )}
                </p>
                {rescorePlan.runs
                  .filter((run) => run.files.length > 0)
                  .map((run) => (
                    <div key={run.run_id} className="rescore-run">
                      <strong>{aliases[run.run_id] ?? run.model}</strong> — {runFilesText(run)}
                      <ul>
                        {run.files.map((file) => (
                          <li key={file.run_id}>
                            {fileLabel(file)}
                            {file.metrics.length > 0 && (
                              <ul>
                                {file.metrics.map((m) => (
                                  <li key={m.id}>{metricLine(m)}</li>
                                ))}
                              </ul>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                <div className="export-confirm-actions">
                  <button type="button" onClick={confirmRescore} disabled={rescoreBusy || rescoreCounts.files === 0}>
                    {rescoreBusy ? '재채점 중…' : `재채점 (파일 ${rescoreCounts.files}개)`}
                  </button>
                  <button type="button" className="ghost" onClick={() => setRescoreOpen(false)} disabled={rescoreBusy}>
                    취소
                  </button>
                </div>
              </div>
            )}
            {rescoreStage && <p className="export-stage">{rescoreStage}</p>}

            {warnings.length > 0 && (
              <div className="compare-warning">
                조건이 다른 실행이 섞여 있습니다 — 비교가 정확하지 않을 수 있습니다.
                <ul>
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            {conditionFacts.length > 0 && (
              <ul className="compare-condition-facts">
                {conditionFacts.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            )}

            <div className="compare-scroll">
              <table className="compare-table">
                <thead>
                  <tr>
                    <th>지표</th>
                    {selectedIds.map((id) => {
                      const d = detailsById[id]
                      // 노트가 부르는 이름(별칭)을 머리글로, 전체 이름은 아래 작게 — 노트와 표를 대조할 수 있게
                      return (
                        <th key={id}>
                          {d ? (
                            <>
                              {aliases[id] ?? d.model}
                              <span className="compare-th-full">{d.model}</span>
                            </>
                          ) : (
                            '불러오는 중…'
                          )}
                        </th>
                      )
                    })}
                    {baseline?.run && <th>기준선 ({baseline.run.model})</th>}
                  </tr>
                </thead>
                <tbody>
                  {ALL_METRIC_DEFS.map((def) => (
                    <tr key={def.key}>
                      <td>
                        {def.label} <span className="compare-direction">{def.direction === 'lower' ? '↓' : '↑'}</span>
                      </td>
                      {selectedIds.map((id) => {
                        const d = detailsById[id]
                        return <td key={id}>{d ? formatRawCell(d, def) : '불러오는 중…'}</td>
                      })}
                      {baseline?.run && (
                        <td>{formatRawCell(baseline.run, def, { baseline: true, inScope: def.inScope })}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <CompareRadar
              rows={selectedDetails}
              baselineEntry={baseline?.run}
              baselineStaleness={baseline?.staleness}
              demotion={demotion}
              onWeightsChange={setWeighting}
            />

            <CompareNotes
              selectedDetails={selectedDetails}
              baseline={baseline}
              demotion={demotion}
              tableText={tableText}
              refreshToken={notesRefresh}
            />

            <ConsistencyJudgmentPanel selectedRunIds={selectedIds} onSaved={onJudgmentSaved} />
          </section>
        )}
      </main>
    </div>
  )
}
