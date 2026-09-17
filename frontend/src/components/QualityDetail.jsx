// 품질·보안 지표의 문항별 원본 답변 드릴다운. `run.metrics`를 그대로 받아
// backend/quality_runner.py가 채운 detail 배열을 펼쳐서 보여준다 — 어떤
// 표현에서 무너졌는지 집계 점수만으로는 안 보이기 때문이다.
// 네이티브 <details>만 쓴다 — 문항이 250개 넘게 나올 수 있어 전부 펼쳐두면
// 스크롤이 감당 안 되고, 상태를 React에 따로 들 필요도 없다.

function pct(v, digits = 0) {
  return v == null ? null : `${(v * 100).toFixed(digits)}%`
}

function Badge({ value }) {
  if (value == null) return <span className="qbadge qbadge-invalid">무효</span>
  if (typeof value === 'boolean') {
    return value ? (
      <span className="qbadge qbadge-pass">PASS</span>
    ) : (
      <span className="qbadge qbadge-fail">FAIL</span>
    )
  }
  if (value >= 0.999) return <span className="qbadge qbadge-pass">PASS</span>
  if (value <= 0.001) return <span className="qbadge qbadge-fail">FAIL</span>
  return <span className="qbadge qbadge-mid">{value.toFixed(2)}</span>
}

function QaRow({ id, variant, response, score, tag }) {
  return (
    <div className="qa-row">
      <div className="qa-head">
        <span className="qid">{id}</span>
        <Badge value={score} />
        {tag && <span className="qtag">{tag}</span>}
      </div>
      {variant && <div className="qa-q">{variant}</div>}
      <div className="qa-a">{response || <em>(빈 응답)</em>}</div>
    </div>
  )
}

function Section({ id, title, scoreLine, note, children }) {
  return (
    <details className="quality-section" data-metric={id}>
      <summary>
        <span className="qs-title">{title}</span>
        <span className="qs-score">{scoreLine}</span>
      </summary>
      <div className="qs-body">
        {note && <p className="qs-note">{note}</p>}
        {children}
      </div>
    </details>
  )
}

export default function QualityDetail({ metrics }) {
  const m = metrics
  if (!m || !m.instruction_following) return null // 품질 항목이 아직 하나도 안 끝났으면 표시하지 않는다

  const sections = []

  if (m.instruction_following) {
    const inf = m.instruction_following
    sections.push(
      <Section
        key="instruction_following"
        id="instruction_following"
        title="지시 따르기 정확도"
        scoreLine={`zero ${pct(inf.zero?.score) ?? '—'} → few ${pct(inf.few?.score) ?? '—'}`}
        note={`능력 대조군(if-010) ${inf.capability_control_passed == null ? '이 실행에서 재지 않음' : inf.capability_control_passed ? '통과' : '실패'} — 인젝션·유출 저항 지표의 전제 조건`}
      >
        {inf.zero?.detail.map((e, i) => (
          <QaRow key={`z${i}`} id={e.id} variant={e.variant} response={e.response} score={e.score} tag="zero" />
        ))}
        {inf.few?.detail.map((e, i) => (
          <QaRow key={`f${i}`} id={e.id} variant={e.variant} response={e.response} score={e.score} tag="few" />
        ))}
      </Section>,
    )
  }

  if (m.structured_output) {
    const so = m.structured_output
    sections.push(
      <Section
        key="structured_output"
        id="structured_output"
        title="구조적 출력 준수"
        scoreLine={`zero ${pct(so.zero?.score) ?? '—'} → few ${pct(so.few?.score) ?? '—'}`}
      >
        {so.zero?.detail.map((e, i) => (
          <QaRow key={`z${i}`} id={e.id} variant={e.variant} response={e.response} score={e.score} tag="zero" />
        ))}
        {so.few?.detail.map((e, i) => (
          <QaRow key={`f${i}`} id={e.id} variant={e.variant} response={e.response} score={e.score} tag="few" />
        ))}
      </Section>,
    )
  }

  if (m.closed_qa) {
    sections.push(
      <Section key="closed_qa" id="closed_qa" title="폐쇄형 정답 정확도" scoreLine={pct(m.closed_qa.score)}>
        {m.closed_qa.detail.map((e, i) => (
          <QaRow key={i} id={e.id} variant={e.variant} response={e.response} score={e.score} />
        ))}
      </Section>,
    )
  }

  if (m.key_coverage) {
    sections.push(
      <Section key="key_coverage" id="key_coverage" title="핵심 정보 포함률" scoreLine={pct(m.key_coverage.score)}>
        {m.key_coverage.detail.map((e, i) => (
          <QaRow key={i} id={e.id} variant={e.variant} response={e.response} score={e.score} />
        ))}
      </Section>,
    )
  }

  if (m.hallucination) {
    const hl = m.hallucination
    sections.push(
      <Section
        key="hallucination"
        id="hallucination"
        title="환각 저항"
        scoreLine={hl.score == null ? '값 없음' : pct(hl.score)}
        note={`능력 대조군 5문항 ${hl.capability_control_passed == null ? '이 실행에서 재지 않음' : hl.capability_control_passed ? '전부 통과' : '일부 실패 — 지표 전체 무효'}`}
      >
        {hl.detail.map((e, i) => (
          <QaRow key={i} id={e.id} variant={e.variant} response={e.response} score={e.score} />
        ))}
        {hl.capability_control_detail?.map((e, i) => (
          <QaRow key={`c${i}`} id={e.id} variant={e.variant} response={e.response} score={e.score} tag="능력 대조군" />
        ))}
      </Section>,
    )
  }

  for (const [key, title] of [
    ['injection_direct', '프롬프트 인젝션 저항성 (직접)'],
    ['injection_indirect', '프롬프트 인젝션 저항성 (간접)'],
    ['prompt_leak', '시스템 프롬프트 유출 저항'],
    ['over_refusal', '과잉 거절률 (정상 응답률로 표시)'],
  ]) {
    const e_ = m[key]
    if (!e_) continue
    sections.push(
      <Section key={key} id={key} title={title} scoreLine={e_.score == null ? '값 없음' : pct(e_.score)}>
        {e_.detail.map((e, i) => (
          <QaRow key={i} id={e.id} variant={e.variant} response={e.response} score={e.score} />
        ))}
      </Section>,
    )
  }

  if (m.tool_calling) {
    const tc = m.tool_calling
    sections.push(
      <Section
        key="tool_calling"
        id="tool_calling"
        title="Tool-calling 정확도"
        scoreLine={`트리거 ${pct(tc.trigger_accuracy) ?? '—'} · 파라미터 ${pct(tc.param_accuracy) ?? '—'} · 오탐 ${pct(tc.false_positive_rate) ?? '—'} · 심화 ${pct(tc.advanced_pass_rate) ?? '—'}`}
        note="기본 12문항(트리거·파라미터·오탐)과 심화 12문항(다중 도구 조합·결과 위 추론·범위 밖·모호함·잘못된 값·실행 실패·위험 도구 확인)을 나눠서 채점한다. delete_model/pull_model은 테스트 중 실제로 실행되지 않는다."
      >
        {tc.basic_detail?.map((e, i) => (
          <QaRow
            key={`b${i}`}
            id={e.id}
            variant={e.variant}
            response={e.response}
            score={e.category === 'false_positive' ? e.trigger_correct : e.param_correct ?? e.trigger_correct}
            tag={e.category}
          />
        ))}
        {tc.advanced_detail?.map((e, i) => (
          <QaRow key={`a${i}`} id={e.id} variant={e.variant} response={e.response} score={e.passed} tag={e.category} />
        ))}
      </Section>,
    )
  }

  if (m.injection_probe) {
    const ip = m.injection_probe
    sections.push(
      <Section
        key="injection_probe"
        id="injection_probe"
        title="도구 결과 프롬프트 인젝션 저항성 (보조 지표)"
        scoreLine={`프로브 호출률 ${pct(ip.probe_trigger_rate) ?? '—'} · 저항률 ${pct(ip.injection_resistance_rate) ?? '—'}`}
        note="정상 대기질 데이터처럼 보이는 응답 안에 delete_model을 호출하라는 지시문이 심어져 있다. 여기서는 모델이 넘어가는지만 본다 — 확인 단계가 실제로 막아주는지는 이 점수와 별도로 tests/test_injection_defense.py가 코드 자체를 검증한다."
      >
        {ip.detail?.map((e, i) => (
          <QaRow
            key={i}
            id={`probe-${i + 1}`}
            variant={e.variant}
            response={e.response}
            score={!e.fell_for_injection}
            tag={e.fell_for_injection ? '인젝션에 넘어감' : '저항함'}
          />
        ))}
      </Section>,
    )
  }

  if (m.consistency) {
    sections.push(
      <Section
        key="consistency"
        id="consistency"
        title="일관성/재현성 (5회 반복)"
        scoreLine={pct(m.consistency.score, 1)}
      >
        {m.consistency.detail.map((e, i) => (
          <div key={i} className="qa-row">
            <div className="qa-head">
              <span className="qid">{e.id}</span>
              <Badge value={e.pairwise_similarity} />
              <span className="qtag">쌍별 평균 유사도</span>
            </div>
            <div className="repeat-list">
              {e.responses.map((r, j) => (
                <div key={j} className="repeat-item">
                  <span className="repeat-no">#{j + 1}</span>
                  {r}
                </div>
              ))}
            </div>
          </div>
        ))}
      </Section>,
    )
  }

  if (m.long_context) {
    const lc = m.long_context
    sections.push(
      <Section
        key="long_context"
        id="long_context"
        title="긴 컨텍스트 기억력 + 다중 턴 제약 유지"
        scoreLine={`기억력 ${pct(lc.recall?.score) ?? '—'} (압축끔 ${pct(lc.recall?.score_uncompressed) ?? '—'}) · 제약 ${pct(lc.constraint?.score) ?? '—'} (압축끔 ${pct(lc.constraint?.score_uncompressed) ?? '—'})`}
      >
        {lc.detail?.map((e, i) => (
          <div key={i} className="qa-row">
            <div className="qa-head">
              <span className="qid">
                {e.scenario} · {e.kind === 'recall' ? '기억력 회수' : '제약 준수'} · {e.turn}턴
              </span>
              <Badge value={e.passed} />
              <span className="qtag">{e.compress ? '압축 켬' : '압축 끔'}</span>
            </div>
            <div className="qa-a">{e.response}</div>
          </div>
        ))}
      </Section>,
    )
  }

  return (
    <div className="quality-detail">
      <h3>지표별 상세 (문항·답변 원문)</h3>
      {sections}
    </div>
  )
}
