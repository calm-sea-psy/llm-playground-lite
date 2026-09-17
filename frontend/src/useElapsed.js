import { useCallback, useEffect, useRef, useState } from 'react'

// 전송~응답완료 경과 시간(ms)을 실시간으로 노출한다. 표시 전용이다 —
// 성능 테스트의 실제 속도 지표는 백엔드에서 측정한다.
export function useElapsed() {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(0)
  const timerRef = useRef(null)

  const clear = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
  }, [])

  const start = useCallback(() => {
    clear()
    startRef.current = Date.now()
    setElapsed(0)
    timerRef.current = setInterval(() => {
      setElapsed(Date.now() - startRef.current)
    }, 200)
  }, [clear])

  // 타이머를 멈추고 최종 경과 시간(ms)을 고정·반환한다.
  const stop = useCallback(() => {
    clear()
    const final = startRef.current ? Date.now() - startRef.current : 0
    setElapsed(final)
    return final
  }, [clear])

  const reset = useCallback(() => {
    clear()
    startRef.current = 0
    setElapsed(0)
  }, [clear])

  useEffect(() => clear, [clear])

  return { elapsed, start, stop, reset }
}
