// 원천(문항 재료)을 화면에서 다루는 규칙 — 무엇이 비어 있고 무엇이 위험한지 한 곳에서 판단한다.
// 화면과 저장 버튼이 같은 판단을 쓴다.

/** 사람이 반드시 채워야 하는 칸 — 비어 있으면 펼치기가 오류를 낸다. 값이 없으면 줄이 없다(빈 배열). */
export function blanks(source) {
  const out = []
  if (!source?.canary?.trim()) out.push('canary — 심은 문서의 값과 글자까지 같아야 한다')
  if (!source?.generated_by?.trim()) out.push('무엇이 만들었나 — 재는 대상이 만든 문항은 측정이 무효다')
  for (const fact of source?.facts ?? []) {
    const ask = (fact.ask ?? []).filter((v) => v?.trim())
    if (ask.length < 2) out.push(`${fact.id} 질문 — 같은 뜻의 다른 말투 2개가 필요하다`)
    else if (ask[0].trim() === ask[1].trim()) out.push(`${fact.id} 질문 — 변형 둘이 같은 문장이다`)
    if ((fact.answer_forms ?? []).filter((v) => v?.trim()).length === 0) out.push(`${fact.id} 정답 표기`)
    const absent = (fact.absent?.ask ?? []).filter((v) => v?.trim())
    const patterns = (fact.absent?.fabrication_patterns ?? []).filter((v) => v?.trim())
    // 한쪽만 쓴 칸이 문제다 — 둘 다 비운 칸은 만들지 않기로 한 문항이라 펼치기가 그냥 지나간다
    if (absent.length + patterns.length > 0) {
      if (absent.length !== 2) out.push(`${fact.id} 문서에 없는 사실 — 질문 2개를 다 쓰거나 질문과 정규식을 모두 비운다`)
      if (patterns.length === 0) out.push(`${fact.id} 지어냄을 잡는 정규식 — 없으면 무엇을 지어냈는지 가릴 수 없다`)
    }
  }
  for (const doc of source?.documents ?? []) {
    const ask = (doc.summary_ask ?? []).filter((v) => v?.trim())
    if (ask.length > 0 && ask.length < 2) out.push(`${doc.path} 요약 질문 — 변형 2개가 필요하다`)
    const injected = typeof doc.injected === 'string' ? [doc.injected] : (doc.injected ?? [])
    if (injected.length > 0 && (doc.task_keywords ?? []).filter((v) => v?.trim()).length === 0) {
      out.push(`${doc.path} task_keywords — 비면 간접 인젝션이 늘 실패한다`)
    }
  }
  return out
}

/** 단계마다 비어 있는 칸 — 한 단계씩 검증하고 넘어가는 화면이 쓴다.
 * 돌려주는 키는 `ask · forms · absent · scenario · meta`이고, 값은 사람이 읽을 한 줄이다. */
export function blanksByStep(source) {
  const out = { ask: [], forms: [], absent: [], scenario: [], meta: [] }
  if (!source?.canary?.trim()) out.meta.push('canary — 심은 문서의 값과 글자까지 같아야 한다')
  if (!source?.topic?.trim()) out.meta.push('주제 — 이 세트가 무엇을 재는지 한 줄')
  if (!source?.generated_by?.trim()) out.meta.push('무엇이 만들었나 — 재는 대상이 만든 문항은 측정이 무효다')
  for (const fact of source?.facts ?? []) {
    const ask = (fact.ask ?? []).filter((v) => v?.trim())
    if (ask.length < 2) out.ask.push(`${fact.id} — 같은 뜻의 다른 말투 2줄이 필요하다`)
    else if (ask[0].trim() === ask[1].trim()) out.ask.push(`${fact.id} — 변형 둘이 같은 문장이다`)
    const forms = (fact.answer_forms ?? []).filter((v) => v?.trim())
    if (forms.length === 0) out.forms.push(`${fact.id} — 정답 표기가 비어 있다`)
    else if (forms.some((v) => v.trim().length < 2)) out.forms.push(`${fact.id} — 한 글자 표기는 거의 모든 답에 걸린다`)
    const absent = (fact.absent?.ask ?? []).filter((v) => v?.trim())
    const patterns = (fact.absent?.fabrication_patterns ?? []).filter((v) => v?.trim())
    if (absent.length + patterns.length > 0) {
      if (absent.length !== 2) out.absent.push(`${fact.id} — 질문 2줄을 다 쓰거나 질문과 정규식을 모두 비운다`)
      if (patterns.length === 0) out.absent.push(`${fact.id} — 지어냄을 잡는 정규식이 없으면 무엇을 지어냈는지 가릴 수 없다`)
    }
  }
  for (const doc of source?.documents ?? []) {
    const name = doc.path.replace('documents/', '')
    const ask = (doc.summary_ask ?? []).filter((v) => v?.trim())
    if (ask.length > 0 && ask.length < 2) out.scenario.push(`${name} 요약 질문 — 변형 2줄이 필요하다`)
    const injected = typeof doc.injected === 'string' ? [doc.injected] : (doc.injected ?? [])
    if (injected.length > 0 && (doc.task_keywords ?? []).filter((v) => v?.trim()).length === 0) {
      out.scenario.push(`${name} task_keywords — 비면 간접 인젝션이 늘 실패한다`)
    }
  }
  return out
}

/** 새로고침 뒤에 어느 단계까지 지나왔는가 — `확인됨`을 기억해 두는 것이 아니라 **지금 조건을 다시 본다.**
 * 자리를 비운 사이 문서가 지워졌을 수도 있어, 기억해 둔 통과는 거짓이 될 수 있다.
 *
 * 앞에서부터 이어지는 만큼만, 그리고 **마지막으로 `확인`을 지난 단계(`_step`)까지만** 돌려준다 — 뒷단계는
 * 조건이 저절로 성립하기도 해서(정답 표기는 규칙이 채우고, 문서에 없는 사실은 비워 둬도 된다), 그대로 두면
 * 열어 본 적 없는 단계가 `확인됨`이 되고 사람이 그 단계를 건너뛰게 된다.
 * `ready`는 준비 상태의 단계들, `documents`는 올라간 문서 수, `minPicks`는 고를 최소 개수, `order`는 단계 차례다. */
export function stepsDone({ source, ready, documents, minPicks, order }) {
  const docProblems = [...(ready?.steps?.[0]?.problems ?? []), ...(ready?.steps?.[1]?.problems ?? [])]
  const left = source ? blanksByStep(source) : null
  const holds = {
    upload: Boolean(documents > 0 && ready && docProblems.length === 0),
    pick: (source?.facts?.length ?? 0) >= minPicks,
    // 펼치기는 눌러 봐야 아는 단계라 조건으로 세지 못한다 — 지나간 적이 있는지(`_step`)로만 본다
    derive: source?._step === 'derive',
  }
  for (const id of ['ask', 'forms', 'absent', 'scenario']) {
    holds[id] = Boolean(left) && [...(left[id] ?? []), ...(id === 'ask' ? (left.meta ?? []) : [])].length === 0
  }
  // 마지막으로 `확인`을 지난 단계까지만 본다 — 뒷단계는 조건이 저절로 성립해도 아직 열어 본 적이 없다
  const last = order.indexOf(source?._step)
  const done = {}
  for (const [i, id] of order.entries()) {
    if (!holds[id] || (last >= 0 && i > last)) break
    done[id] = { ok: true, problems: [] }
  }
  return done
}

/** 뼈대를 다시 짤 때, 사람이 이미 채운 칸을 옮겨 온다 — 고른 사실 하나를 바꿨다고 써 둔 질문이 날아가면
 * 다시 쓸 길이 없다. 사실은 고른 후보(`_from.id`)로, 문서는 경로로 짝을 짓는다. */
export function carryOver(built, old) {
  if (!old?.facts?.length && !old?.documents?.length) return built
  const byFrom = new Map((old.facts ?? []).map((fact) => [fact._from?.id, fact]))
  const byPath = new Map((old.documents ?? []).map((doc) => [doc.path, doc]))
  return {
    ...built,
    topic: old.topic?.trim() ? old.topic : built.topic,
    generated_by: old.generated_by?.trim() ? old.generated_by : built.generated_by,
    scenarios: old.scenarios?.length ? old.scenarios : built.scenarios,
    facts: built.facts.map((fact) => {
      const before = byFrom.get(fact._from?.id)
      if (!before) return fact
      return { ...fact, ask: before.ask ?? fact.ask, answer_forms: before.answer_forms ?? fact.answer_forms,
               absent: before.absent ?? fact.absent }
    }),
    documents: built.documents.map((doc) => {
      const before = byPath.get(doc.path)
      if (!before) return doc
      return { ...doc, injected: before.injected ?? doc.injected, summary_ask: before.summary_ask ?? doc.summary_ask,
               task_keywords: before.task_keywords ?? doc.task_keywords }
    }),
  }
}

/** 고른 사실이 위험한가 — 짧은 문서에서 잘렸거나 여러 문서에 같은 값이 있으면 문항이 깨진다. */
export function factWarning(from) {
  if (!from) return null
  if (from.in_long === false) return '짧은 문서에만 있다 — 긴 문서를 읽는 실행에서는 정답이 문서에 없다'
  if ((from.also_in ?? []).length > 0) {
    return `같은 값이 ${from.also_in.join(' · ')}에도 있다 — 질문에 문서를 지정하지 않으면 정답이 둘이 된다`
  }
  return null
}

/** 선정을 가르는 축의 권장 칸 수 — 한 칸이 6.3%p 아래로 내려가야 문항 하나가 결론을 뒤집지 않는다. */
export const LEAST_CELLS = 16

/** 칸 수 미리 보기 — 한 칸의 무게(1/n)를 세트를 쓰기 전에 본다. `least`가 있는 줄은 그 수를 채워야 한다. */
export function cellPreview(source) {
  const facts = source?.facts ?? []
  const withAbsent = facts.filter((f) => (f.absent?.ask ?? []).filter((v) => v?.trim()).length === 2)
  const summaries = (source?.documents ?? []).filter((d) => (d.summary_ask ?? []).filter((v) => v?.trim()).length === 2)
  const injected = (source?.documents ?? []).flatMap((d) =>
    typeof d.injected === 'string' ? [d.injected] : (d.injected ?? []),
  )
  // 선정을 가르는 축(환각·보안)은 한 칸이 결론을 뒤집을 수 있어 16칸 이상을 권한다 — 모자라면 그 자리에서 말한다
  return [
    { label: '폐쇄형(문서 문항)', cells: facts.length * 2 },
    { label: '핵심 정보', cells: summaries.length * 2 },
    { label: '환각(답 없는 문항)', cells: withAbsent.length * 2, least: LEAST_CELLS },
    { label: '간접 인젝션', cells: injected.length * 2, least: LEAST_CELLS },
  ]
}
