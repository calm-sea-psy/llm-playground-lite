import { useCallback, useEffect, useRef, useState } from 'react'
import { useModels } from '../model-context'
import { useElapsed } from '../useElapsed'
import { confirmToolCall, fetchActiveTest, fetchConversation, fetchSystemPrompts, streamChat } from '../api'
import ModelPanel from '../components/ModelPanel'
import ConversationList from '../components/ConversationList'
import MessageList from '../components/MessageList'
import Composer from '../components/Composer'
import NavTabs from '../components/NavTabs'
import SystemPromptPicker from '../components/SystemPromptPicker'
import { promptIdentity } from '../promptIdentity'

const TEST_ACTIVE_POLL_MS = 3000

// 일반 대화 모드 페이지.
// 대화 이력의 주인은 백엔드다 — 여기서는 conversationId만 들고 다니고,
// 메시지 배열은 화면에 보여주기 위한 사본일 뿐이다.
// 이 화면을 다시 열 때도 고른 대화 종류가 그대로여야 한다 — 이 브라우저에만 적어 두는 편의값이다
const MODE_KEY = 'chat.mode'
const PLAIN = 'plain'
const WITH_PROMPT = 'prompt'

const COMPRESS_KEY = 'chat.compress'
const TOOLS_KEY = 'chat.tools'

function readMode() {
  try {
    return localStorage.getItem(MODE_KEY) === WITH_PROMPT ? WITH_PROMPT : PLAIN
  } catch {
    return PLAIN // 저장을 못 읽어도 화면은 돈다
  }
}

// 켜고 끄는 값도 이 브라우저에 적어 둔다 — 새로 열 때마다 다시 켜는 일이 없게
function readFlag(key, fallback) {
  try {
    const saved = localStorage.getItem(key)
    return saved === null ? fallback : saved === '1'
  } catch {
    return fallback
  }
}

function writeFlag(key, on) {
  try {
    localStorage.setItem(key, on ? '1' : '0')
  } catch {
    /* 저장을 못 해도 이번 화면에서는 고른 대로 쓴다 */
  }
}

export default function ChatPage() {
  const { selectedId, error: modelsError } = useModels()
  // 시스템 프롬프트는 이 대화에 걸리는 조건이다 — 기본은 없음, 저장된 것 또는 직접 입력
  const [prompts, setPrompts] = useState([])
  const [promptSelection, setPromptSelection] = useState('')
  const [system, setSystem] = useState('')
  const [mode, setMode] = useState(readMode)
  const [temperature, setTemperature] = useState(0.7)
  const [compress, setCompress] = useState(() => readFlag(COMPRESS_KEY, true))
  // 도구를 쓰는 대화가 기본이다 — 끄면 그 선택이 다음에도 남는다
  const [toolsEnabled, setToolsEnabled] = useState(() => readFlag(TOOLS_KEY, true))
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [chatError, setChatError] = useState('')
  const [conversationId, setConversationId] = useState(null)
  const [compressedNotice, setCompressedNotice] = useState(false)
  const [listVersion, setListVersion] = useState(0)
  const [testActive, setTestActive] = useState(false)

  const abortRef = useRef(null)
  const prevModelRef = useRef(selectedId)
  const { elapsed, start, stop, reset: resetTimer } = useElapsed()

  const stopStream = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  // 화면을 비우고 다음 메시지부터 새 대화로 시작한다. 이미 저장된 대화는
  // 지워지지 않는다 — "저장된 대화" 목록에서 언제든 다시 이어할 수 있다.
  const startNewConversation = useCallback(() => {
    stopStream()
    setMessages([])
    setConversationId(null)
    setChatError('')
    setCompressedNotice(false)
    resetTimer()
  }, [stopStream, resetTimer])

  // 모델을 바꾸면 지금 화면의 대화는 그 모델 것이 아니게 된다 — 새로 시작한다.
  // (저장은 매 턴 이미 끝났으므로 잃는 것은 없다. 목록에서 다시 열 수 있다)
  useEffect(() => {
    if (prevModelRef.current !== selectedId) {
      prevModelRef.current = selectedId
      startNewConversation()
    }
  }, [selectedId, startNewConversation])

  const refreshPrompts = useCallback(
    () =>
      fetchSystemPrompts()
        .then((list) => {
          setPrompts(list)
          // `프롬프트 적용`인데 고른 것이 없으면 첫 프롬프트로 시작한다 — 빈 값은 `일반`과 다를 바가 없다
          setPromptSelection((chosen) => {
            if (chosen || readMode() !== WITH_PROMPT || list.length === 0) return chosen
            setSystem(list[0].content ?? '')
            return `saved:${list[0].name}`
          })
        })
        .catch((e) => setChatError(String(e.message || e))),
    [],
  )
  useEffect(() => {
    refreshPrompts()
  }, [refreshPrompts])

  // 성능 테스트가 돌고 있으면 일반 대화도 측정값을 오염시킨다(같은 로컬 자원을
  // 쓰므로) — 막지는 않고 경고만 띄운다. 새 테스트 시작 자체는
  // 백엔드가 409로 거부한다.
  useEffect(() => {
    let alive = true
    const check = () => {
      fetchActiveTest()
        .then((r) => {
          if (alive) setTestActive(Boolean(r.run_id))
        })
        .catch(() => {})
    }
    check()
    const timer = setInterval(check, TEST_ACTIVE_POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  // RatingStars가 저장을 마치면 서버가 돌려준 최신 메시지(rating/rating_note/rated_at)로
  // 화면의 사본을 맞춘다.
  function handleMessageRated(updatedMsg) {
    setMessages((msgs) => msgs.map((m) => (m.id === updatedMsg.id ? { ...m, ...updatedMsg } : m)))
  }

  // 종류를 바꾸면 그 자리에서 조건도 바꾼다 — `일반`인데 프롬프트가 걸려 있으면 무엇을 쓴 대화인지 알 수 없다
  function chooseMode(next) {
    setMode(next)
    try {
      localStorage.setItem(MODE_KEY, next)
    } catch {
      /* 저장을 못 해도 이번 화면에서는 고른 대로 쓴다 */
    }
    if (next === PLAIN) {
      setPromptSelection('')
      setSystem('')
    } else if (!promptSelection && prompts.length > 0) {
      setPromptSelection(`saved:${prompts[0].name}`)
      setSystem(prompts[0].content ?? '')
    }
  }

  async function loadConversation(id) {
    stopStream()
    setChatError('')
    setCompressedNotice(false)
    resetTimer()
    try {
      const conv = await fetchConversation(id)
      setConversationId(conv.id)
      setMessages(conv.messages.map((m) => ({ ...m, elapsedMs: null })))
      setSystem(conv.system || '')
      // 저장된 프롬프트로 시작한 대화면 그 이름으로, 본문만 있으면 직접 입력으로 되살린다
      const savedName = conv.system_prompt?.name
      if (conv.system) setMode(WITH_PROMPT)
      setPromptSelection(
        savedName && prompts.some((p) => p.name === savedName) ? `saved:${savedName}` : conv.system ? 'custom' : '',
      )
    } catch (e) {
      setChatError(String(e.message || e))
    }
  }

  // 도구 호출이 켜진 턴은 스트림 헤더만으로 확인 대기 여부를 알 수 없다
  // (헤더는 스트림 시작 전에 이미 정해지는데, 확인 대기는 도중에 결정된다).
  // 그래서 스트림이 끝나면 대화를 다시
  // 불러 저장된 진짜 모양(도구 호출 요약, 확인 대기, 숨겨진 도구 결과)으로
  // 화면을 맞춘다. elapsedMs는 백엔드에 저장되지 않는 프론트 전용 값이라
  // 새로 불러온 메시지에는 없다 — 방금 턴의 마지막 자연어 응답에만 다시 붙인다.
  async function reconcileAfterTurn(id, finalMs) {
    try {
      const conv = await fetchConversation(id)
      const mapped = conv.messages.map((m) => ({ ...m, elapsedMs: null }))
      const last = mapped[mapped.length - 1]
      if (last && last.role === 'assistant' && !last.tool_calls) {
        mapped[mapped.length - 1] = { ...last, elapsedMs: finalMs }
      }
      setMessages(mapped)
    } catch {
      // 재조회에 실패해도 지금까지 스트리밍된 화면은 그대로 둔다
    }
  }

  async function send() {
    const text = input.trim()
    if (!text || !selectedId || busy || pendingConfirmation) return

    const next = [...messages, { role: 'user', content: text }]
    setMessages([...next, { role: 'assistant', content: '', elapsedMs: null }])
    setInput('')
    setBusy(true)
    setChatError('')
    setCompressedNotice(false)
    start()

    let activeConversationId = conversationId
    abortRef.current = new AbortController()
    try {
      await streamChat(
        {
          model: selectedId,
          message: text,
          conversationId,
          system: promptSelection ? system.trim() || null : null,
          systemPromptName: promptIdentity(prompts, promptSelection, system).name,
          temperature,
          compress,
          toolsEnabled,
        },
        (delta) => {
          setMessages((msgs) => {
            const copy = msgs.slice()
            const last = copy[copy.length - 1]
            copy[copy.length - 1] = { ...last, content: last.content + delta }
            return copy
          })
        },
        ({ conversationId: newId, compressed, assistantMessageId }) => {
          if (newId) {
            activeConversationId = newId
            setConversationId(newId)
          }
          setCompressedNotice(compressed)
          if (assistantMessageId) {
            setMessages((msgs) => {
              const copy = msgs.slice()
              const last = copy[copy.length - 1]
              copy[copy.length - 1] = { ...last, id: assistantMessageId }
              return copy
            })
          }
        },
        abortRef.current.signal,
      )
    } catch (e) {
      if (e.name !== 'AbortError') setChatError(String(e.message || e))
    } finally {
      const finalMs = stop()
      if (toolsEnabled && activeConversationId) {
        await reconcileAfterTurn(activeConversationId, finalMs)
      } else {
        setMessages((msgs) => {
          const copy = msgs.slice()
          const last = copy[copy.length - 1]
          if (last && last.role === 'assistant') {
            copy[copy.length - 1] = { ...last, elapsedMs: finalMs }
          }
          return copy
        })
      }
      setBusy(false)
      abortRef.current = null
      setListVersion((v) => v + 1) // 방금 턴으로 제목/수정시각이 바뀌었을 수 있다
    }
  }

  // 위험 도구 확인 응답. 승인/거부 둘 다 이어지는 자연어 답변을
  // 스트리밍으로 돌려받으므로 send()와 같은 모양으로 처리한다.
  async function respondToConfirmation(approved) {
    if (!conversationId || busy) return
    setBusy(true)
    setChatError('')
    start()
    setMessages((msgs) => [...msgs, { role: 'assistant', content: '', elapsedMs: null }])

    abortRef.current = new AbortController()
    try {
      await confirmToolCall(
        { conversationId, approved },
        (delta) => {
          setMessages((msgs) => {
            const copy = msgs.slice()
            const last = copy[copy.length - 1]
            copy[copy.length - 1] = { ...last, content: last.content + delta }
            return copy
          })
        },
        () => {},
        abortRef.current.signal,
      )
    } catch (e) {
      if (e.name !== 'AbortError') setChatError(String(e.message || e))
    } finally {
      const finalMs = stop()
      await reconcileAfterTurn(conversationId, finalMs)
      setBusy(false)
      abortRef.current = null
      setListVersion((v) => v + 1)
    }
  }

  const lastMessage = messages[messages.length - 1]
  const pendingConfirmation = Boolean(
    lastMessage?.role === 'assistant' && lastMessage?.tool_calls?.length && lastMessage?.confirmation === 'pending',
  )

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>LLM Playground</h1>
        <NavTabs />

        {/* 프롬프트를 걸었는지가 대화의 조건이다 — 성능 테스트의 `실행 종류`와 같은 자리·같은 모양으로 둔다 */}
        <fieldset className="run-type-control chat-mode" disabled={busy}>
          <legend>대화 종류</legend>
          {[
            [PLAIN, '일반'],
            [WITH_PROMPT, '프롬프트 적용'],
          ].map(([value, label]) => (
            <label key={value} className="field-inline">
              <input
                type="radio"
                name="chat-mode"
                value={value}
                checked={mode === value}
                onChange={() => chooseMode(value)}
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>

        <ModelPanel />

        <ConversationList
          model={selectedId}
          activeId={conversationId}
          refreshKey={listVersion}
          onSelect={loadConversation}
          onDeleted={(id) => {
            if (id === conversationId) startNewConversation()
          }}
        />

        <label className="field">
          <span>Temperature: {temperature.toFixed(2)}</span>
          <input
            type="range"
            min="0"
            max="2"
            step="0.05"
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
          />
        </label>

        <label className="field field-inline">
          <input
            type="checkbox"
            checked={compress}
            onChange={(e) => {
              setCompress(e.target.checked)
              writeFlag(COMPRESS_KEY, e.target.checked)
            }}
          />
          <span>대화 요약 압축 사용</span>
        </label>

        <label className="field field-inline">
          <input
            type="checkbox"
            checked={toolsEnabled}
            onChange={(e) => {
              setToolsEnabled(e.target.checked)
              writeFlag(TOOLS_KEY, e.target.checked)
            }}
          />
          <span>도구 호출(tool-calling) 사용</span>
        </label>

        <button className="ghost" onClick={startNewConversation}>
          새 대화
        </button>
      </aside>

      <main className="chat">
        {testActive && (
          <div className="test-warning">
            성능 테스트 진행 중이라 지금 대화하면 측정값에 영향을 줍니다
          </div>
        )}

        <MessageList
          messages={messages}
          busy={busy}
          liveElapsed={elapsed}
          conversationId={conversationId}
          onMessageRated={handleMessageRated}
          onConfirm={respondToConfirmation}
        />

        {compressedNotice && (
          <div className="compress-notice">이전 대화가 요약되어 전달됐습니다</div>
        )}

        {(modelsError || chatError) && (
          <div className="error">{modelsError || chatError}</div>
        )}

        {pendingConfirmation && (
          <div className="test-warning">위 도구 호출을 승인/거부해야 다음 메시지를 보낼 수 있습니다</div>
        )}

        {/* 쓰는 자리 바로 위 — 전역 설정이 아니라 지금 보낼 말에 걸리는 조건이다 */}
        {mode === WITH_PROMPT && (
          <SystemPromptPicker
            prompts={prompts}
            selection={promptSelection}
            content={system}
            allowCustom
            allowNone={false}
            disabled={busy}
            onPromptsChanged={refreshPrompts}
            onChange={({ selection, content }) => {
              setPromptSelection(selection)
              setSystem(content)
            }}
          />
        )}

        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          onStop={stopStream}
          busy={busy}
          canSend={Boolean(input.trim()) && Boolean(selectedId) && !pendingConfirmation}
        />
      </main>
    </div>
  )
}
