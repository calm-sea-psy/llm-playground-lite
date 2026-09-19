// 메시지 입력창 + 전송/중지 버튼.
export default function Composer({ value, onChange, onSend, onStop, busy, canSend }) {
  function onKeyDown(e) {
    // isComposing: 한글 입력기 조합 중의 Enter는 전송이 아니라 조합 확정이다.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      onSend()
    }
  }

  return (
    <div className="composer">
      <textarea
        rows={1}
        value={value}
        placeholder="메시지 입력 (Enter 전송, Shift+Enter 줄바꿈)"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {busy ? (
        <button className="stop" onClick={onStop}>
          중지
        </button>
      ) : (
        <button onClick={onSend} disabled={!canSend}>
          전송
        </button>
      )}
    </div>
  )
}
