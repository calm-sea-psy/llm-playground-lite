// 표시용 포매터.

export function formatBytes(n) {
  if (n == null) return null
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  return `${Math.round(n / 1e3)} KB`
}

// 컨텍스트 토큰 수 → "128K" 형태
export function formatContext(n) {
  if (n == null) return null
  if (n >= 1024) return `${Math.round(n / 1024)}K`
  return String(n)
}

export function formatDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

// 경과 시간 ms → "M:SS" (한 시간 넘으면 "H:MM:SS")
export function formatElapsed(ms) {
  const total = Math.floor((ms || 0) / 1000)
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (x) => String(x).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}
