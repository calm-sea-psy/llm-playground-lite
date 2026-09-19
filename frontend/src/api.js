// 백엔드(FastAPI) 호출 래퍼.

// 설치된 모든 모델의 상세 정보. 프론트가 한 번 받아 localStorage에 캐시한다.
export async function fetchModelDetails() {
  const res = await fetch('/api/models/details')
  if (!res.ok) throw new Error(`모델 정보 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 해당 모델의 저장된 대화 목록(제목/수정시각). 저장 자체는 /api/chat이 매 턴 자동으로 한다.
export async function fetchConversations(model) {
  const res = await fetch(`/api/conversations?model=${encodeURIComponent(model)}`)
  if (!res.ok) throw new Error(`대화 목록 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function fetchConversation(id) {
  const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`대화 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function deleteConversation(id) {
  const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`대화 삭제 실패 (HTTP ${res.status})`)
}

// 메시지 하나에 별점(1~5)을 매기거나, rating: null로 지운다.
export async function rateMessage(conversationId, messageId, { rating, note }) {
  const res = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/rating`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, note: note ?? null }),
    },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `별점 저장 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 성능 테스트

export async function fetchSystemPrompts() {
  const res = await fetch('/api/system-prompts')
  if (!res.ok) throw new Error(`시스템 프롬프트 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 시스템 프롬프트 저장·삭제 — 이름은 파일 식별자(slug), 제목은 화면용.
export async function saveSystemPrompt(name, { title, content }) {
  const res = await fetch(`/api/system-prompts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `시스템 프롬프트 저장 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

export async function deleteSystemPrompt(name) {
  const res = await fetch(`/api/system-prompts/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `시스템 프롬프트 삭제 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 공개본에 없을 수 있는 기능(프롬프트 실험·지표 다시 재기·유지보수 CLI)이 켜져 있는지.
// 세트 만들기(개발 기능) — 사실 후보·원천·펼치기. 문항과 정답이 오가므로 전부 로컬 백엔드 안에서만 돈다.
export async function fetchFactCandidates() {
  const res = await fetch('/api/testsets/facts')
  if (!res.ok) throw new Error(`사실 후보 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

async function postJson(url, body, method = 'POST') {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail || `요청 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

export async function removeTestsetDocument(name) {
  const res = await fetch(`/api/testsets/documents/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail || `문서 삭제 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

export async function fetchTestsetReadiness() {
  const res = await fetch('/api/testsets/readiness')
  if (!res.ok) throw new Error(`준비 상태 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function uploadTestsetDocuments(edition, documents) {
  return postJson('/api/testsets/documents/upload', { edition, documents })
}

export async function buildSourceSkeleton(picked) {
  // canary는 보내지 않는다 — 심은 문서에서 읽는다
  return postJson('/api/testsets/facts/skeleton', { picked })
}

export async function saveTestsetSource(source) {
  return postJson('/api/testsets/source', { source }, 'PUT')
}

export async function deriveTestsets(source, write) {
  return postJson('/api/testsets/derive', { source, write })
}

export async function fetchFeatures() {
  const res = await fetch('/api/features')
  if (!res.ok) throw new Error(`기능 목록 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function fetchActiveTest() {
  const res = await fetch('/api/tests/active')
  if (!res.ok) throw new Error(`실행 상태 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 실행 전 확인 패널 — 측정 항목 목록·적용 조건·예상 소요 시간.

export async function fetchTestSuites() {
  const res = await fetch('/api/tests/suites')
  if (!res.ok) throw new Error(`측정 항목 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function fetchTestConfig() {
  const res = await fetch('/api/tests/config')
  if (!res.ok) throw new Error(`측정 조건 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function fetchTestEstimate(model, scope = 'full') {
  const res = await fetch(
    `/api/tests/estimate?model=${encodeURIComponent(model)}&scope=${encodeURIComponent(scope)}`,
  )
  if (!res.ok) throw new Error(`예상 소요 시간 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 선정용 실행은 프롬프트를 보내지 않는다. 실험이면 저장된 프롬프트 이름만 — 본문은 백엔드가 파일에서 읽는다.
export async function startTestRun({ model, runType = 'selection', systemPromptName = null }) {
  const res = await fetch('/api/tests/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, run_type: runType, system_prompt_name: systemPromptName || null }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `테스트 실행 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 지표 단위 재실행 — 고른 항목만 별도 결과 파일로 다시 잰다. 이유가 없으면 서버가 받지 않는다(리포트의 혼합 실행 줄에 실린다).
export async function rerunTestItems(runId, itemIds, reason) {
  const res = await fetch(`/api/tests/runs/${encodeURIComponent(runId)}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: itemIds, reason }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `지표 재실행 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

export async function fetchTestRun(id) {
  const res = await fetch(`/api/tests/runs/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`실행 상태 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function cancelTestRun(id) {
  const res = await fetch(`/api/tests/runs/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`실행 중단 실패 (HTTP ${res.status})`)
}

export async function fetchTestResults() {
  const res = await fetch('/api/tests/results')
  if (!res.ok) throw new Error(`결과 목록 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function fetchTestResult(id) {
  const res = await fetch(`/api/tests/results/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`결과 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 선택한 실행의 재채점 — 미리보기(파일을 고치지 않는다)와 실행
export async function fetchRescorePlan(runIds) {
  const res = await fetch('/api/tests/rescore/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  if (!res.ok) throw new Error(`재채점 미리보기 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function rescoreRuns(runIds) {
  const res = await fetch('/api/tests/rescore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `재채점 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 클라우드 베이스라인

// 기준선은 후보와 같은 문서 길이로 고른다 — 주지 않으면 다음 후보가 읽을 판(대표 판)이다
export async function fetchBaseline(documentLength = null) {
  const query = documentLength ? `?document_length=${encodeURIComponent(documentLength)}` : ''
  const res = await fetch(`/api/tests/baseline${query}`)
  if (!res.ok) throw new Error(`베이스라인 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

export async function startBaselineRun() {
  const res = await fetch('/api/tests/baseline', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `베이스라인 실행 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 비교 노트 — 선택된 실행들의 모델 각각에게 비교 표 전체를 주고
// 특이사항 노트를 쓰게 한다. tableText는 프론트가 이미 렌더링한 표를
// 그대로 직렬화한 것 — 지표 라벨·단위 정의를 백엔드에 또 두지 않기 위해서다.

export async function generateCompareNotes(runIds, tableText) {
  const res = await fetch('/api/compare/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds, table_text: tableText }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `노트 생성 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// run_id → 별칭. 별칭 규칙은 백엔드(model_aliases.py) 하나에만 있다 — 노트 입력 표 머리글과 리포트가 같은 이름을 쓴다.
export async function fetchCompareAliases(runIds) {
  const res = await fetch('/api/compare/aliases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  if (!res.ok) throw new Error(`별칭 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 이 조합의 노트 캐시 상태 — 있는 노트·빠진 모델·실측 평균 소요. 모델을 부르지 않는다.
export async function fetchCompareNotesStatus(runIds, tableText) {
  const res = await fetch('/api/compare/notes/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds, table_text: tableText }),
  })
  if (!res.ok) throw new Error(`노트 상태 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 모델 하나의 노트만 생성 — 409(측정 중)는 호출자가 "노트 없이" 경로로 받도록 status를 실어 던진다.
export async function generateOneCompareNote(runIds, tableText, runId) {
  const res = await fetch('/api/compare/notes/one', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds, table_text: tableText, run_id: runId }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = new Error(body?.detail || `노트 생성 실패 (HTTP ${res.status})`)
    err.status = res.status
    throw err
  }
  return res.json()
}

export async function rateCompareNote(key, runId, { rating, note }) {
  const res = await fetch(
    `/api/compare/notes/${encodeURIComponent(key)}/${encodeURIComponent(runId)}/rating`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, note: note ?? null }),
    },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `별점 저장 실패 (HTTP ${res.status})`)
  }
  return res.json()
}

// 일관성 대표 쌍 판정 — 칸은 서버만 아는 토큰으로 가리킨다(판정이 끝나기 전에는 모델·실행을 내주지 않는다).
export async function fetchConsistencyJudgments() {
  const res = await fetch('/api/consistency-judgments')
  if (!res.ok) throw new Error(`판정 목록 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 비교에 든 실행들의 사람 판정으로 일관성을 종합 순위에서 뺄지 — 판정 기록에서 세고, 가중치는 화면이 적용한다.
export async function fetchConsistencyDemotion(runIds) {
  const res = await fetch('/api/consistency-judgments/demotion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  if (!res.ok) throw new Error(`일관성 게이트 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 비교에 든 실행의 판정 칸이 판정 파일에 다 있나 — 없으면 화면에 판정이 차 있어도 리포트의 판정칸은 비어 나온다.
export async function fetchConsistencyCoverage(runIds) {
  const res = await fetch('/api/consistency-judgments/coverage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  if (!res.ok) throw new Error(`판정 파일 대조 실패 (HTTP ${res.status})`)
  return res.json()
}

// 비교에 든 실행 기준으로 판정 칸을 다시 만든다. dryRun이면 쓰지 않고 이어 받을 판정·옮길 판정·새 칸 수만 센다.
// 409는 그새 칸이 다 생긴 경우다 — 호출자가 상태를 다시 받도록 status를 실어 던진다.
export async function rebuildConsistencyJudgments(runIds, dryRun) {
  const res = await fetch('/api/consistency-judgments/rebuild', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds, dry_run: dryRun }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = new Error(body?.detail || `판정 칸 다시 만들기 실패 (HTTP ${res.status})`)
    err.status = res.status
    throw err
  }
  return res.json()
}

// 409는 서버가 다시 떠 토큰이 새로 발급된 경우다 — 호출자가 목록을 다시 받도록 status를 실어 던진다.
export async function saveConsistencyVerdict(token, verdict, reason = null) {
  const res = await fetch(`/api/consistency-judgments/${encodeURIComponent(token)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ verdict, reason }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = new Error(body?.detail || `판정 저장 실패 (HTTP ${res.status})`)
    err.status = res.status
    throw err
  }
  return res.json()
}

// 스트리밍 응답 하나를 소비하는 공용부 — /api/chat과 /api/chat/confirm이 같은
// 모양(text/plain 스트림 + X-Conversation-Id/X-Assistant-Message-Id 헤더)이라
// 공유한다.
async function _consumeChatStream(res, onDelta, onMeta) {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `채팅 요청 실패 (HTTP ${res.status})`)
  }

  onMeta({
    conversationId: res.headers.get('X-Conversation-Id'),
    compressed: res.headers.get('X-Compress-Applied') === '1',
    assistantMessageId: res.headers.get('X-Assistant-Message-Id'),
  })

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    onDelta(decoder.decode(value, { stream: true }))
  }
}

// 스트리밍 채팅. conversationId가 없으면 새 대화가 만들어지고 응답 헤더로 id가 온다.
// onDelta(chunk)는 텍스트가 올 때마다, onMeta({conversationId, compressed, assistantMessageId})는
// 헤더를 읽는 즉시(스트림 소비 전, 응답 내용이 오기 전) 한 번 호출된다 — assistantMessageId는
// 아직 저장되지 않은 응답의 최종 id로, 스트림이 끝나자마자 별점 UI를 붙이는 데 쓴다.
// toolsEnabled가 true면 모델이 도구를 부를 수 있다 — 위험한 도구는 그 자리에서
// 실행되지 않고 확인 대기 메시지로 스트림이 끝난다. 호출 쪽이 이후 대화를 다시 불러
// (`fetchConversation`) 확인 대기 여부를 판단해야 한다 — 헤더는 스트림 시작 전에 이미
// 정해지므로 도중에 알게 되는 이 상태를 실어 보낼 수 없다.
export async function streamChat(
  { model, message, conversationId, system, systemPromptName, temperature, compress, toolsEnabled },
  onDelta,
  onMeta,
  signal,
) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      message,
      conversation_id: conversationId || null,
      system,
      // 저장된 프롬프트를 골랐으면 이름 — 백엔드는 본문이 파일과 같을 때만 대화에 이름·해시를 남긴다
      system_prompt_name: systemPromptName || null,
      temperature,
      compress,
      tools_enabled: Boolean(toolsEnabled),
    }),
    signal,
  })
  await _consumeChatStream(res, onDelta, onMeta)
}

// 위험 도구 확인 응답. 승인/거부 둘 다 같은
// 스트리밍 응답으로 이어지는 자연어 답변을 돌려준다 — streamChat과 같은 방식으로 소비한다.
export async function confirmToolCall({ conversationId, approved }, onDelta, onMeta, signal) {
  const res = await fetch('/api/chat/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, approved }),
    signal,
  })
  await _consumeChatStream(res, onDelta, onMeta)
}

// 등록된 도구 정의 + 상태. /tools 화면이 쓴다.
export async function fetchTools() {
  const res = await fetch('/api/tools')
  if (!res.ok) throw new Error(`도구 목록 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 도구 결과 프롬프트 인젝션 테스트 전용 도구 — 정확도 테스트 목록(7개)과 별도로 노출된다.
export async function fetchInjectionProbeTool() {
  const res = await fetch('/api/tools/injection-probe')
  if (!res.ok) throw new Error(`인젝션 테스트 도구 조회 실패 (HTTP ${res.status})`)
  return res.json()
}

// 도구 하나를 모델 없이 직접 실행한다. 원본 응답을 그대로 돌려준다.
export async function invokeTool(name, args) {
  const res = await fetch(`/api/tools/${encodeURIComponent(name)}/invoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args: args || {} }),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.detail || `도구 실행 실패 (HTTP ${res.status})`)
  return body
}
