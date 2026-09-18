import {
  conditionLabel,
  consistencyNumPredict,
  hardwareText,
  machineLabel,
  ollamaVersionText,
  powerText,
  longContextReloadText,
  reloadAfterSkippedText,
  samplingText,
  toolResponsesText,
} from '../runDiff'

// 실행 전 확인 패널 — "실제로 뭐가 적용되는지, 지난번과 뭐가
// 달라졌는지"를 실행 버튼을 누르기 전에 보여준다. 시스템 프롬프트는 기본
// 펼침(select만 있던 것과 달리 본문을 바로 아래에 보여준다), 조건은
// bench_config 스냅샷(GET /api/tests/config)을 그대로 나열한다 — 화면에 또
// 하드코딩하면 백엔드 값이 바뀔 때 조용히 어긋난다.
export default function RunSummaryPanel({ runType, prompt, promptDirty, config, suites, estimate, toolWarning, diff, lastRun }) {
  const experiment = runType === 'prompt_experiment'
  return (
    <section className="run-summary-panel">
      <h3 className="metrics-heading">이번 실행</h3>
      <dl className="metrics-list run-summary-list">
        <div className="metrics-row">
          <dt>실행 종류</dt>
          <dd>{experiment ? '프롬프트 실험' : '선정'}</dd>
        </div>
        {experiment && (
          <div className="metrics-row">
            <dt>시스템 프롬프트</dt>
            <dd>{prompt ? `${prompt.title}${promptDirty ? ' (저장하지 않은 수정 있음 — 실행 불가)' : ''}` : '고르지 않음 — 실행 불가'}</dd>
          </div>
        )}
      </dl>
      {experiment ? (
        prompt && <pre className="prompt-body">{prompt.content}</pre>
      ) : (
        <p className="model-empty">(없음 — 시스템 프롬프트 없이 실행됩니다)</p>
      )}

      <h3 className="metrics-heading">이번 실행 조건</h3>
      {config && (
        <dl className="metrics-list run-summary-list">
          <div className="metrics-row">
            <dt>컨텍스트 길이</dt>
            <dd>{config.num_ctx?.toLocaleString()} 토큰</dd>
          </div>
          <div className="metrics-row">
            <dt>출력 상한 (속도 / 품질 / 일관성)</dt>
            <dd>
              {config.num_predict} / {config.quality_num_predict} / {consistencyNumPredict(config)} 토큰
            </dd>
          </div>
          <div className="metrics-row">
            <dt>샘플링 (순위 지표)</dt>
            <dd>{samplingText(config.sampling)}</dd>
          </div>
          <div className="metrics-row">
            <dt>샘플링 (일관성/재현성)</dt>
            <dd>
              {samplingText(config.consistency_sampling)}
              {config.consistency_sampling && ' · seed 없음(의도됨)'}
            </dd>
          </div>
          <div className="metrics-row">
            <dt>thinking</dt>
            <dd>{config.think ? '켬' : '끔'}</dd>
          </div>
          <div className="metrics-row">
            <dt>짧은 탐침 반복</dt>
            <dd>{config.repeat_count}회</dd>
          </div>
          <div className="metrics-row">
            <dt>타임아웃 (짧은 / 긴 / 품질)</dt>
            <dd>
              {config.timeout_short_sec}초 / {config.timeout_long_sec}초 / {config.quality_timeout_sec}초
            </dd>
          </div>
          <div className="metrics-row">
            <dt>요약 압축 모델</dt>
            <dd>{config.summarizer_model}</dd>
          </div>
          <div className="metrics-row">
            <dt>측정 기계</dt>
            <dd>{machineLabel(config.measurement_machine)}</dd>
          </div>
          <div className="metrics-row">
            <dt>Ollama 버전</dt>
            <dd>{ollamaVersionText(config)}</dd>
          </div>
          <div className="metrics-row">
            <dt>하드웨어</dt>
            <dd>{hardwareText(config.hardware)}</dd>
          </div>
          <div className="metrics-row">
            <dt>전원 (시작 시)</dt>
            <dd>{powerText(config.power)}</dd>
          </div>
          <div className="metrics-row">
            <dt>도구 응답</dt>
            <dd>{toolResponsesText(config)}</dd>
          </div>
          <div className="metrics-row">
            <dt>긴 컨텍스트 시작</dt>
            <dd>{longContextReloadText(config)}</dd>
          </div>
          <div className="metrics-row">
            <dt>건너뛴 항목 뒤 재로드</dt>
            <dd>{reloadAfterSkippedText(config)}</dd>
          </div>
        </dl>
      )}
      {toolWarning && <p className="compare-warning">{toolWarning}</p>}

      {suites && suites.length > 0 && (
        <>
          <h3 className="metrics-heading">측정 항목 ({suites.length}개)</h3>
          <ul className="run-summary-items">
            {suites.map((s) => (
              <li key={s.id}>{s.label}</li>
            ))}
          </ul>
        </>
      )}

      {diff &&
        (diff.changed.length > 0 ? (
          <p className="run-diff-note">
            직전 실행({lastRun && new Date(lastRun.started_at).toLocaleString()}) 대비 달라진 조건:{' '}
            {diff.changed.map(conditionLabel).join(', ')}
          </p>
        ) : (
          <p className="run-diff-note">직전 실행과 조건이 같습니다.</p>
        ))}
      {diff?.unrecorded.length > 0 && (
        <p className="run-diff-note">
          직전 실행에 기록이 없어 대조하지 못한 조건: {diff.unrecorded.map(conditionLabel).join(', ')}
        </p>
      )}

      <p className="run-estimate-note">
        예상 소요 시간:{' '}
        {estimate?.total_sec != null
          ? `약 ${Math.max(1, Math.round(estimate.total_sec / 60))}분 (과거 실행 ${estimate.sample_count}건 기준)`
          : '예상 불가 (참고할 과거 실행 없음)'}
      </p>
    </section>
  )
}
