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

// ── Paste document parsing ────────────────────────────────────────────────

// Matches the one-line wire format written by pastes.reference_line:
//   [pasted document: /path/to/file.md — N lines, X KB]
const PASTE_REF_RE = /\[pasted document: (.+?) — (\d+) lines, (.+?)\]/g

const PASTE_MIN_CHARS = 1200
const PASTE_MIN_LINES_DOC = 20

/**
 * parseUserMessage(text) → segments[]
 *
 * A segment is one of:
 *   {type: 'text', content: string}
 *   {type: 'doc', source: 'ref', path: string, lines: number, size: string,
 *                session_id: string, name: string}
 *   {type: 'doc', source: 'heuristic', content: string, lines: number}
 *
 * The 'ref' variant is used for messages that contain a [pasted document: …]
 * marker. The 'heuristic' variant is used for long plain-text blocks that
 * were pasted directly (no marker) — covers pre-existing history.
 */
export function parseUserMessage(text) {
  if (!text) return [{ type: 'text', content: '' }]

  const segments = []
  let lastIndex = 0
  let match

  PASTE_REF_RE.lastIndex = 0
  while ((match = PASTE_REF_RE.exec(text)) !== null) {
    // Text before this match
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index).trim()
      if (before) segments.push({ type: 'text', content: before })
    }
    const filePath = match[1]
    const lines = parseInt(match[2], 10)
    const size = match[3]
    // Extract session_id and name from the path
    // Path shape: …/pastes/<session_id>/<name>
    const pathParts = filePath.replace(/\\/g, '/').split('/')
    const name = pathParts[pathParts.length - 1]
    // session_id is the directory just before the filename
    const session_id = pathParts[pathParts.length - 2] || '_unassigned'
    segments.push({ type: 'doc', source: 'ref', path: filePath, lines, size, session_id, name })
    lastIndex = match.index + match[0].length
  }

  // Remaining text after last match
  const rest = text.slice(lastIndex)
  if (rest.trim()) {
    const restLines = rest.split('\n').length
    if (rest.length >= PASTE_MIN_CHARS || restLines >= PASTE_MIN_LINES_DOC) {
      // Long plain text — heuristic collapse
      segments.push({ type: 'doc', source: 'heuristic', content: rest, lines: restLines })
    } else {
      segments.push({ type: 'text', content: rest })
    }
  }

  return segments.length ? segments : [{ type: 'text', content: text }]
}
