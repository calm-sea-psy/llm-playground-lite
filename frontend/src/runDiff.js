// 측정 기계 — 기록이 없는 옛 결과는 주 기계로 읽는다(주 기계는 설정 없이 `main`으로 기록된다).
export const MAIN_MACHINE = 'main'
// 문서 길이 — 문서를 주고 묻는 세트가 읽은 판. 실행 하나에 하나다.
export const SHORT_DOCUMENTS = '짧은 문서'
export const LONG_DOCUMENTS = '긴 문서'

/** 기록이 없는 옛 실행은 긴 판이 생기기 전에 잰 것이라 짧은 판이다 — `기록 없음`으로 두면 옛 실행 전부가 새 실행과 대조되지 않는다. */
export function documentLength(config) {
  return config?.document_length ?? SHORT_DOCUMENTS
}
// 호스트 이름과 하드웨어 사양은 `측정 기계` 딱지를 되짚기 위한 기록이라 조건 비교에서 뺀다 — 기계가 다르다는 것은
// `측정 기계`가 말하고, 사양까지 대조하면 같은 차이가 두 줄로 뜬다. 모델 식별값은 모델마다 당연히 달라(모델끼리 대조하면
// 늘 `조건 다름`이 된다) 기록만 한다. Python·패키지 버전도 기록이다 — 채점이 바뀌면 판정기 버전이 따로 말한다.
// 전원 상태도 기록이다 — 속도·전력 값을 앞선 실행과 나란히 놓는 리포트 표에 함께 찍고, 다르다고 경고하는 문턱은 아직 두지 않았다
const RECORD_ONLY_KEYS = new Set(['measurement_host', 'hardware', 'model_identity', 'software', 'power'])
// 전원 기록 가운데 **GPU 전력 한계**만 따로 대조한다 — 한계가 다르면 속도·안정성은 다른 환경에서 잰 값이다(AC/배터리·잔량은
// 기록이라 대조하지 않는다). config에 없는 파생 키라 비교하는 곳이 키 목록에 직접 넣는다
const DERIVED_KEYS = { gpu_power_limit: (cfg) => gpuPowerLimits(cfg.power) }

/** 채점이 기대는 소프트웨어 — Python과 선언한 패키지의 설치 판. 같은 답이어도 판정 라이브러리가 바뀌면 점수가 달라질 수 있다.
 * 설치되지 않은 패키지는 `없음`이라 적는다(선언과 설치가 다른 것도 기록이다). */
export function softwareText(software) {
  if (!software) return '기록 없음 (이 기록을 남기기 전 측정)'
  const packages = Object.entries(software.packages ?? {})
    .map(([name, version]) => `${name} ${version ?? '없음'}`)
    .join(' · ')
  return `Python ${software.python ?? '기록 없음'}${packages ? ` · ${packages}` : ''}`
}

/** 측정한 코드의 커밋 — `df242ca` 또는 손댄 것이 있으면 `df242ca+dirty`. 그 기록 이전 실행은 `기록 없음`. */
export function commitText(commit) {
  if (!commit?.sha) return '기록 없음 (이 기록을 남기기 전 측정)'
  return `${commit.sha}${commit.dirty ? '+dirty' : ''}`
}

/** 그 실행에 실제로 걸려 있던 GPU 전력 한계(없으면 설정한 한계) — 없으면 undefined(기록 없음과 `GPU 없음`을 가르지 않는다). */
function gpuPowerLimits(power) {
  const gpus = power?.gpus
  if (!gpus?.length) return undefined
  const limits = gpus.map((g) => g.enforced_power_limit_watts ?? g.power_limit_watts).filter((w) => w != null)
  return limits.length ? limits.slice().sort((a, b) => a - b) : undefined
}
// 긴 컨텍스트 압축 켬 경로(참고 행)의 요약기 조건 — 점수에 드는 값에는 걸리지 않아 모델끼리 대조해 표지에 경고하지 않는다.
// 실행마다 요약기가 무엇이었는지는 리포트가 그 참고 행의 각주에 적는다. 직전 실행과 달라진 조건 한 줄에는 그대로 뜬다
const REFERENCE_ONLY_KEYS = new Set(['summarizer_model', 'summarizer_sampling'])
// 도구 응답(고정값/실시간)은 도구를 부르는 항목에만, 긴 컨텍스트 시나리오마다 재로드는 긴 컨텍스트에만 걸린다 — 모든 항목에
// 대조하면 `도구 응답 — 폐쇄형, 환각 …`처럼 영향이 없는 지표까지 조건 다름으로 뜬다. 키마다 항목 목록을 두지 않는다는 원칙의
// 예외라 이 두 키만 둔다
const ITEM_SCOPED_KEYS = {
  tool_responses: new Set(['tool_calling', 'injection_probe']),
  long_context_reload: new Set(['long_context']),
  // 전력 한계는 속도·안정성을 재는 항목에만 걸린다 — 품질·보안 답은 전력 한계로 달라지지 않는다(실측: 45W와 95W에서 같은 답)
  gpu_power_limit: new Set(['model_load', 'short_probe', 'context_2000', 'context_4000', 'context_8000']),
  // 문서 방어는 문서를 함께 보내는 지표에만 걸린다 — 문서가 없는 호출에는 붙지 않아 조건이 그대로다
  document_guard: new Set(['closed_qa', 'key_coverage', 'hallucination', 'injection_indirect']),
  // TTFT 재는 법은 속도 항목에만 걸린다 — 품질 답은 캐시로 달라지지 않는다
  ttft_method: new Set(['short_probe', 'context_2000', 'context_4000', 'context_8000']),
}
// TTFT — 기록이 없는 실행은 같은 글을 반복해 **캐시가 맞은** 값이다(그 값은 찬 캐시 값과 나란히 읽으면 안 된다)
export const TTFT_CACHED = '캐시 맞음(옛 방식)'
// 문서 방어 — 기록이 없는 실행은 방어 없이 쟀다(방어를 넣기 전이다)
export const NO_DOCUMENT_GUARD = '없음'
// 도구 응답 — 기록이 없는 옛 실행은 도구를 실시간으로 실행했다(고정값이 생기기 전이다)
export const TOOL_RESPONSES_LIVE = 'live'
const TOOL_RESPONSES_TEXT = { fixed: '고정', live: '실시간' }
// 이 기록을 남기기 전에 잰 실행 — 사양·서버 버전은 실행을 시작할 때만 읽을 수 있어 나중에 채울 수 없다
const ENVIRONMENT_UNRECORDED = '기록 없음 (이 기록을 남기기 전 측정)'

export function machineLabel(machine) {
  const id = machine ?? MAIN_MACHINE
  return id === MAIN_MACHINE ? '주 기계' : id
}

const gib = (bytes) => `${(bytes / 1024 ** 3).toFixed(1)}GB`

/** 표지에 한 줄로 적는 기계 사양 — **CPU·RAM·GPU만**이다. 드라이버·OS·CUDA는 조건 상세의 몫이고,
 * 표지는 한 쪽이라 `무엇으로 잰 값인가`에 답하는 만큼만 싣는다. 기록이 없으면 `null`이라 그 줄 자체가 안 나간다
 * — 측정하지 않은 것을 표지에 적지 않는다. */
export function machineSpecText(hw) {
  if (!hw) return null
  const parts = []
  if (hw.cpu) parts.push(hw.cpu)
  if (hw.ram_bytes) parts.push(`RAM ${gib(hw.ram_bytes)}`)
  if (hw.gpus?.length) parts.push(hw.gpus.map((g) => `${g.name}${g.vram_bytes ? ` ${gib(g.vram_bytes)}` : ''}`).join(', '))
  else if (hw.gpus) parts.push('NVIDIA GPU 없음')
  return parts.length ? parts.join(' · ') : null
}

/** 측정한 기계의 사양 한 줄 — 못 읽은 칸은 못 읽었다고 적는다(`GPU 없음`과 `GPU 못 읽음`은 다른 말이다). */
export function hardwareText(hw) {
  if (!hw) return ENVIRONMENT_UNRECORDED
  const cpu = hw.cpu ? `${hw.cpu}${hw.logical_cpus ? ` (논리 ${hw.logical_cpus}개)` : ''}` : 'CPU 못 읽음'
  const ram = hw.ram_bytes ? `RAM ${gib(hw.ram_bytes)}` : 'RAM 못 읽음'
  const gpus =
    hw.gpus == null
      ? 'GPU 못 읽음 (nvidia-smi)'
      : hw.gpus.length === 0
        ? 'NVIDIA GPU 없음'
        : hw.gpus
            .map((g) => `${g.name}${g.vram_bytes ? ` ${gib(g.vram_bytes)}` : ''}${g.driver ? ` (드라이버 ${g.driver})` : ''}`)
            .join(', ')
  // OS·GPU 백엔드는 나중에 기록하기 시작했다 — 없는 실행은 그렇게 적는다(지금 기계의 값으로 채우면 측정 시 값으로 읽힌다)
  const os = hw.os ?? 'OS 기록 없음'
  const cuda = 'cuda' in hw ? `CUDA ${hw.cuda ?? '못 읽음'}` : 'GPU 백엔드 기록 없음'
  return [cpu, ram, gpus, os, cuda].join(' · ')
}

/** 실행을 시작할 때의 전원 — AC/배터리와 GPU 전력 한계(실제로 걸린 한계, 없으면 설정한 한계). 못 읽은 칸은 못 읽었다고 적는다. */
export function powerText(power) {
  if (!power) return ENVIRONMENT_UNRECORDED
  const source =
    power.ac_power === true
      ? 'AC 연결'
      : power.ac_power === false
        ? `배터리${power.battery_percent != null ? ` ${power.battery_percent}%` : ''}`
        : 'AC/배터리 못 읽음'
  const watts = (w) => `${Math.round(w)}W`
  const gpus =
    power.gpus == null
      ? 'GPU 전력 한계 못 읽음 (nvidia-smi)'
      : power.gpus.length === 0
        ? 'NVIDIA GPU 없음'
        : power.gpus
            .map((g) => {
              // `한계 45W (기본 80W)`는 45W가 미달이라는 뜻으로 읽힌다 — 이 기계에서 실제로 걸린 상한이 무엇인지 적는다
              const limit = g.enforced_power_limit_watts ?? g.power_limit_watts
              const name = power.gpus.length > 1 ? `${g.name} ` : ''
              const base = g.default_power_limit_watts != null ? ` (GPU 기본값 ${watts(g.default_power_limit_watts)})` : ''
              return `${name}이 환경의 GPU 전력 한계 ${limit != null ? watts(limit) : '못 읽음'}${base}`
            })
            .join(', ')
  return `${source} · ${gpus}`
}

/** 측정 경로의 도구 응답 — 고정이면 세트 옆에 기록해 둔 실제 응답, 실시간이면 도구를 그때 실행했다. 기록이 없는 옛 실행은 실시간이다. */
export function toolResponsesText(config) {
  const value = conditionValue(config ?? {}, 'tool_responses')
  return value === 'fixed'
    ? '고정 — 기록해 둔 실제 응답(현재 시각·공휴일·대기질·설치된 모델). 실시간으로 잰 실행의 Tool-calling 값과 나란히 읽지 않는다'
    : `실시간 — 도구를 그때 실행${config && 'tool_responses' in config ? '' : '(기록 이전 실행)'}`
}

/** 긴 컨텍스트의 시작 상태 — 앞선 호출이 남긴 상태가 같은 입력의 답을 바꿔, 시나리오마다 모델을 다시 올렸는지가 조건이다.
 * 기록이 없는 옛 실행은 앞 시나리오에 이어 돌았다. */
export function longContextReloadText(config) {
  if (conditionValue(config ?? {}, 'long_context_reload')) return '시나리오마다 모델을 다시 올림 — 켬·끔 두 경로와 2회차가 같은 상태에서 시작'
  return `앞 시나리오에 이어서${config && 'long_context_reload' in config ? '' : '(기록 이전 실행)'}`
}

/** 2회차가 건너뛰는 항목 바로 뒤에서 두 바퀴 모두 모델을 다시 올렸나 — 바로 앞 호출이 남긴 상태가 답을 바꿔, 올리지 않은 실행은
 * 건너뛴 자리 뒤 문항의 시작 상태가 다르다(1회차 점수에도 걸린다). 기록이 없는 옛 실행은 올리지 않았다. */
export function reloadAfterSkippedText(config) {
  if (conditionValue(config ?? {}, 'reload_after_skipped')) return '켬 — 2회차가 건너뛰는 항목 바로 뒤에서 두 바퀴 모두 모델을 다시 올림'
  return `끔${config && 'reload_after_skipped' in config ? '' : '(기록 이전 실행)'}`
}

/** Ollama 서버 버전 — 키가 없으면 그 기록 이전 실행, null이면 읽으려 했지만 못 읽었다. */
export function ollamaVersionText(config) {
  if (!('ollama_version' in (config ?? {}))) return ENVIRONMENT_UNRECORDED
  return config.ollama_version ?? '못 읽음'
}

/** 실제로 보낸 샘플링 — null은 그 경로가 샘플링 값을 보내지 않았다는 기록이다(프로바이더 기본값으로 돌았다). */
export function samplingText(s) {
  if (s === null) return '보내지 않음 (프로바이더 기본값)'
  if (!s || Object.keys(s).length === 0) return '기록 없음'
  return Object.entries(s)
    .map(([k, v]) => `${k}=${v}`)
    .join(', ')
}

// 일관성 전용 출력 상한 — 기록이 없는 옛 실행은 품질 지표 상한으로 쟀다
export function consistencyNumPredict(cfg) {
  return cfg.consistency_num_predict ?? cfg.quality_num_predict
}

function conditionValue(cfg, key) {
  if (DERIVED_KEYS[key]) return DERIVED_KEYS[key](cfg)
  if (key === 'measurement_machine') return cfg[key] ?? MAIN_MACHINE
  if (key === 'document_length') return documentLength(cfg)
  if (key === 'consistency_num_predict') return consistencyNumPredict(cfg)
  if (key === 'tool_responses') return cfg[key] ?? TOOL_RESPONSES_LIVE
  if (key === 'long_context_reload') return cfg[key] ?? false
  if (key === 'document_guard') return cfg[key] ?? NO_DOCUMENT_GUARD
  if (key === 'ttft_method') return cfg[key] ?? TTFT_CACHED
  if (key === 'reload_after_skipped') return cfg[key] ?? false
  return cfg[key]
}

const CONDITION_LABELS = {
  num_ctx: 'num_ctx',
  num_predict: '속도 탐침 num_predict',
  sampling: '샘플링',
  think: 'thinking',
  warmup_count: '워밍업 횟수',
  repeat_count: '반복 횟수',
  timeout_short_sec: '짧은 타임아웃',
  timeout_long_sec: '긴 타임아웃',
  context_stage_tokens: '컨텍스트 단계',
  quality_num_predict: '품질 num_predict',
  quality_timeout_sec: '품질 타임아웃',
  consistency_num_predict: '일관성 num_predict',
  consistency_sampling: '일관성 샘플링',
  summarizer_model: '요약 압축 모델',
  summarizer_sampling: '요약 샘플링',
  document_length: '문서 길이',
  document_guard: '문서 방어',
  ttft_method: 'TTFT 재는 법',
  measurement_machine: '측정 기계',
  ollama_version: 'Ollama 버전',
  tool_responses: '도구 응답',
  long_context_reload: '긴 컨텍스트 시나리오마다 재로드',
  reload_after_skipped: '건너뛴 항목 뒤 재로드',
  gpu_power_limit: 'GPU 전력 한계',
}

export function conditionLabel(key) {
  return CONDITION_LABELS[key] ?? key
}

/** 조건 값을 사람이 읽는 문구로 — 값이 없으면 `기록 없음`(추측으로 채우지 않는다). */
export function conditionValueText(key, value) {
  if (value === null && (key === 'sampling' || key === 'consistency_sampling')) return samplingText(null)
  if (value === null && key === 'ollama_version') return '못 읽음'
  if (value === undefined || value === null) return '기록 없음'
  if (key === 'measurement_machine') return machineLabel(value)
  if (key === 'tool_responses') return TOOL_RESPONSES_TEXT[value] ?? String(value)
  if (key === 'gpu_power_limit') return value.map((w) => `${w}W`).join('·')
  if (key === 'document_guard') return value === NO_DOCUMENT_GUARD ? value : `v${value.version} (${value.sha256})`
  // 커밋은 리포트 어디서나 같은 꼴로 적는다(`ee19e75` · `ee19e75+dirty`) — 날 것(`sha=…, dirty=false`)으로
  // 두면 한 줄이 두 배로 길어지고, 같은 값이 장마다 다른 꼴로 인쇄된다
  if (key === 'tool_commit') return commitText(value)
  if (typeof value === 'boolean') return value ? '켬' : '끔'
  if (Array.isArray(value)) return value.join(' / ')
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([k, v]) => `${k}=${v}`)
      .join(', ')
  }
  return String(value)
}

/** 실행 설정 차이 — 실행 종류·시스템 프롬프트. 지표가 아니라 실행 전체에 걸린다. */
function runSettingDiff(prev, next) {
  // 선정용 실행은 프롬프트가 없고, `프롬프트 실험`은 그 자체가 조건 불일치 사유다
  const prevType = prev.run_type || 'selection'
  const nextType = next.run_type || 'selection'
  if (prevType !== nextType) return ['실행 종류(선정 / 프롬프트 실험)']
  if ((prev.system_prompt_meta?.sha256 ?? null) !== (next.system_prompt_meta?.sha256 ?? null)) return ['시스템 프롬프트']
  return []
}

// 두 실행의 조건(시스템 프롬프트 + config 스냅샷)을 비교해 `{ changed, unrecorded }`를 낸다 — 달라진 키와, 직전 실행에
// 기록이 없어 대조하지 못한 키(그 기록을 남기기 전 실행). 둘을 합치면 새로 기록하기 시작한 조건이 `달라졌다`로 뜬다.
// 성능 테스트의 "직전 실행과 달라진 조건 한 줄"이 쓴다 — 여러 실행을 견주는
// 비교 화면·리포트는 부모 조건끼리가 아니라 지표별 출처 조건을 대조한다(`metricConditionMismatches`).
export function diffRunConditions(prev, next) {
  if (!prev) return null
  const prevCfg = prev.config || {}
  const nextCfg = next.config || {}
  // 이 기능 이전에 저장된 실행은 config가 통째로 비어 있다 — 그걸 "전부
  // 바뀜"으로 보여주면 의미 없는 경고만 늘어난다. 비교 자체를 생략한다.
  if (Object.keys(prevCfg).length === 0) return null

  const changed = runSettingDiff(prev, next)
  const unrecorded = []
  const keys = new Set([...Object.keys(prevCfg), ...Object.keys(nextCfg), ...Object.keys(DERIVED_KEYS)])
  for (const key of keys) {
    if (RECORD_ONLY_KEYS.has(key)) continue
    const [before, after] = [conditionValue(prevCfg, key), conditionValue(nextCfg, key)]
    if (before === undefined && after !== undefined) unrecorded.push(key)
    else if (JSON.stringify(before) !== JSON.stringify(after)) changed.push(key)
  }
  return { changed, unrecorded }
}

/** 비교에 든 실행들의 실행 설정 차이 — 첫 실행을 기준으로 시스템 프롬프트가 다른 실행. 실행 종류 차이는
 * `promptExperimentWarnings`가 따로 알린다. `[{ runId, otherId, changes }]`. */
export function runSettingMismatches(details) {
  if (details.length < 2) return []
  const [first, ...rest] = details
  return rest
    .map((r) => ({ runId: first.id, otherId: r.id, changes: runSettingDiff(first, r).filter((c) => c === '시스템 프롬프트') }))
    .filter((m) => m.changes.length > 0)
}

/** **지표(실행 항목)마다 그 값을 실제로 잰 실행의 조건끼리** 대조한다 — 부모 조건끼리 비교하면 한 모델은 재실행이고
 * 다른 모델은 아닐 때 둘 다 부모 조건으로 보여 차이가 안 잡힌다. 합친 뷰의 `provenance[항목].config`가 그 출처다.
 *
 * - 조건 키는 항목과 무관하게 **전부** 본다. 어느 키가 어느 항목에 영향을 주는지 목록을 따로 두면 빠뜨린 키가
 *   조용히 사라진다 — 놓쳤을 때 보이는 쪽(관련 없는 키까지 뜨는 쪽)으로 기운다.
 * - 같은 키에서 같은 값 배치로 다른 항목들은 한 줄로 묶는다.
 * - 모르는 것을 다르다고 적지 않는다: 출처에 조건 기록이 통째로 없는 항목은 `unrecorded`, 키 하나만 기록이 없고
 *   나머지 실행끼리는 같은 경우는 `unrecordedKeys`로 따로 낸다(그 기능 이전 실행). 기록된 값끼리 다르면 불일치다.
 *
 * `{ mismatches: [{ key, values: [{ runId, value }], items }], unrecorded: [{ runId, items }],
 *    unrecordedKeys: [{ key, runIds, items }] }` */
export function metricConditionMismatches(details) {
  const byItem = new Map()
  for (const d of details) {
    for (const item of d.items ?? []) {
      const source = d.provenance?.[item.id]
      const config = (source ? source.config : d.config) || {}
      if (!byItem.has(item.id)) byItem.set(item.id, { label: item.label || item.id, entries: [] })
      byItem.get(item.id).entries.push({ runId: d.id, config })
    }
  }
  const groups = new Map()
  const keyGaps = new Map()
  const unrecorded = new Map()
  // 대조까지 간 항목 수 — 한 키가 이만큼에 다 걸리면 항목을 세지 않고 `전 항목`이라 적는다
  let compared = 0
  const addTo = (map, signature, make, label) => {
    if (!map.has(signature)) map.set(signature, make())
    map.get(signature).items.push(label)
  }
  for (const [itemId, { label, entries }] of byItem.entries()) {
    if (entries.length < 2) continue
    const recorded = entries.filter((e) => Object.keys(e.config).length > 0)
    for (const e of entries) {
      if (Object.keys(e.config).length === 0) unrecorded.set(e.runId, [...(unrecorded.get(e.runId) ?? []), label])
    }
    if (recorded.length < 2) continue
    compared += 1
    const keys = new Set([...recorded.flatMap((e) => Object.keys(e.config)), ...Object.keys(DERIVED_KEYS)])
    for (const key of keys) {
      if (RECORD_ONLY_KEYS.has(key) || REFERENCE_ONLY_KEYS.has(key)) continue
      if (ITEM_SCOPED_KEYS[key] && !ITEM_SCOPED_KEYS[key].has(itemId)) continue
      const values = recorded.map((e) => ({ runId: e.runId, value: conditionValue(e.config, key) }))
      const known = values.filter((v) => v.value !== undefined)
      // 파생 키는 아무 실행도 그 기록이 없으면 대조할 것 자체가 없다 — `기록 없음` 각주를 내지 않는다(그 기록 이전 실행들이다)
      if (DERIVED_KEYS[key] && known.length === 0) continue
      if (new Set(known.map((v) => JSON.stringify(v.value))).size >= 2) {
        addTo(groups, JSON.stringify([key, values]), () => ({ key, values, items: [] }), label)
      } else if (known.length < values.length) {
        const runIds = values.filter((v) => v.value === undefined).map((v) => v.runId)
        addTo(keyGaps, JSON.stringify([key, runIds]), () => ({ key, runIds, items: [] }), label)
      }
    }
  }
  // `everyItem`은 셈이지 판단이 아니다 — 문장을 만드는 쪽이 항목을 나열할지 `전 항목`이라 적을지 고른다
  const mark = (g) => ({ ...g, everyItem: compared > 1 && g.items.length === compared })
  return {
    mismatches: [...groups.values()].map(mark),
    unrecorded: [...unrecorded.entries()].map(([runId, items]) => ({ runId, items })),
    unrecordedKeys: [...keyGaps.values()].map(mark),
  }
}

export const ACROSS_RUNS_HEAD = '모델끼리 조건 다름'
export const WITHIN_RUN_MACHINE_HEAD = '한 모델 안에서 기계 섞임'

/** 비교 화면과 리포트 표지가 함께 쓰는 조건 문장 — 두 곳의 판단이 갈리지 않게 한 함수에서 만든다.
 * - `warnings` — 실행 설정 차이(프롬프트 실험·시스템 프롬프트), 실행 사이 조건 차이(`모델끼리 조건 다름`),
 *   한 실행 안의 기계 섞임(`한 모델 안에서 기계 섞임`). 같은 기계 차이가 두 확인에 다 걸리면 두 줄이 나온다 —
 *   확인한 것이 달라 한쪽이 다른 쪽을 대신하지 않으므로 머리말로 갈라 둔다.
 * - `footnotes` — 기록이 없어 대조하지 못한 것.
 * - `facts` — 한 실행 안의 기계 외 섞임(`intended`·`changed`). 경고가 아니라 사실이라 따로 낸다: `[{ runId, itemId, text }]`.
 * `nameOf(runId)`로 이름을 붙인다(화면은 모델 이름, 리포트는 별칭 자리표시자). 기준선은 대조하지 않는다 —
 * 다른 제공자라 조건이 같아질 수 없고, 기준선과의 차이는 `프롬프트 실험` 경고와 기준선 상태 줄이 맡는다. */
export function conditionMismatchLines(details, { hasBaseline = false, nameOf = (runId) => runId } = {}) {
  const warnings = [
    ...promptExperimentWarnings(details, { hasBaseline, nameOf: (d) => nameOf(d.id) }),
    ...runSettingMismatches(details).map((m) => `${nameOf(m.runId)} vs ${nameOf(m.otherId)}: 시스템 프롬프트가 다르다`),
  ]
  const { mismatches, unrecorded, unrecordedKeys } = metricConditionMismatches(details)
  warnings.push(...mismatches.map((m) => `${ACROSS_RUNS_HEAD} · ${conditionMismatchText(m, nameOf)}`))
  const notes = withinRunConditionNotes(details)
  warnings.push(
    ...notes
      .filter((n) => n.kind === 'machine')
      .map((n) => `${WITHIN_RUN_MACHINE_HEAD} · ${nameOf(n.runId)}: ${n.label} — ${withinRunNoteText(n)}`),
  )
  const footnotes = [
    ...unrecorded.map((u) => `${nameOf(u.runId)}: 측정 조건 기록 없음 — 대조하지 못한 항목: ${u.items.join(', ')}`),
    ...unrecordedKeys.map(
      (g) => `${conditionLabel(g.key)} — ${whereText(g)}: ${g.runIds.map(nameOf).join('·')} 기록 없음(그 기능 이전 실행)이라 대조하지 못함`,
    ),
  ]
  const facts = notes
    .filter((n) => n.kind !== 'machine')
    .map((n) => ({ runId: n.runId, itemId: n.itemId, label: n.label, text: withinRunNoteText(n) }))
  return { warnings, footnotes, facts }
}

/** 항목 자리 — 대조한 항목에 다 걸렸으면 세어 적는다. 열아홉 개를 늘어놓은 줄은 표지 한 쪽을 넘겨 리포트 조립을
 * 실패시키고, 다 걸렸다는 사실 자체는 이름을 하나도 잃지 않고 `전 항목`으로 말할 수 있다(항목이 하나뿐이면
 * 그 이름이 `전 항목`보다 많은 것을 말하므로 그대로 적는다 — `everyItem`이 그 경우를 이미 뺀다). */
function whereText({ items, everyItem }) {
  return everyItem ? `전 항목(${items.length}개)` : items.join(', ')
}

/** 조건 불일치 한 줄 — `일관성 num_predict — 일관성/재현성: A·B 2048 / C 512`. 같은 값인 실행은 모은다. */
export function conditionMismatchText({ key, values, items, everyItem }, nameOf) {
  const byValue = new Map()
  for (const { runId, value } of values) {
    const text = conditionValueText(key, value)
    byValue.set(text, [...(byValue.get(text) ?? []), nameOf(runId)])
  }
  const spread = [...byValue.entries()].map(([text, names]) => `${names.join('·')} ${text}`).join(' / ')
  return `${conditionLabel(key)} — ${whereText({ items, everyItem })}: ${spread}`
}

/** `프롬프트 실험` 실행이 비교에 끼었는가 — 후보끼리든 기준선과든 경고한다. 기준선은 프롬프트를 쓰지
 * 않으므로 이 경고가 그 비교의 유일한 방어선이다("기본 제외"는 사용자가 직접 고르면 풀린다). */
export function promptExperimentWarnings(details, { hasBaseline = false, nameOf = (d) => d.model } = {}) {
  return details
    .filter((d) => d?.run_type === 'prompt_experiment')
    .map(
      (d) =>
        `${nameOf(d)}: 프롬프트 실험 실행(${d.system_prompt_meta?.title ?? d.system_prompt_meta?.name ?? '프롬프트'})` +
        (hasBaseline ? ' — 기준선은 프롬프트 없이 쟀다' : ' — 선정용 실행과 조건이 다르다'),
    )
}

const CONSISTENCY_ITEM = 'consistency'
const MACHINE_KEY = 'measurement_machine'

/** **한 실행 안의 조건 섞임** — 지표 재실행을 합친 실행에서, 재실행으로 온 항목마다 원래(부모) 실행과 달라진 조건을
 * **차이마다 하나씩** 가른다. 실행 사이 대조(`metricConditionMismatches`)와는 다른 것을 확인한다 — 저것은 모델끼리
 * 같은 조건으로 잰 값인지, 이것은 한 모델의 지표들이 같은 조건으로 잰 값인지다(네 모델이 똑같이 섞이면 저것은 못 잡는다).
 *
 * - `machine` — 측정 기계가 다르다. 기계에 딸린 값이 섞일 수 있고 섞이면 구분할 방법이 없어 **경고**다. 지표를 가리지 않는다.
 * - `intended` — 일관성 지표에서 **달라진 조건이 전용 상한뿐**이다. 전용 상한은 규칙으로 만든 예외라 코드가 의도를 안다.
 *   다른 차이가 하나라도 섞이면 이 딱지를 붙이지 않는다.
 * - `changed` — 그 밖의 차이. 의도를 주장하지 않는 사실이다.
 *
 * 부모·재실행 어느 한쪽에 조건 기록이 없으면(키 하나든 통째든) 대조하지 않는다 — 모르는 것을 달라졌다고 적지 않는다.
 * `[{ runId, itemId, label, kind, key, from, to }]` */
export function withinRunConditionNotes(details) {
  const out = []
  for (const d of details) {
    const parent = d.config || {}
    if (!d.provenance || Object.keys(parent).length === 0) continue
    const labels = Object.fromEntries((d.items ?? []).map((it) => [it.id, it.label || it.id]))
    for (const [itemId, source] of Object.entries(d.provenance)) {
      const config = source.config || {}
      if (source.run_id === d.id || Object.keys(config).length === 0) continue
      const keys = [...new Set([...Object.keys(parent), ...Object.keys(config)])].filter(
        (k) => !RECORD_ONLY_KEYS.has(k) && (!ITEM_SCOPED_KEYS[k] || ITEM_SCOPED_KEYS[k].has(itemId)),
      )
      const changed = keys.filter((key) => {
        const [from, to] = [conditionValue(parent, key), conditionValue(config, key)]
        return from !== undefined && to !== undefined && JSON.stringify(from) !== JSON.stringify(to)
      })
      const note = (kind, key) => ({
        runId: d.id,
        itemId,
        label: labels[itemId] ?? itemId,
        kind,
        key,
        from: conditionValue(parent, key),
        to: conditionValue(config, key),
      })
      if (itemId === CONSISTENCY_ITEM && changed.length === 1 && changed[0] === 'consistency_num_predict') {
        out.push(note('intended', changed[0]))
        continue
      }
      for (const key of changed) out.push(note(key === MACHINE_KEY ? 'machine' : 'changed', key))
    }
  }
  return out
}

/** 실행 안 섞임 한 건의 문구(이름·항목 없이) — 화면은 앞에 `모델: 항목 —`을, 리포트는 출처 각주 뒤에 붙인다. */
export function withinRunNoteText({ kind, key, from, to }) {
  if (kind === 'intended') return `전용 상한 ${conditionValueText(key, to)}로 다시 쟀다 (의도한 것)`
  if (kind === 'machine') return `원래 실행과 다른 기계에서 다시 쟀다 (${conditionValueText(key, from)} → ${conditionValueText(key, to)})`
  return `원래 실행과 다른 조건으로 다시 쟀다 (${conditionLabel(key)} ${conditionValueText(key, from)} → ${conditionValueText(key, to)})`
}
