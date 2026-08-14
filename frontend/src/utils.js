// Shared utilities and constants for Quarterdeck components.

export const STATUS_CONFIG = {
  thinking: { color: '#16a34a', label: '⟳ Thinking', bg: '#f0fdf4' },
  running: { color: '#16a34a', label: '● Running', bg: '#f0fdf4' },
  'awaiting-approval': { color: '#ca8a04', label: '◉ Awaiting', bg: '#fefce8' },
  idle: { color: '#64748b', label: '○ Idle', bg: '#f8fafc' },
  done: { color: '#94a3b8', label: '○ Done', bg: '#ffffff' },
  error: { color: '#dc2626', label: '✕ Error', bg: '#fef2f2' },
}

// Paths arrive twice: `cwd` is the real path and the only thing safe to send
// back to the API, `cwd_display` is the abbreviated form meant for reading.
export const showPath = (o) => o?.cwd_display || o?.cwd || ''

export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
  }
  const el = document.createElement('textarea')
  el.value = text
  el.style.position = 'fixed'
  el.style.left = '-9999px'
  document.body.appendChild(el)
  el.select()
  document.execCommand('copy')
  document.body.removeChild(el)
}

export function timeAgo(dateStr) {
  if (!dateStr) return ''
  const ts = new Date(dateStr).getTime()
  if (isNaN(ts)) return ''
  const diff = (Date.now() - ts) / 1000
  if (diff < 0) return ''
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`
  return `${Math.floor(diff / 86400)}d`
}

// Panel and pane sizing constants
export const PANEL_MIN = 360
export const PANEL_DEFAULT = 480
export const CELL_SAMPLE = 40
export const PANE_MIN_COLS = 20
export const PANE_MIN_ROWS = 6
export const PANE_SCROLLBACK = 400
export const PANE_FOLLOW_SLACK = 32

export const clampPanelWidth = (px) =>
  Math.round(Math.max(PANEL_MIN, Math.min(px, Math.max(PANEL_MIN, window.innerWidth - 340))))

// Composer history
export const HISTORY_LIMIT = 50
export const historyKey = (sessionId) => `composer-history:${sessionId}`

export function readHistory(sessionId) {
  try {
    const raw = JSON.parse(localStorage.getItem(historyKey(sessionId)) || '[]')
    return Array.isArray(raw) ? raw.filter(x => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function writeHistory(sessionId, history) {
  // Write to localStorage for in-session access
  try {
    localStorage.setItem(historyKey(sessionId), JSON.stringify(history))
  } catch { /* quota or private mode */ }
  // Write to backend file so it survives WKWebView process restarts
  // (WKWebView doesn't flush localStorage synchronously on window close)
  fetch('/api/prefs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [historyKey(sessionId)]: history }),
  }).catch(() => {})
}

export async function loadHistoryFromPrefs(sessionId) {
  // Called on session switch to restore history and draft from backend into localStorage.
  // Returns the history array if found, null otherwise.
  try {
    const d = await fetch('/api/prefs').then(r => r.json())
    const histKey = historyKey(sessionId)
    const draftKey = `draft:${sessionId}`
    // Seed draft into localStorage
    if (typeof d[draftKey] === 'string' && d[draftKey]) {
      localStorage.setItem(draftKey, d[draftKey])
    } else if (d[draftKey] === null) {
      localStorage.removeItem(draftKey)
    }
    // Return history
    if (d[histKey] && Array.isArray(d[histKey])) {
      localStorage.setItem(histKey, JSON.stringify(d[histKey]))
      return d[histKey]
    }
  } catch {}
  return null
}
