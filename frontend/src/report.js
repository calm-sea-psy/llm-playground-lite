// 결과 리포트 내보내기 — PDF에 들어갈 수치와 **이번 실행에 대한 사실 문장**을
// 여기서 만들어 구조화된 JSON으로 보낸다. 렌더링(matplotlib)은 백엔드가 하지만, 25개
// 지표 정의·저장 상태 해석·정규화·가중치 로직은 scoring.js/metrics.js 하나에만 둔다 —
// 백엔드에 같은 걸 또 만들면 소스가 갈라진다. 백엔드(backend/report.py)는 이 payload를
// 그리기만 한다.
//
// 예외 둘은 백엔드가 소유한다(규칙의 주인이 그쪽이라서다):
// - **세트 지문의 판정** — 일치/불일치/기록 없음/비교 불가와 키 종류별 원인은 baseline.py 규칙이다.
//   여기서는 실행별 원본 맵만 싣는다.
// - **일관성/재현성 상세 장** — 쌍을 고르려면 유사도 판정기를, 질문을 붙이려면 세트 파일과
//   지문 대조를 써야 한다. JS로 하면 채점기가 두 벌이 된다. 여기서는 run_id만 싣는다.
import {
  BASELINE_ROW_ID,
  CATEGORIES,
  COMPOSITE_DEFINITION,
  FAILURE_CAUSE_LABEL,
  METRICS,
  OUTCOME,
  OUTCOME_LABEL,
  PRESETS,
  RUN_TYPE_LABEL,
  baselineExclusions,
  baselineMetricCount,
  baselineRemeasurePending,
  categoryContribution,
  consistencyExclusion,
  consistencyGateLines,
  flipGroups,
  formatMetricCell,
  gatedWeights,
  isPromptExperiment,
  itemFailure,
  koreanPurityGate,
  metricSetGaps,
  metricState,
  normalize,
  presetFlips,
  rankOrder,
  rankWithTies,
  sameOrder,
  TIE_LABEL,
  TIE_OMITTED_NOTE,
  toRow,
  weightedScore,
} from './scoring'
import { applySelectionRule } from './selection'
import { pctFmt } from './metrics'
import {
  conditionMismatchLines,
  consistencyNumPredict,
  documentLength,
  NO_DOCUMENT_GUARD,
  commitText,
  hardwareText,
  machineSpecText,
  softwareText,
  powerText,
  longContextReloadText,
  reloadAfterSkippedText,
  machineLabel,
  ollamaVersionText,
  samplingText,
  toolResponsesText,
} from './runDiff'

// 상용 대비 점수 — 기준선이 값을 가진 지표가 이보다 적으면 싣지 않는다(리포트가 그 까닭을 점수 정리에 적는다)
export const COMMERCIAL_MIN_METRICS = 5

// 결과 설명의 "갈린다" 임계값 — 문서에 못박은 값이고 문장에 그대로
// 드러낸다. 숨기면 사실이 아니라 판단이 된다.
export const SPLIT_THRESHOLD_PP = 20 // 비율 지표: 최고 − 최저 ≥ 20%p
export const SPLIT_THRESHOLD_RATIO = 2 // 크기 지표: 최고 ÷ 최저 ≥ 2배
export const MAX_SPLIT_METRICS = 3

// 모델 이름은 별칭 규칙이 백엔드(report.py)에 있으므로 문장에는 자리표시자로 넣는다.
const model = (runId) => `⟦run:${runId}⟧`

/** 표지의 "측정 조건 한 벌" — 몇 달 뒤 이 PDF가 무엇을 잰 것인지 알아야 남겨둔 의미가 생긴다.
 * 일관성을 지표 단위로 재실행했으면 그 값은 부모가 아니라 재실행의 조건으로 쟀다. */
export function conditionLines(detail) {
  const c = detail?.config || {}
  if (Object.keys(c).length === 0) return ['측정 조건 기록 없음 (이 기능 이전 실행)']
  const consistencyConfig = detail?.provenance?.consistency?.config || c
  return [
    // 상한마다 무엇의 상한인지 붙인다 — 이름 없는 하나가 품질·일관성 옆에 서면 기본값인데 둘만 올린 것으로 읽힌다
    `num_ctx ${c.num_ctx} · 속도 탐침 num_predict ${c.num_predict} · 품질 num_predict ${c.quality_num_predict} · 일관성 num_predict ${consistencyNumPredict(consistencyConfig)}`,
    `샘플링 ${samplingText(c.sampling)}`,
    `일관성 샘플링 ${samplingText(c.consistency_sampling)}`,
    `thinking ${c.think ? '켬' : '끔'} · 워밍업 ${c.warmup_count}회(본 집계에서 뺐다) · 반복 ${c.repeat_count}회`,
    `타임아웃 짧은 ${c.timeout_short_sec}초 / 긴 ${c.timeout_long_sec}초 / 품질 ${c.quality_timeout_sec}초`,
    `컨텍스트 단계 ${(c.context_stage_tokens || []).join(' / ')} 토큰`,
    `요약 압축 모델 ${c.summarizer_model ?? '기록 없음'}`,
    `긴 컨텍스트 시작 ${longContextReloadText(c)}`,
    `건너뛴 항목 뒤 재로드 ${reloadAfterSkippedText(c)}`,
    `문항 집합 ${c.question_set ?? (c.assignment ? '고른 문항' : '기록 없음')}`,
    `문서 길이 ${documentLength(c)}`,
    `문서 방어 ${documentGuardText(c)}`,
    `측정 기계 ${machineLabel(c.measurement_machine)} · Ollama ${ollamaVersionText(c)}`,
    `하드웨어 ${hardwareText(c.hardware)}`,
    `도구 커밋 ${commitText(c.tool_commit)}`,
    `소프트웨어 ${softwareText(c.software)}`,
    `전원(시작 시) ${powerText(c.power)}`,
    `도구 응답 ${toolResponsesText(c)}`,
  ]
}

/** 문서를 함께 보내는 호출에 붙은 방어 — 판과 지문만 적는다(문구는 코드에 있다).
 * 기록이 없으면 방어 없이 잰 실행이다. 문서를 읽는 지표의 값을 나란히 읽을 때 이 줄이 같아야 같은 조건이다. */
export function documentGuardText(config) {
  const guard = config?.document_guard
  if (!guard) return NO_DOCUMENT_GUARD
  return `v${guard.version} (${guard.sha256})`
}

// 켬 경로의 요약기가 후보 자신이라는 기록 값 — 백엔드 `quality_runner.SELF_SUMMARIZER`와 같다
const SELF_SUMMARIZER = '후보 자신'

/** 측정 조건 상세의 `요약 압축` 문단 — 이 Use Case에 다중 턴 압축이 없어 긴 컨텍스트 두 지표의 점수는 **압축을 끈 경로의
 * 값**이고, 켠 경로는 참고 행이라는 것과, 두 경로의 값이 다른 칸. 채팅 화면의 압축 옵션은 이 도구의 기능이지 제품
 * 파이프라인이 아니라는 것도 여기 적는다 — 그 옵션을 보고 제품에 압축이 있다고 읽기 쉽다(실제로 한 번 그렇게 읽혔다).
 * **값은 문장에 박지 않고 결과에서 센다** — 다시 재거나 재채점하면 따라간다. 켬 경로의 요약기가 실행마다 같지 않으면
 * 요약기를 문장에 적지 않고 측정값 표의 행 각주(실행마다 요약기)를 가리킨다.
 *
 * 켬↔끔 차이의 까닭은 요약 seed가 없던 실행에서만 가를 수 없다고 적는다 — 요약문이 결과에 남지 않고 그 요약이 고정 샘플링이
 * 아니었다. 요약 seed가 고정된 실행의 차이는 압축의 효과로 읽는다(압축이 걸리기 전에 갈린 실행은 따로 적힌다). */
export function compressionLines(details) {
  const METRICS_LC = [
    ['recall', '기억력'],
    ['constraint', '제약 유지'],
  ]
  const runs = details.filter((d) => d.metrics?.long_context)
  if (runs.length === 0) return []
  const summarizerOf = (d) => {
    const config = d.provenance?.long_context?.config || d.config || {}
    return { model: config.summarizer_model ?? null, seed: config.summarizer_sampling?.seed ?? null }
  }
  const changes = new Map() // `기억력 100→67%` → [실행]
  const unseeded = new Set()
  const summarizers = []
  const missing = []
  let cells = 0
  for (const d of runs) {
    const lc = d.metrics.long_context
    const pairs = METRICS_LC.filter(([key]) => lc[key]?.score != null && lc[key]?.score_compressed != null)
    if (pairs.length === 0) {
      missing.push(model(d.id))
      continue
    }
    summarizers.push(summarizerOf(d))
    for (const [key, label] of pairs) {
      cells += 1
      const off = pctFmt(lc[key].score)
      const on = pctFmt(lc[key].score_compressed)
      if (on === off) continue
      const text = `${label} ${off.slice(0, -1)}→${on}`
      changes.set(text, [...(changes.get(text) ?? []), model(d.id)])
      if (summarizerOf(d).seed == null) unseeded.add(model(d.id))
    }
  }
  const numCtx = runs.map((d) => d.config?.num_ctx).find((v) => v != null)
  const selfSeeds = new Set(summarizers.map((s) => (s.model === SELF_SUMMARIZER && s.seed != null ? s.seed : null)))
  const who =
    summarizers.length && selfSeeds.size === 1 && !selfSeeds.has(null)
      ? `후보 자신이 요약, seed ${[...selfSeeds][0]}`
      : '요약기는 실행마다 측정값 표의 행 각주에'
  const head =
    `요약 압축 — 다중 턴 압축 없음: 문서와 대화는 ${numCtx != null ? `num_ctx ${numCtx}` : '컨텍스트'} 안에서 그대로 유지한다. ` +
    '긴 컨텍스트 기억력·다중 턴 제약 유지 점수는 압축 끔 경로 값이다. ' +
    `압축 켬 경로(${who})는 참고 행으로만 싣는다. 플레이그라운드 채팅 화면의 압축 옵션은 이 도구의 기능이지 제품 파이프라인이 아니다.`
  const changed = [...changes.entries()].map(([text, names]) => `${names.join('·')} ${text}`)
  const changedCells = [...changes.values()].reduce((a, names) => a + names.length, 0)
  const lines = []
  if (changed.length) {
    const rest = changedCells < cells ? ' 후보 중 나머지는 같다.' : ''
    lines.push(`${head} 압축을 켠 경로와 값이 다른 칸(끔→켬): ${changed.join(', ')}.${rest}`)
    if (unseeded.size) {
      lines.push(`요약 seed가 없던 실행(${[...unseeded].join('·')})은 두 경로가 갈린 까닭이 압축인지 기록으로 가를 수 없다 — 요약문이 결과에 남지 않고 그 요약이 고정 샘플링이 아니었다.`)
    }
  } else if (cells) {
    lines.push(`${head} 압축을 켠 경로와 값이 같다.`)
  } else {
    lines.push(head)
  }
  if (missing.length) lines.push(`압축을 켠 경로 값이 없다 — ${missing.join('·')}`)
  return lines
}

/** 가중치 민감도 장의 결론 — 화면(CompareRadar)이 같은 상황에서 띄우는 문장과 같다. 순위가 다르면
 * **뒤집힌 쌍**을 적는다 — "순위가 다릅니다"만으로는 어디가 뒤집혔는지 알 수 없어
 * 덤벨을 눈으로 다시 읽어야 한다. */
function weightSensitivityConclusion({ neutralOrder, usageOrder, pairs }) {
  if (neutralOrder.length < 2) return '점수가 나온 실행이 둘 미만이라 순위 민감도를 판단할 수 없습니다.'
  if (sameOrder(neutralOrder, usageOrder)) {
    return '두 프리셋의 순위가 같습니다 — 가중치 선택과 무관하게 우세한 차이로 보입니다.'
  }
  const flips = pairs.map(
    ({ a, b, i, j }) => `${i + 1}~${j + 1}위가 뒤집힌다 (균등: ${model(a)} > ${model(b)} / ${PRESETS.usage.label}: 반대)`,
  )
  return `두 프리셋의 순위가 다릅니다 — 이 순위는 가중치 선택에 좌우됩니다. ${flips.join('; ')}`
}

/** 종합 순위 장의 동률 — 지금 가중치 순위에 프리셋 뒤집힘 묶음을 얹는다. 값은 그대로 인쇄하고 등수만 나누지 않는다. */
function rankingTies(details, scores, flips) {
  const order = rankOrder(details, (id) => scores[id])
  const { ranks, tied, omitted } = rankWithTies(order, flipGroups(flips.pairs))
  // 표시되는 묶음만(생략된 묶음은 빼고), 지금 순위 순서로
  const groups = flipGroups(flips.pairs)
    .filter((g) => g.every((id) => tied.has(id)))
    .map((g) => [...g].sort((a, b) => order.indexOf(a) - order.indexOf(b)))
  const sentences = groups.map((g) => `${g.map(model).join(' · ')}: ${TIE_LABEL} — ${ranks[g[0]]}위를 함께 쓴다`)
  if (omitted) sentences.push(TIE_OMITTED_NOTE)
  return { order, groups, tied: [...tied], omitted, sentences }
}

/** 채점기 버전 대조의 원본 — 판정(지금 버전·재채점 가능 여부)은 백엔드 규칙이라 기록만 싣는다.
 * 합친 뷰의 `scorer_versions`는 항목마다 그 값을 낸 실행의 기록이다. */
function scorerVersionSource(run) {
  if (!run) return null
  return {
    recorded: run.scorer_versions ?? {},
    items: (run.items ?? []).map((it) => ({ id: it.id, label: it.label, outcome: it.outcome ?? null })),
    scored: Object.entries(run.metrics ?? {})
      .filter(([, v]) => v != null)
      .map(([k]) => k),
    // 재채점 시각과 그때 바뀐 버전 — 기록과 코드가 일치해도 `언제 다시 채점했고 무엇이 바뀌었나`를 리포트가 말한다.
    // `changes`는 **없음(null)과 빈 기록({})을 가른다**: 없으면 기록을 남기기 전 파일이라 모르고, 비었으면 바뀐 것이 없다
    rescored_at: run.rescored_at ?? null,
    changes: run.scorer_version_changes ?? null,
  }
}

/** 측정값 칸 문자열 — 실행 실패면 원인까지 적는다(리포트에서 가장 눈에 띄어야 하는 상태). */
function cellText(run, metric, opts) {
  const text = formatMetricCell(run, metric, opts)
  if (opts?.baseline) return text
  const { outcome } = metricState(run, metric)
  if (outcome !== OUTCOME.FAILED) return text
  const causes = (metric.items ?? [])
    .map((i) => itemFailure(run, i)?.cause)
    .filter(Boolean)
    .map((c) => FAILURE_CAUSE_LABEL[c] ?? c)
  return causes.length ? `${text} (${[...new Set(causes)].join('·')})` : text
}

/** 지표별 비교 장의 결과 설명(임계값을 못박고 문장에 그 기준을
 * 드러낸다). 후보끼리만 비교하고, 상태가 붙은 값(능력 부재 0점 등)은 판정에서 빼되
 * 판정 모수와 능력 부재 문장은 따로 적는다 — 빼기만 하고 말하지 않으면 두 모델만 비교한
 * 결과가 넷을 비교한 것처럼 읽힌다. 순서는 정규화 값 차이 순(단위가 달라도 비교된다). */
export function metricSplitSentences(details, normalized) {
  const splits = []
  const incapable = new Map()
  for (const m of METRICS) {
    const states = details.map((d) => ({ id: d.id, ...metricState(d, m) }))
    for (const s of states) {
      if (s.outcome === OUTCOME.INCAPABLE || s.outcome === OUTCOME.CONFIRMED_FAILURE) {
        const list = incapable.get(s.id) ?? []
        list.push(`${m.label}(${OUTCOME_LABEL[s.outcome]})`)
        incapable.set(s.id, list)
      }
    }
    const measured = states.filter((s) => s.outcome === OUTCOME.MEASURED)
    if (measured.length < 2) continue
    const values = measured.map((s) => s.value)
    const max = Math.max(...values)
    const min = Math.min(...values)
    const basis = measured.length < details.length ? ` — ${details.length}개 중 ${measured.length}개로 판정` : ''
    // 같은 값은 갈리지 않는다 — 전원 같은 행을 모은 줄은 리포트가 표·차트와 같은 규칙으로 따로 적는다(값이 없는 모델이 있으면 전원이 아니다)
    if (max === min) continue
    const norms = measured.map((s) => normalized[m.key]?.[s.id]).filter((v) => v != null)
    const order = norms.length ? Math.max(...norms) - Math.min(...norms) : 0
    if (m.kind === 'proportion') {
      const pp = (max - min) * 100
      if (pp >= SPLIT_THRESHOLD_PP) {
        splits.push({ order, text: `${m.label}: ${pp.toFixed(0)}%p 갈린다 (${m.fmt(max)} / ${m.fmt(min)})${basis}` })
      }
    } else if (min === 0) {
      splits.push({ order, text: `${m.label}: 한 모델은 0 (${m.fmt(max)} / ${m.fmt(min)})${basis}` })
    } else if (max / min >= SPLIT_THRESHOLD_RATIO) {
      splits.push({ order, text: `${m.label}: ${SPLIT_THRESHOLD_RATIO}배 이상 차이 (${m.fmt(max)} / ${m.fmt(min)})${basis}` })
    }
  }
  splits.sort((a, b) => b.order - a.order)
  const out = [
    `기준 — 비율 지표는 최고와 최저가 ${SPLIT_THRESHOLD_PP}%p 이상, 크기 지표는 ${SPLIT_THRESHOLD_RATIO}배 이상 차이날 때만 "갈린다"고 적는다(후보끼리, 차이 큰 순 최대 ${MAX_SPLIT_METRICS}개).`,
  ]
  if (splits.length === 0) out.push('이 기준을 넘는 지표가 없다.')
  out.push(...splits.slice(0, MAX_SPLIT_METRICS).map((s) => s.text))
  for (const [runId, labels] of incapable) out.push(`${model(runId)}는 수행하지 못한다 — ${labels.join(', ')}`)
  return out
}

/** 1위·최하위 문장 — 그 자리가 동률 묶음이면 한 모델만 지목하지 않고 묶음 전체를 적는다(등수를 나누지 않는다). */
function rankingSentences(details, scores, ties) {
  const { order, groups } = ties
  if (order.length === 0) return ['종합 점수가 나온 실행이 없다.']
  const first = order[0]
  const last = order[order.length - 1]
  const seat = (id) => groups.find((g) => g.includes(id)) ?? [id]
  const names = (id) => {
    const members = seat(id)
    const text = members.map((m) => `${model(m)} ${scores[m].toFixed(3)}`).join(' · ')
    return members.length > 1 ? `${text} (동률)` : text
  }
  const out = [`1위 ${names(first)}`]
  if (!seat(first).includes(last)) {
    out.push(`최하위 ${names(last)} — 차이 ${(scores[first] - scores[last]).toFixed(3)}`)
  }
  return out
}

/** 점수 구성 장의 결론 — 가장 큰 몫만 적으면 전원이 같은 한 줄을 받아 스택 막대에서 읽어야 할 것이
 * 사라진다. **어디서 벌고 어디서 잃었나**를 함께 적되, 기준은 그 카테고리의 **모델 평균 기여도**다
 * (총점 대비 비율로 적으면 총점이 낮은 모델이 어디서나 잃은 것처럼 보인다). */
function breakdownSentences(details, contribution) {
  // 차트와 같은 순서(기여도 합 = 종합 점수, 높은 순)로 적는다 — 선택 순서로 적으면 차트와 어긋나 읽힌다.
  const total = (d) => Object.values(contribution[d.id] ?? {}).reduce((sum, v) => sum + (v ?? 0), 0)
  const ordered = [...details].sort((a, b) => total(b) - total(a))
  const tops = ordered.map((d) => {
    const entries = Object.entries(contribution[d.id] ?? {}).filter(([, v]) => v > 0)
    if (entries.length === 0) return { id: d.id, label: null }
    const [label, value] = entries.sort((a, b) => b[1] - a[1])[0]
    return { id: d.id, label, value }
  })
  const categories = [...new Set(ordered.flatMap((d) => Object.keys(contribution[d.id] ?? {})))]
  const share = (d, c) => contribution[d.id]?.[c] ?? 0
  const mean = (c) => ordered.reduce((sum, d) => sum + share(d, c), 0) / ordered.length
  const means = Object.fromEntries(categories.map((c) => [c, mean(c)]))
  const gap = (d, c) => share(d, c) - means[c]
  const signed = (v) => `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(3)}`

  const lines = []
  // 전원 동일 규칙은 모든 장의 결과 문장에 적용한다 — 네 줄을 읽고 나서야 같다는 것을 알게 하지 않는다
  if (tops.length > 1 && tops.every((t) => t.label && t.label === tops[0].label)) {
    lines.push(`전원 가장 큰 몫은 ${tops[0].label}이다 (${tops.map((t) => `${model(t.id)} ${t.value.toFixed(3)}`).join(' · ')})`)
  } else {
    lines.push(...tops.map((t) =>
      t.label ? `${model(t.id)}: 가장 큰 몫은 ${t.label} ${t.value.toFixed(3)}` : `${model(t.id)}: 기여한 카테고리 없음`,
    ))
  }
  if (ordered.length < 2 || categories.length === 0) return lines
  for (const d of ordered) {
    const sorted = [...categories].sort((a, b) => gap(d, b) - gap(d, a))
    const best = sorted[0]
    const worst = sorted[sorted.length - 1]
    const parts = []
    if (gap(d, best) > 0.0005) parts.push(`${best} ${signed(gap(d, best))}`)
    if (gap(d, worst) < -0.0005) parts.push(`${worst} ${signed(gap(d, worst))}`)
    lines.push(parts.length
      ? `${model(d.id)}: 모델 평균 대비 ${parts.join(' · ')}`
      : `${model(d.id)}: 모든 카테고리가 모델 평균과 같다`)
  }
  return lines
}

/** 합성 지표의 세부 행과 참고 행(세부 행은 상위 값의 구성 요소일 때만, 방향을 상위 행에
 * 맞춰 변환해 적는다. 점수에 들어가지 않는 값은 세부 행이 아니라 `참고`다). 노트 입력 표·화면 표와 리포트
 * 표가 같은 값을 가리키게 한다. */
function subRows(metric, details, baseRun) {
  const cell = (run, get, raw) => {
    const v = run?.metrics ? get(run.metrics) : null
    if (v == null) return '—'
    const r = raw?.(run.metrics)
    return raw && r != null ? `${pctFmt(v)} (${metric.components.find((c) => c.raw === raw).rawLabel} ${pctFmt(r)})` : pctFmt(v)
  }
  const cells = (run, def) => (run?.metrics && def.cells && def.get(run.metrics) != null ? def.cells(run.metrics) : null)
  const rows = []
  for (const c of metric.components ?? []) {
    rows.push({
      // 키를 함께 싣는다 — 리포트가 이 행에 표시를 달 때 이름이 아니라 키로 찾는다
      key: c.key,
      label: c.label,
      kind: 'component',
      raw: Object.fromEntries(details.map((d) => [d.id, cell(d, c.get, c.raw)])),
      baseline_raw: baseRun ? cell(baseRun, c.get, c.raw) : null,
      n: Object.fromEntries(details.map((d) => [d.id, cells(d, c)])),
      baseline_n: baseRun ? cells(baseRun, c) : null,
    })
  }
  for (const ref of metric.references ?? []) {
    rows.push({
      key: ref.key,
      label: ref.label,
      kind: 'reference',
      raw: Object.fromEntries(details.map((d) => [d.id, cell(d, ref.get)])),
      // 기준선이 재지 않는 참고 값은 옛 기준선 파일에 값이 남아 있어도 싣지 않고 그 사실을 적는다(`baselineText`)
      baseline_raw: baseRun ? (ref.baselineText ?? cell(baseRun, ref.get)) : null,
      n: Object.fromEntries(details.map((d) => [d.id, cells(d, ref)])),
      baseline_n: baseRun && !ref.baselineText ? cells(baseRun, ref) : null,
    })
  }
  return rows
}

/** 지표 행의 채점 칸 수 — 칸에 값이 적히는 경우에만(검증 중·원인 미확인은 값을 곁에 적어 센다). 기준선의 비교 제외
 * 지표는 `metricState`가 값을 싣지 않아 저절로 빠진다. */
function metricCells(run, metric, { baseline = false } = {}) {
  const { outcome, value, raw } = metricState(run, metric, { baseline })
  return (value ?? raw) == null || outcome === OUTCOME.INCAPABLE || !metric.cells || !run?.metrics ? null : metric.cells(run.metrics)
}

/** 도구 결과 인젝션 저항성의 칸 수 — 참고 행과 채점 규칙 장이 같이 쓴다(둘이 다른 수를 적으면 안 된다). */
const probeCells = (d) => (d.metrics?.injection_probe?.injection_resistance_rate == null ? null : d.metrics.injection_probe.detail?.length ?? null)

/** 측정값 표 맨 아래의 `참고` 행 — 점수에 들어가지 않는 값(보조 지표, 한국어 출력 순도 게이트). */
function referenceRows(details) {
  const probe = (d) => {
    const v = d.metrics?.injection_probe?.injection_resistance_rate
    return v == null ? '—' : pctFmt(v)
  }
  const purity = (d) => {
    const gate = koreanPurityGate(d)
    if (!gate) return '—'
    if (gate.state === 'undeterminable') return '판정 불가'
    return `${pctFmt(gate.score)}${gate.state === 'unfit' ? ' · 주 용도 부적합' : ''}`
  }
  // 문턱도 백엔드가 판정에 쓴 값을 그대로 적는다 — 표시용 사본을 두면 어긋날 때 사람이 틀린 문턱을 읽는다
  const thresholds = [...new Set(details.map((d) => koreanPurityGate(d)?.threshold).filter((t) => t != null))]
  const thresholdText = thresholds.length ? `, 임계 ${thresholds.map((t) => `${Math.round(t * 100)}%`).join(' / ')}` : ''
  // 게이트의 분모는 칸이 아니라 답한 응답 수다(빈 응답·잘린 깨끗한 답은 백엔드가 이미 뺐다)
  const purityCount = (d) => {
    const gate = koreanPurityGate(d)
    return gate && gate.state !== 'undeterminable' ? (gate.responses ?? null) : null
  }
  return [
    { label: '도구 결과 인젝션 저항성 (보조 지표)', items: ['injection_probe'], raw: Object.fromEntries(details.map((d) => [d.id, probe(d)])),
      n: Object.fromEntries(details.map((d) => [d.id, probeCells(d)])) },
    { label: `한국어 출력 순도 (게이트${thresholdText})`, raw: Object.fromEntries(details.map((d) => [d.id, purity(d)])),
      n: Object.fromEntries(details.map((d) => [d.id, purityCount(d)])), n_unit: '응답' },
    ...meteredRows(details),
  ]
}

/** 계측 참고 행 — 두 값이 **같은 호출**(계측 구간 안의 후보 모델 호출)에서 나온다. 기준선 칸은 값이 없는 까닭이 둘이라 말을
 * 가른다: 응답 시간은 기준선에 그 기록이 없어서, GPU 전력은 클라우드라 해당이 없어서다. */
export function meteredRows(details) {
  const timing = (d) => d.metrics?.call_timing
  const power = (d) => d.metrics?.gpu_power
  const seconds = (d) => {
    const v = timing(d)?.elapsed_median_sec
    return v == null ? '—' : `${v.toFixed(2)}초`
  }
  const watts = (d) => {
    const p = power(d)
    if (!p) return '—'
    if (p.not_measured) return '재지 않음'
    return p.extra_wh_per_call == null ? '—' : `${p.extra_wh_per_call.toFixed(3)}Wh`
  }
  const count = (get) => (d) => get(d)?.calls ?? null
  if (!details.some((d) => timing(d) || power(d))) return []
  return [
    { key: 'call_timing', label: '계측 · 응답 시간 중앙값 (초, 답 길이 포함)', raw: Object.fromEntries(details.map((d) => [d.id, seconds(d)])),
      n: Object.fromEntries(details.map((d) => [d.id, count(timing)(d)])), n_unit: '호출', baseline_raw: '기록 없음', baseline_status: 'unrecorded' },
    { key: 'gpu_power', label: '계측 · 호출당 추가 GPU 전력 (Wh, GPU만)', raw: Object.fromEntries(details.map((d) => [d.id, watts(d)])),
      n: Object.fromEntries(details.map((d) => [d.id, count(power)(d)])), n_unit: '호출', baseline_raw: '해당 없음', baseline_status: 'not_applicable' },
  ]
}

/** 상용 대비 점수 — **기준선이 값을 가진 지표만으로** 후보와 기준선을 다시 계산한다.
 * 균등 가중치 하나로만 매긴다(품질·도구 프리셋을 쓰면 이 부분집합에 도구 지표가 없어 이름이 거짓이 된다).
 * 검증 전 지표(`검증 중`·`원인 미확인`)는 `metricState`가 이미 뺐다. 남은 지표가 다섯 개 미만이면 싣지 않는다. */
function commercialComparison(details, baseRun, normalized) {
  if (!baseRun) return null
  const subset = METRICS.filter((m) => metricState(baseRun, m, { baseline: true }).value != null)
  const excluded = baselineExclusions(baseRun).map((e) => ({ label: e.label, reason: e.reason }))
  if (subset.length < COMMERCIAL_MIN_METRICS) {
    return { omitted: true, metric_count: subset.length, excluded }
  }
  const weights = Object.fromEntries(METRICS.map((m) => [m.key, subset.includes(m) ? 1 : 0]))
  return {
    omitted: false,
    metric_count: subset.length,
    metric_labels: subset.map((m) => m.label),
    excluded,
    scores: Object.fromEntries(details.map((d) => [d.id, weightedScore(normalized, weights, d.id)])),
    baseline_score: weightedScore(normalized, weights, BASELINE_ROW_ID),
  }
}

/** 변동 장의 결론 — **관측된 사실까지만** 적는다(겹침 여부). 표본이 적을 때 ±1σ 비겹침은
 * 휴리스틱이라 "차이는 실재한다"로 번역하지 않는다. 다만 이 장의 읽는 법(`겹치면 동률로 읽는다`)은
 * **결과 줄이 끝까지 적용한다** — 규칙을 적어 두고 적용해 주지 않으면 읽는 사람이 매번 다시 판단한다. */
function varianceSentences(details) {
  const bars = details
    .map((d) => ({ id: d.id, median: d.metrics?.tok_per_sec, stdev: d.metrics?.tok_per_sec_stdev ?? 0 }))
    .filter((b) => b.median != null)
  if (bars.length < 2) return []
  const overlaps = []
  for (let i = 0; i < bars.length; i++) {
    for (let j = i + 1; j < bars.length; j++) {
      const a = bars[i]
      const b = bars[j]
      if (a.median - a.stdev <= b.median + b.stdev && b.median - b.stdev <= a.median + a.stdev) {
        overlaps.push(`${model(a.id)}와 ${model(b.id)}`)
      }
    }
  }
  if (overlaps.length === 0) {
    return [`${bars.length}개 모델의 오차 막대(중앙값 ±표준편차)가 서로 겹치지 않는다 — 읽는 법대로 속도 순위는 그대로 읽어도 된다.`]
  }
  return [`오차 막대가 겹치는 쌍: ${overlaps.join(', ')} — 읽는 법대로 이 쌍은 속도 동률로 읽고, 나머지 순위는 그대로 읽는다.`]
}

export function buildReportPayload({ selectedDetails, baseline, weights: chosenWeights, presetName, demotion, notes, chapters }) {
  const rows = selectedDetails.map((d) => toRow(d))
  const baseRun = baseline?.run
  const normRows = baseRun ? [...rows, toRow(baseRun, BASELINE_ROW_ID)] : rows
  const normalized = normalize(normRows)
  // 일관성 게이트 → 가중치 확정 → 뒤집힘 → 동률 순서다. 세 가중치 모두 게이트를 먼저 적용한다.
  const weights = gatedWeights(chosenWeights, demotion)
  const neutralW = gatedWeights(PRESETS.neutral.build(), demotion)
  const usageW = gatedWeights(PRESETS.usage.build(), demotion)
  const scoresFor = (w) => Object.fromEntries(selectedDetails.map((d) => [d.id, weightedScore(normalized, w, d.id)]))
  const currentScores = scoresFor(weights)
  const neutralScores = scoresFor(neutralW)
  const usageScores = scoresFor(usageW)
  const flips = presetFlips(selectedDetails, neutralScores, usageScores)
  const ties = rankingTies(selectedDetails, currentScores, flips)
  const gate = consistencyGateLines(demotion)
  // 게이트 조회 실패 — 강등 여부를 모르는 채 일관성이 들어간 종합 점수를 인쇄하지 않는다. 막지 않고
  // 종합 점수에서 나온 값(순위·점수 구성·프리셋 점수·결론 문장)만 싣지 않는다. 표지 문구와 뺀 장은 백엔드가 적는다.
  const withheld = demotion?.status === 'error'
  const conditions = conditionMismatchLines(selectedDetails, { hasBaseline: Boolean(baseRun), nameOf: model })
  const contribution = Object.fromEntries(
    selectedDetails.map((d) => {
      const byId = categoryContribution(normalized, weights, d.id)
      return [d.id, Object.fromEntries(CATEGORIES.map((c) => [c.label, byId[c.id] ?? 0]))]
    }),
  )
  const gaps = metricSetGaps(selectedDetails)

  return {
    meta: {
      generated_at: new Date().toISOString(),
      baseline: baseRun
        ? {
            id: baseRun.id,
            model: baseRun.model,
            measured_at: baseRun.measured_at,
            // 기준선 1.000 각주 — "비교군 안에서 그 지표들 전부 최고" + "N개 지표로 계산"
            metric_count: baselineMetricCount(baseRun),
            // 표지의 기준선 상태 — 원인은 단정하지 않는다(프로브·재측정 전까지는 `원인 미확인`)
            remeasure_pending: baselineRemeasurePending(baseRun),
            exclusions: baselineExclusions(baseRun).map((e) => ({ label: e.label, outcome: e.outcome, reason: e.reason, why: e.why })),
            // 샘플링을 고정했는지는 설정 기록이 아니라 프로바이더가 정한다 — 클라우드 경로는 기록된 샘플링을 보내지 않는다
            provider: baseRun.provider_name ?? null,
          }
        : null,
      // 기준선 열이 빈 까닭 — 후보와 같은 문서 길이로 잰 기준선이 없거나 후보의 판이 섞였다(`기준선 없음`과 뭉뚱그리지 않는다)
      baseline_unmatched: baseRun ? null : (baseline?.unmatched ?? null),
      // 한국어 출력 순도 게이트 — 임계 미만이면 표지·종합 순위 장에 `주 용도 부적합` 경고
      purity_gates: Object.fromEntries(
        selectedDetails.map((d) => {
          const gate = koreanPurityGate(d)
          return [
            d.id,
            gate
              ? { state: gate.state, score: gate.score, contaminated: gate.contaminated, responses: gate.responses, samples: gate.samples }
              : null,
          ]
        }),
      ),
      runs: selectedDetails.map((d) => ({
        id: d.id,
        model: d.model,
        started_at: d.started_at,
        // 실행 종류 — 선정용 실행은 프롬프트가 없고, 실험이면 제목과 길이를 적는다
        run_type: RUN_TYPE_LABEL[d.run_type ?? 'selection'],
        // 실제로 부른 가중치 — 태그만으로는 같은 이름에 다른 모델이 올라왔는지 모른다(리포트가 값으로 적는다)
        digest: d.config?.model_identity?.digest ?? null,
        prompt_title: isPromptExperiment(d) ? (d.system_prompt_meta?.title ?? d.system_prompt_meta?.name ?? null) : null,
        mixed: Boolean(d.mixed),
        // 혼합 실행 — 어느 지표가 어느 실행에서 왔는지(표지에 적는다)
        provenance: Object.entries(d.provenance ?? {})
          .filter(([, p]) => p.run_id !== d.id)
          .map(([itemId, p]) => ({
            item: itemId,
            label: d.items?.find((it) => it.id === itemId)?.label ?? itemId,
            run_id: p.run_id,
            started_at: p.started_at,
            rescored_at: p.rescored_at ?? null,
            // 왜 다시 쟀나 — 이유 기록 전 재실행은 null(리포트가 `이유 기록 없음`으로 적는다)
            reason: p.reason ?? null,
            // 이 재실행이 원래 실행과 어떻게 다르게 쟀는지 — 경고가 아닌 사실이라 출처 각주 뒤에 붙인다(기계 섞임은 경고 목록으로)
            notes: conditions.facts.filter((f) => f.runId === d.id && f.itemId === itemId).map((f) => f.text),
          })),
      })),
      metric_count: METRICS.length,
      condition_lines: conditionLines(selectedDetails[0]),
      // 표지의 기계 한 줄 — 무엇으로 잰 값인지는 조건 상세를 빼고 뽑아도 남아야 한다. 실행마다 다르면 다른 만큼 싣고,
      // 기록이 없으면 빈 목록이라 그 줄이 안 나간다(측정하지 않은 것을 표지에 적지 않는다)
      machine_specs: [...new Set(selectedDetails.map((d) => machineSpecText(d.config?.hardware)).filter(Boolean))],
      // 문서 길이 줄은 비교에 든 실행이 읽은 판으로 센다 — 판이 섞였으면 판마다 한 줄(섞인 것 자체는 조건 불일치 경고가 말한다)
      document_lengths: [...new Set(selectedDetails.map((d) => documentLength(d.config)))],
      // 긴 컨텍스트 두 지표가 압축을 켠 경로의 값이라는 것과, 끈 경로와 값이 다른 칸 — 첫 실행 기준이 아니라 후보 전부에서 센다
      compression_lines: compressionLines(selectedDetails),
      // 위 한 벌은 첫 실행 기준이다 — 지표마다 그 값을 잰 실행끼리 대조한 차이는 화면과 같은 함수로 따로 싣는다
      condition_basis_run_id: selectedDetails[0]?.id ?? null,
      condition_mismatches: { warnings: conditions.warnings, footnotes: conditions.footnotes },
      weight_preset: presetName ? PRESETS[presetName]?.label : '커스텀 (슬라이더 조정)',
      weight_sensitivity_conclusion: withheld ? null : weightSensitivityConclusion(flips),
      // 일관성 사람 판정 게이트 — 표지 두 줄(상태, 채점기 감시 부재)
      // `excluded`는 종합 점수에서 일관성을 뺀 까닭(넣었으면 null) — 리포트 `합치는 법` 줄이 무게 0 지표 옆에 붙인다
      consistency_gate: {
        status: demotion?.status ?? null,
        lines: withheld ? [] : gate.lines,
        watcher: gate.watcher,
        excluded: consistencyExclusion(demotion),
      },
      // 종합 점수 정의의 판 — 채점기 버전 곁에 찍힌다. 판이 다른 리포트끼리는 종합 점수를 옮겨 읽지 않는다
      composite_definition: COMPOSITE_DEFINITION,
      // 지표 집합 경고 — 모델별로 빠진 지표(표지·종합 순위·점수 구성·가중치 민감도에 붙는다)
      metric_set_gaps: Object.fromEntries(
        Object.entries(gaps).map(([id, list]) => [id, list.map((g) => ({ label: g.label, outcome: OUTCOME_LABEL[g.outcome] }))]),
      ),
      // 채점기 버전 기록 그대로 — 지표마다 지금 판정기와 대조하는 것은 백엔드(scorer_versions.py)가 한다
      scorer_versions: {
        runs: Object.fromEntries(selectedDetails.map((d) => [d.id, scorerVersionSource(d)])),
        baseline: scorerVersionSource(baseRun),
      },
      // 원본 맵 그대로 — 판정은 baseline.py가 한다
      fingerprints: {
        runs: Object.fromEntries(
          selectedDetails.map((d) => [d.id, { scope: d.scope ?? 'full', fingerprints: d.fingerprints ?? {} }]),
        ),
        baseline: baseRun ? { scope: baseRun.scope ?? 'baseline', fingerprints: baseRun.fingerprints ?? {} } : null,
      },
    },
    // 고른 장 — 빈 배열은 `기본 장만`이고, 안 넘기면(undefined) 키 자체가 빠져 백엔드가 전부 싣는다.
    // 둘을 가르지 않으면 고르는 화면을 거치지 않는 경로(시험·CLI)가 기본 판으로 쪼그라든다
    ...(chapters ? { chapters } : {}),
    models: selectedDetails.map((d) => ({ id: d.id, label: d.model, started_at: d.started_at })),
    // 지표마다 **이 실행들이 실제로 채점한 칸 수** — 리포트의 채점 표가 값 옆에 적는다.
    // 세트 크기가 아니라 돈 칸이라야 값을 그 수로 나눠 읽을 수 있다(두 칸짜리 1.000과 스무 칸짜리 1.000은 다르다)
    // 합친 지표(인젝션 저항성·Tool-calling 기본)는 제 칸이 없다 — 채점 규칙 장이 갈래마다 한 줄씩 적으므로
    // 갈래의 칸도 함께 보낸다. 보조 지표도 그 장에 줄이 있어 같이 보낸다
    metric_cells: Object.fromEntries([
      ...METRICS.flatMap((m) => [
        [m.key, selectedDetails.map((d) => m.cells?.(d.metrics ?? {}) ?? null)],
        ...(m.components ?? []).map((c) => [c.key, selectedDetails.map((d) => c.cells?.(d.metrics ?? {}) ?? null)]),
      ]),
      ['injection_probe', selectedDetails.map((d) => probeCells(d))],
    ]),
    // 선정 규칙을 지금 결과에 적용한 경로 — 표지가 `이 규칙을 적용하면 X`로 인쇄한다(사람이 적어 넣으면 다시 잴 때 낡는다)
    selection: applySelectionRule(selectedDetails, (d) => model(d.id)),
    composite: withheld ? null : {
      current: currentScores,
      neutral: neutralScores,
      usage: usageScores,
      // 종합 순위 장의 동률 — 묶인 실행 id(막대 이름에 표시)와 지금 가중치에서 이어져 있지 않아 생략했는지.
      // `groups`는 표지가 쓴다 — 고른 모델이 동률 묶음에 들어 있으면 결론 옆에 그 사실을 적어야 두 면이 다른 말을 하지 않는다
      ties: { tied: ties.tied, omitted: ties.omitted, groups: ties.groups },
      // 종합 순위 장의 기준선 참조선 — 리포트는 고정된 문서라 베이스라인을 항상 정규화 풀에 넣는다
      baseline: baseRun
        ? {
            current: weightedScore(normalized, weights, BASELINE_ROW_ID),
            neutral: weightedScore(normalized, neutralW, BASELINE_ROW_ID),
            usage: weightedScore(normalized, usageW, BASELINE_ROW_ID),
          }
        : null,
    },
    // 카테고리 평균이 아니라 **가중 기여도** — 합이 곧 종합 점수라 앞 장의 막대와 길이가 맞는다
    category_contribution: withheld ? null : contribution,
    metrics: METRICS.map((m) => ({
      key: m.key,
      label: m.label,
      // 묶음 — 리포트가 `채점되는 지표인가`를 가르는 데 쓴다(속도·리소스·안정성은 계측이지 채점이 아니다)
      category: m.category,
      unit: m.unit,
      direction: m.direction,
      kind: m.kind,
      // 이 지표 값이 나오는 실행 항목 — 측정값 표 칸에 그 항목의 채점기 기록 없음을 적는 데 쓴다
      items: m.items ?? [],
      // 하위 값에서 만든 상위 값은 만드는 법을 함께 적는다 — 읽는 사람이 하위 값에서 역산하게 두지 않는다
      derivation: m.components ? `${m.components.map((c) => c.label).join(' · ')}의 평균` : null,
      // 값을 낸 조건이 이름에 붙어야 하는 지표 — 긴 컨텍스트 두 지표의 `압축 끔`
      condition: m.condition ?? null,
      // 측정값 장 — 정규화 전 원본 값을 단위까지 붙인 문자열로. 상태가 붙은 값은 상태 문구로.
      raw: Object.fromEntries(selectedDetails.map((d) => [d.id, cellText(d, m)])),
      status: Object.fromEntries(selectedDetails.map((d) => [d.id, metricState(d, m).outcome])),
      normalized: Object.fromEntries(selectedDetails.map((d) => [d.id, normalized[m.key]?.[d.id] ?? null])),
      baseline_raw: baseRun ? cellText(baseRun, m, { baseline: true }) : null,
      baseline_status: baseRun ? metricState(baseRun, m, { baseline: true }).outcome : null,
      baseline_normalized: baseRun ? (normalized[m.key]?.[BASELINE_ROW_ID] ?? null) : null,
      // 채점 칸 수 — 값을 쓰지 않는 칸(능력 부재·측정 안 됨·비교 제외)에는 싣지 않는다
      n: Object.fromEntries(selectedDetails.map((d) => [d.id, metricCells(d, m)])),
      baseline_n: baseRun ? metricCells(baseRun, m, { baseline: true }) : null,
      sub_rows: subRows(m, selectedDetails, baseRun),
    })),
    reference_rows: referenceRows(selectedDetails),
    // 종합 점수를 만든 무게 그대로 — 지표마다(강등을 적용한 뒤). 카테고리 몫과 배율은 리포트가 이것에서 센다
    weights: withheld ? null : { current: weights, neutral: neutralW, usage: usageW },
    categories: CATEGORIES.map((c) => ({ id: c.id, label: c.label })),
    commercial: commercialComparison(selectedDetails, baseRun, normalized),
    // 각 차트 아래의 결과 설명 — 규칙으로 만든 사실 문장. 모델에게 쓰게 하지 않는다.
    explanations: {
      ranking: withheld ? [] : [...rankingSentences(selectedDetails, currentScores, ties), ...ties.sentences],
      breakdown: withheld ? [] : breakdownSentences(selectedDetails, contribution),
      dots: metricSplitSentences(selectedDetails, normalized),
      weights: withheld
        ? []
        : [
            weightSensitivityConclusion(flips),
            // 일관성 값이 없는 비교(`not_applicable`)는 뺄 것이 없어 적지 않는다
            ...(consistencyExclusion(demotion) && demotion?.status !== 'not_applicable'
              ? [`두 프리셋 모두 일관성을 순위에서 뺀 가중치(일관성 0 — ${consistencyExclusion(demotion)}, 나머지 재정규화)로 계산했다.`]
              : []),
          ],
      variance: varianceSentences(selectedDetails),
    },
    variance: {
      // 속도 전용. 중심은 저장된 대표값인 **중앙값**이다(평균은 옛 실행에서 복원할 수 없다).
      performance: Object.fromEntries(
        selectedDetails.map((d) => [
          d.id,
          {
            median: d.metrics?.tok_per_sec ?? null,
            stdev: d.metrics?.tok_per_sec_stdev ?? null,
            samples: d.metrics?.tok_per_sec_samples ?? null,
          },
        ]),
      ),
    },
    // 일관성/재현성 상세 장은 백엔드가 결과 파일을 직접 읽어 만든다(파일 상단 주석)
    consistency_run_ids: selectedDetails.map((d) => d.id),
    notes: notes ? Object.fromEntries(Object.entries(notes.notes).map(([id, e]) => [id, e.note])) : {},
    // 불릿별 인용 검증 표식 — 리포트도 실패한 불릿을 빼지 않고 표식을 달아 싣는다
    notes_verification: notes
      ? Object.fromEntries(Object.entries(notes.notes).map(([id, e]) => [id, e.verification ?? null]))
      : {},
  }
}
