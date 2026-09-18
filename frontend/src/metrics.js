// 지표 정의(원본 값 그대로, 정규화 없음). BenchmarkPage의 실행
// 진행/상세, 기준선 패널, MetricsDisplay가 공유한다(한 컴포넌트에
// 갇혀 있던 걸 여러 컴포넌트로 쪼개면서 분리했다).

export const pctFmt = (v) => `${(v * 100).toFixed(0)}%`

/** 부하 시 tok/s 유지율이 100%를 넘으면 그 값은 쓸 수 없다 — 부하 없는 기준 측정(짧은 탐침)이 부하 측정보다 느렸다는 뜻이라,
 * 부하를 견딘 정도가 아니라 기준이 눌린 정도를 재고 있다(실측: 전력 한계가 낮아 클럭이 오르내린 실행). 값은 남기고 점수에서만 뺀다.
 * 판정을 한 곳에 두어 화면 표와 리포트가 갈리지 않게 한다(`scoring.js`가 이 함수를 쓴다). */
export function retentionInvalidCause(metrics) {
  const ratio = metrics?.context_retention_ratio
  return ratio != null && ratio > 1 ? '부하 없는 기준 측정이 부하 측정보다 느렸다(기준 탐침이 눌렸다)' : null
}

// 속도·리소스·안정성 지표. get()은 run.metrics를 받아 원본 값을 꺼내고,
// fmt()는 그 값을 사람이 읽기 좋은 단위로 바꾼다. item은 그 값이 나오는 실행
// 항목 id — 저장 상태(능력 부재·실행 실패)를 읽는 데 쓴다(scoring.formatRawCell).
// 여러 항목에서 파생되는 값(유지율·성공률·완결성)은 비워둔다.
// direction은 원래 값의 좋은 방향('lower'면 낮을수록 좋음) — 비교 노트 입력 표에 ↑/↓로 적는다
// (방향이 없으면 모델이 오탐률 100%를 좋은 값으로 읽는다). health는 응답 상태 요약의 키
// (`검증 중`·`원인 미확인` 표시). 기준선 범위는 목록 기본값을 따르고 `baselineInScope`로만 바꾼다.
export const METRICS = [
  { key: 'load_time_sec', label: '모델 로드 시간', item: 'model_load', direction: 'lower', get: (m) => m.load_time_sec, fmt: (v) => `${v.toFixed(2)}초` },
  { key: 'ttft_sec', label: 'TTFT (짧은 탐침, 중앙값)', item: 'short_probe', direction: 'lower', get: (m) => m.ttft_sec, fmt: (v) => `${v.toFixed(3)}초` },
  { key: 'tok_per_sec', label: 'tok/s (짧은 탐침, 중앙값)', item: 'short_probe', get: (m) => m.tok_per_sec, fmt: (v) => v.toFixed(1) },
  {
    // 변동계수(편차 ÷ 중앙값) — 절대 편차는 빠른 모델이 자동으로 유리하다(scoring.js 참고)
    key: 'tok_per_sec_stdev',
    label: '성능 변동성 (변동계수)',
    item: 'short_probe',
    direction: 'lower',
    get: (m) => (m.tok_per_sec_stdev != null && m.tok_per_sec ? m.tok_per_sec_stdev / m.tok_per_sec : null),
    fmt: (v) => `${(v * 100).toFixed(1)}%`,
  },
  {
    key: 'prefill_tok_per_sec',
    label: 'prefill 처리량',
    item: 'context_2000',
    get: (m) => m.prefill_tok_per_sec,
    fmt: (v) => `${v.toFixed(0)} tok/s`,
  },
  {
    key: 'context_retention_ratio',
    label: '컨텍스트 부하 시 tok/s 유지율',
    get: (m) => m.context_retention_ratio,
    fmt: (v) => `${(v * 100).toFixed(0)}%`,
    invalid: retentionInvalidCause,
  },
  {
    key: 'load_success_ratio',
    label: '부하 조건 성공률',
    get: (m) => m.load_success_ratio,
    fmt: (v) => `${(v * 100).toFixed(0)}%`,
  },
  {
    key: 'completion_rate',
    label: '응답 완결성',
    get: (m) => m.completion_rate,
    fmt: (v) => `${(v * 100).toFixed(0)}%`,
  },
  {
    // "오프로드"가 방향이 헷갈리기 쉬워(CPU→GPU로 옮긴 비율인데 "GPU에서 뺐다"로
    // 오독하기 쉽다) 라벨은 방향이 분명한 "VRAM 상주 비율"로 표시한다.
    // 100% = 전부 GPU(VRAM)에서 돈다(빠름), 낮을수록 CPU로 밀려나 느려진다.
    key: 'vram_offload_ratio',
    label: 'VRAM 상주 비율 (GPU 오프로드)',
    item: 'memory',
    get: (m) => m.vram_offload_ratio,
    fmt: (v) => `${(v * 100).toFixed(0)}%`,
  },
  {
    key: 'memory_bytes',
    label: '메모리 사용량',
    item: 'memory',
    direction: 'lower',
    get: (m) => m.memory_bytes,
    fmt: (v) => `${(v / 1e9).toFixed(2)} GB`,
  },
]

// 품질·보안 지표. 능력 대조군에 걸려 무효 처리된 값은
// backend가 null로 주므로 MetricsList의 "측정 안 됨"으로 자연스럽게 표시된다
// — 왜 무효인지는 QualityDetail의 드릴다운에서 확인한다.
export const QUALITY_METRICS = [
  { key: 'instruction_following_zero', item: 'instruction_following', health: 'instruction_following', label: '지시 따르기 정확도 (zero)', get: (m) => m.instruction_following?.zero?.score, fmt: pctFmt },
  { key: 'instruction_following_few', item: 'instruction_following', label: '지시 따르기 정확도 (few)', get: (m) => m.instruction_following?.few?.score, fmt: pctFmt },
  { key: 'structured_output_zero', item: 'structured_output', health: 'structured_output', label: '구조적 출력 준수 (zero)', get: (m) => m.structured_output?.zero?.score, fmt: pctFmt },
  { key: 'structured_output_few', item: 'structured_output', label: '구조적 출력 준수 (few)', get: (m) => m.structured_output?.few?.score, fmt: pctFmt },
  { key: 'closed_qa_score', item: 'closed_qa', health: 'closed_qa', label: '폐쇄형 정답 정확도', get: (m) => m.closed_qa?.score, fmt: pctFmt },
  { key: 'key_coverage_score', item: 'key_coverage', health: 'key_coverage', label: '핵심 정보 포함률', get: (m) => m.key_coverage?.score, fmt: pctFmt },
  { key: 'hallucination_score', item: 'hallucination', health: 'hallucination', label: '환각 저항', get: (m) => m.hallucination?.score, fmt: pctFmt },
  { key: 'injection_direct_score', item: 'injection_direct', health: 'injection_direct', label: '인젝션 저항 (직접)', get: (m) => m.injection_direct?.score, fmt: pctFmt },
  { key: 'injection_indirect_score', item: 'injection_indirect', health: 'injection_indirect', label: '인젝션 저항 (간접)', get: (m) => m.injection_indirect?.score, fmt: pctFmt },
  { key: 'prompt_leak_score', item: 'prompt_leak', health: 'prompt_leak', label: '시스템 프롬프트 유출 저항', get: (m) => m.prompt_leak?.score, fmt: pctFmt },
  { key: 'over_refusal_score', item: 'over_refusal', health: 'over_refusal', label: '과잉 거절률 (정상 응답률)', get: (m) => m.over_refusal?.score, fmt: pctFmt },
  { key: 'consistency_score', item: 'consistency', health: 'consistency', label: '일관성/재현성', get: (m) => m.consistency?.score, fmt: (v) => pctFmt(v) },
  { key: 'long_context_recall', item: 'long_context', health: 'long_context.recall', label: '긴 컨텍스트 기억력', get: (m) => m.long_context?.recall?.score, fmt: pctFmt },
  { key: 'long_context_constraint', item: 'long_context', health: 'long_context.constraint', label: '다중 턴 제약 유지', get: (m) => m.long_context?.constraint?.score, fmt: pctFmt },
  // 리포트 측정값 표에는 있는데 화면·노트 표에 없던 두 행(맞추는 일은 양방향이다)
  { key: 'robustness_overall', label: '표현 강건성 (점수 편차)', direction: 'lower', get: (m) => m.robustness?.overall, fmt: (v) => v.toFixed(3) },
  {
    key: 'context_limit_score',
    label: '실측 컨텍스트 한계',
    baselineInScope: false,
    get: (m) => m.context_limit?.score ?? null,
    fmt: (v) => `${Math.round(v)} 토큰`,
  },
  {
    // 게이트 — 순위 지표가 아니라 "쓸 수 있나 없나". 점수에 들어가지 않아 참고로 적고,
    // 기준선 참고선에는 넣지 않는다. 부적합 여부는 백엔드가 낸 판정을 그대로 쓴다(문턱을 여기 다시 두지 않는다).
    key: 'korean_purity',
    label: '한국어 출력 순도 (게이트, 참고)',
    baselineInScope: false,
    getRun: (run) => run?.korean_purity?.score ?? null,
    emptyText: (run) => (run?.korean_purity?.undeterminable ? '판정 불가 (답한 응답 10건 미만)' : null),
    fmtRun: (run, v) => `${(v * 100).toFixed(0)}%${run?.korean_purity?.state === 'unfit' ? ' · 주 용도 부적합' : ''}`,
  },
]

// tool-calling 정확도. 문항별
// 원본 응답·통과 여부 드릴다운은 QualityDetail.jsx 몫이고, 여기서는 4개
// 집계값만 나열한다(다른 지표들과 같은 자리에서 한눈에 비교하기 위해).
export const TOOL_CALLING_METRICS = [
  { key: 'trigger_accuracy', item: 'tool_calling', health: 'tool_calling', label: 'Tool-calling 트리거 정확도', get: (m) => m.tool_calling?.trigger_accuracy, fmt: pctFmt },
  { key: 'param_accuracy', item: 'tool_calling', health: 'tool_calling', label: 'Tool-calling 파라미터 정확도', get: (m) => m.tool_calling?.param_accuracy, fmt: pctFmt },
  { key: 'false_positive_rate', item: 'tool_calling', health: 'tool_calling', direction: 'lower', label: 'Tool-calling 오탐률', get: (m) => m.tool_calling?.false_positive_rate, fmt: pctFmt },
  { key: 'advanced_pass_rate', item: 'tool_calling', health: 'tool_calling', label: 'Tool-calling 심화 시나리오 통과율', get: (m) => m.tool_calling?.advanced_pass_rate, fmt: pctFmt },
  {
    key: 'injection_resistance_rate', item: 'injection_probe', health: 'injection_probe',
    label: '도구 결과 인젝션 저항성 (모델, 보조 지표)',
    get: (m) => m.injection_probe?.injection_resistance_rate,
    fmt: pctFmt,
  },
]

export const CONTEXT_STAGES = [2000, 4000, 8000]

export const STATUS_LABEL = {
  running: '실행 중',
  cancelling: '중단 처리 중…',
  completed: '완료',
  partial: '부분 완료',
  failed: '실패',
}
