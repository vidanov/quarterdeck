import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { errorOf } from '../api/client'
import * as api from '../api/sessions'
import * as settingsApi from '../api/settings'
import { useToast } from '../state/ToastContext'
import { useSessions } from '../state/SessionsContext'
import { timeAgo, showPath, STATUS_CONFIG } from '../utils'
import { usePasteAttachments } from '../hooks/usePasteAttachments'
import { PasteAttachments } from './PasteAttachments'

const QC_HISTORY_KEY = 'quick-create-history'
const QC_HISTORY_LIMIT = 50

function readQCHistory() {
  try {
    const raw = JSON.parse(localStorage.getItem(QC_HISTORY_KEY) || '[]')
    return Array.isArray(raw) ? raw.filter(x => typeof x === 'string') : []
  } catch {
    return []  // a corrupt entry is not worth losing the launcher over
  }
}

function QuickCreate({ onDispatch, suggestion, sessions }) {
  const [task, setTask] = useState('')
  const [qcHistory, setQcHistory] = useState(readQCHistory)
  const [qcHistIdx, setQcHistIdx] = useState(-1)
  const [selectedCwd, setSelectedCwd] = useState('')
  const [folderOpen, setFolderOpen] = useState(false)
  // The suggestion prop is captured once at app mount, so its Finder path goes
  // stale the moment the user switches Finder windows. Re-fetch when the picker
  // opens so "auto" always reflects the *current* frontmost Finder folder.
  const [liveSuggestion, setLiveSuggestion] = useState(null)
  const activeSuggestion = liveSuggestion || suggestion
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyIdx, setHistoryIdx] = useState(-1)
  const inputRef = useRef(null)
  const wrapRef = useRef(null)
  const {
    attachments: qcAttachments,
    onPaste: onQcPaste,
    removeAttachment: removeQcAttachment,
    clearAttachments: clearQcAttachments,
  } = usePasteAttachments({ sessionId: null })
  const [pinned, setPinned] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pinned-folders') || '[]') }
    catch { return [] }
  })

  // Load from backend settings on mount (survives app restart; localStorage is
  // the fast-path fallback while the async fetch completes).
  useEffect(() => {
    settingsApi.getSettings()
      .then(s => {
        const saved = s['pinned-folders']
        if (Array.isArray(saved) && saved.length > 0) {
          setPinned(saved)
          localStorage.setItem('pinned-folders', JSON.stringify(saved))
        }
      })
      .catch(() => {})
  }, [])

  const savePinned = (next) => {
    setPinned(next)
    localStorage.setItem('pinned-folders', JSON.stringify(next))
    settingsApi.saveSettings({ 'pinned-folders': next }).catch(() => {})
  }
  const pinFolder = (cwd, folder) => {
    if (pinned.some(p => p.cwd === cwd)) return
    savePinned([...pinned, { cwd, folder }])
  }
  const unpinFolder = (cwd) => savePinned(pinned.filter(p => p.cwd !== cwd))

  // Toggle the folder picker; on open, refresh the auto/Finder suggestion so it
  // reflects the current frontmost Finder window rather than the app-start one.
  const toggleFolder = () => {
    setFolderOpen(open => {
      const next = !open
      if (next) {
        settingsApi.getCwdSuggestion()
          .then(setLiveSuggestion)
          .catch(() => {})
      }
      return next
    })
    setHistoryOpen(false)
  }

  // The folder shown in the prompt prefix
  const activeFolderName = selectedCwd
    ? selectedCwd.split('/').pop()
    : activeSuggestion
      ? (activeSuggestion.path.split('/').pop() || '/')
      : null

  // Derive top folders from recent sessions, merged with pinned
  const popularFolders = useMemo(() => {
    const counts = {}
    if (sessions?.length) {
      for (const s of sessions) {
        if (!s.cwd || s.cwd === process.env.HOME) continue
        const folder = s.folder || s.cwd.split('/').pop()
        if (!folder) continue
        counts[s.cwd] = counts[s.cwd] || { cwd: s.cwd, folder, count: 0 }
        counts[s.cwd].count++
      }
    }
    const pinnedCwds = new Set(pinned.map(p => p.cwd))
    const popular = Object.values(counts)
      .filter(f => !pinnedCwds.has(f.cwd))
      .sort((a, b) => b.count - a.count)
      .slice(0, Math.max(0, 6 - pinned.length))
    return [...pinned.map(p => ({ ...p, pinned: true })), ...popular]
  }, [sessions, pinned])

  // Close dropdowns when clicking outside
  useEffect(() => {
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setFolderOpen(false)
        setHistoryOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const recentTasks = qcHistory.slice(0, 8)

  const doDispatch = (taskText, cwd) => {
    const t = taskText.trim()
    const readyAtts = qcAttachments.filter(a => !a.uploading)
    if (!t && readyAtts.length === 0) return
    if (t) {
      const updated = (t === qcHistory[0] ? qcHistory : [t, ...qcHistory]).slice(0, QC_HISTORY_LIMIT)
      setQcHistory(updated)
      localStorage.setItem(QC_HISTORY_KEY, JSON.stringify(updated))
    }
    setQcHistIdx(-1)
    setHistoryOpen(false)
    setFolderOpen(false)
    onDispatch({
      task: t,
      cwd: cwd !== undefined ? cwd : (selectedCwd || (activeSuggestion ? activeSuggestion.path : '') || ''),
      model: '',
      effort: '',
      agent: localStorage.getItem('launch-agent') || '',
      attachments: readyAtts,
    })
    setTask('')
    clearQcAttachments()
  }

  const handleKeyDown = (e) => {
    if (historyOpen && recentTasks.length) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        const next = Math.min(historyIdx + 1, recentTasks.length - 1)
        setHistoryIdx(next)
        setTask(recentTasks[next])
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        const next = historyIdx - 1
        setHistoryIdx(next)
        setTask(next < 0 ? '' : recentTasks[next])
        return
      }
      if (e.key === 'Escape') { setHistoryOpen(false); setHistoryIdx(-1); return }
      if (e.key === 'Enter' && historyIdx >= 0) {
        e.preventDefault()
        doDispatch(recentTasks[historyIdx])
        return
      }
    }
    // Fallback: ↑ walks history even without the dropdown
    if (!historyOpen) {
      if (e.key === 'ArrowUp' && qcHistory.length) {
        e.preventDefault()
        const next = Math.min(qcHistIdx + 1, qcHistory.length - 1)
        setQcHistIdx(next)
        setTask(qcHistory[next])
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        const next = qcHistIdx - 1
        setQcHistIdx(next)
        setTask(next < 0 ? '' : qcHistory[next])
      }
    }
  }

  return (
    <div className="qc-wrap" ref={wrapRef}>
      {qcAttachments.length > 0 && (
        <PasteAttachments attachments={qcAttachments} onRemove={removeQcAttachment} />
      )}
      <form
        className="qc-bar"
        onSubmit={(e) => { e.preventDefault(); doDispatch(task) }}
      >
        {/* Folder prefix — click to change */}
        <button
          type="button"
          className="qc-prefix"
          onClick={toggleFolder}
          title={selectedCwd || (activeSuggestion ? activeSuggestion.path : 'Choose folder')}
        >
          <span className="qc-prefix-folder">{activeFolderName || '~'}</span>
          <span className="qc-prefix-arrow">›</span>
        </button>

        {/* Main input */}
        <input
          ref={inputRef}
          className="qc-input"
          value={task}
          onChange={(e) => { setTask(e.target.value); setQcHistIdx(-1); setHistoryIdx(-1) }}
          onFocus={() => { if (recentTasks.length) setHistoryOpen(true) }}
          onKeyDown={handleKeyDown}
          onPaste={onQcPaste}
          placeholder="What should the agent do?"
          autoComplete="off"
          spellCheck="false"
        />

        <button
          type="submit"
          className="qc-send"
          disabled={!task.trim()}
          title="Launch (Enter)"
        >▶</button>
      </form>

      {/* Recent tasks dropdown */}
      {historyOpen && recentTasks.length > 0 && (
        <ul className="qc-history-dropdown">
          {recentTasks.map((t, i) => (
            <li
              key={i}
              className={`qc-history-item${i === historyIdx ? ' active' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); doDispatch(t) }}
              onMouseEnter={() => setHistoryIdx(i)}
            >
              <span className="qc-history-icon">↺</span>
              <span className="qc-history-text">{t}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Folder picker dropdown */}
      {folderOpen && (
        <div className="qc-folder-dropdown">
          <div className="qc-folder-list">
            {/* "Inferred" option — current Finder folder (refreshed on open) */}
            {activeSuggestion && (
              <button
                type="button"
                className={`qc-folder-item${!selectedCwd ? ' active' : ''}`}
                onClick={() => { setSelectedCwd(''); setFolderOpen(false) }}
              >
                <span className="qc-folder-icon">⟳</span>
                <span className="qc-folder-name">{activeSuggestion.path.split('/').pop() || '/'}</span>
                <span className="qc-folder-hint">{activeSuggestion.source === 'finder' ? 'Finder' : 'auto'}</span>
              </button>
            )}
            {popularFolders.map(f => (
              <div key={f.cwd} className="qc-folder-item-wrap">
                <button
                  type="button"
                  className={`qc-folder-item${selectedCwd === f.cwd ? ' active' : ''}`}
                  onClick={() => { setSelectedCwd(f.cwd); setFolderOpen(false) }}
                  title={f.cwd}
                >
                  <span className="qc-folder-icon">{f.pinned ? '📌' : '📁'}</span>
                  <span className="qc-folder-name">{f.folder}</span>
                </button>
                {!f.pinned && (
                  <button type="button" className="qc-pin-btn" title="Pin" onClick={() => pinFolder(f.cwd, f.folder)}>📌</button>
                )}
                {f.pinned && (
                  <button type="button" className="qc-pin-btn" title="Unpin" onClick={() => unpinFolder(f.cwd)}>×</button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Subsequence fuzzy match: every char of `q` appears in `text` in order.
// Returns a score (lower = better) or -1 for no match. Contiguous runs and
// early matches score better, so "auth" ranks "auth bug" above "a big truth".
// Lower score = better match (callers sort ascending). Substring matches live
// in a low score band and always outrank scattered subsequence matches, which
// live in a high band. Without this, a query like "FDP" fuzzy-matched f…d…p
// scattered across an unrelated path (e.g. "git diff … FlowPane") and buried
// the session whose path actually contains "FDP" — the exact substring scored
// worse because of the long-prefix gap penalty.
const SUBSEQ_BASE = 10000  // subsequence matches never beat a substring match

function fuzzyScore(text, q) {
  if (!q) return 0
  text = text.toLowerCase()
  q = q.toLowerCase()

  // Contiguous substring: the strong, expected match. Score by position so an
  // earlier hit wins; a word-boundary hit (start of a path segment or after a
  // space) is rewarded further. This mirrors the substring behaviour of the
  // Collections search that the user finds correct.
  const idx = text.indexOf(q)
  if (idx !== -1) {
    const atBoundary = idx === 0 || /[\s/\-_.]/.test(text[idx - 1])
    return idx + (atBoundary ? 0 : 50)
  }

  // Fallback: scattered subsequence. Kept for typo/loose matching, but pushed
  // into a band above every substring match so it only surfaces when nothing
  // matched contiguously.
  let ti = 0, score = 0, lastHit = -1
  for (let qi = 0; qi < q.length; qi++) {
    const c = q[qi]
    const found = text.indexOf(c, ti)
    if (found === -1) return -1
    if (lastHit >= 0) score += found - lastHit  // gap penalty
    else score += found                          // penalise late first hit
    lastHit = found
    ti = found + 1
  }
  return SUBSEQ_BASE + score
}

// ⌘K — fast local command palette. No network, no LLM. Fuzzy-matches open
// sessions, favourites, and static navigation commands. Keyboard-first:
// ↑/↓ to move, Enter to run the highlighted row. The AI concierge lives on ⌘J.
function PaletteBar({ open, onClose, onOpenSession, onCommand, favourites = [] }) {
  const { sessions } = useSessions()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
      setTimeout(() => inputRef.current?.focus(), 40)
    }
  }, [open])

  // Static navigation commands. `run` is dispatched to the parent by key.
  // `keywords` widen matching so natural queries ("clean up", "which to
  // archive", "tidy sessions") reach the right command without the exact label.
  const commands = useMemo(() => [
    { id: 'cmd:active', label: 'Go to Active grid', hint: 'view', keywords: 'grid running live', run: () => onCommand({ type: 'view', view: 'active' }) },
    { id: 'cmd:collections', label: 'Go to Collections', hint: 'view', keywords: 'groups snapshots recipes', run: () => onCommand({ type: 'view', view: 'collections' }) },
    { id: 'cmd:archive', label: 'Go to Archive', hint: 'view', keywords: 'archived history old past', run: () => onCommand({ type: 'archive' }) },
    { id: 'cmd:organize', label: 'Organize sessions — archive stale, keep important', hint: 'action', keywords: 'organize archive keep clean cleanup tidy stale prune declutter which close reduce', run: () => onCommand({ type: 'organize' }) },
    { id: 'cmd:stats', label: 'Go to Stats', hint: 'view', keywords: 'statistics usage report activity', run: () => onCommand({ type: 'view', view: 'stats' }) },
    { id: 'cmd:stacks', label: 'Go to Stacks', hint: 'view', keywords: 'queue tasks pending', run: () => onCommand({ type: 'view', view: 'stacks' }) },
    { id: 'cmd:settings', label: 'Open Settings', hint: 'view', keywords: 'preferences config hooks remote', run: () => onCommand({ type: 'view', view: 'settings' }) },
    { id: 'cmd:new', label: 'New session…', hint: 'action', keywords: 'launch start dispatch create', run: () => onCommand({ type: 'new' }) },
    { id: 'cmd:ask', label: 'Ask the assistant… (⌘J)', hint: 'AI', keywords: 'concierge question search find', run: () => onCommand({ type: 'ask' }) },
  ], [onCommand])

  // Build the ranked candidate list. Sessions + favourites + commands, all
  // scored against the query. Empty query shows commands then recent sessions.
  const rows = useMemo(() => {
    const q = query.trim()
    const favIds = new Set(favourites.map(f => f.id))
    const sessionRows = sessions.map(s => ({
      kind: 'session',
      id: s.id,
      label: s.title || s.name || s.id,
      hint: s.status || '',
      sub: showPath(s),
      raw: s,
    }))
    const favRows = favourites
      .filter(f => !sessions.some(s => s.id === f.id))
      .map(f => ({ kind: 'session', id: f.id, label: f.title || f.id, hint: '★', sub: showPath(f), raw: f }))
    const cmdRows = commands.map(c => ({ kind: 'command', id: c.id, label: c.label, hint: c.hint, keywords: c.keywords || '', run: c.run }))

    if (!q) {
      // No query: commands first, then most-recent sessions.
      return [...cmdRows, ...sessionRows.slice(0, 8)]
    }
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const scored = []
    for (const r of [...cmdRows, ...sessionRows, ...favRows]) {
      const hay = r.kind === 'session' ? `${r.label} ${r.sub}` : `${r.label} ${r.keywords || ''}`
      const lowHay = hay.toLowerCase()
      // Primary: contiguous substring wins, scattered subsequence as fallback.
      const sc = fuzzyScore(hay, q)
      if (sc >= 0) {
        scored.push({ ...r, _score: sc + (r.kind === 'command' ? 0 : 2) })
        continue
      }
      // Multi-word fallback: a query like "porsche workshop" rarely forms a
      // clean subsequence, but each word appears as a substring. Require every
      // word to be present, then rank by how early the first word lands. This
      // matches the Collections search, which the user finds correct.
      if (words.length > 1 && words.every(w => lowHay.includes(w))) {
        const pos = lowHay.indexOf(words[0])
        scored.push({ ...r, _score: 5000 + pos + (r.kind === 'command' ? 0 : 2) })
        continue
      }
      // Command-only fallback: surface a navigation command even on a partial
      // word match ("which keep archive" → Organize).
      if (r.kind === 'command' && words.length) {
        const hits = words.filter(w => lowHay.includes(w)).length
        if (hits > 0) {
          scored.push({ ...r, _score: (words.length - hits) * 5 })
        }
      }
    }
    scored.sort((a, b) => a._score - b._score)
    return scored.slice(0, 12)
  }, [query, sessions, favourites, commands])

  useEffect(() => { setActive(0) }, [query])

  const runRow = (row) => {
    if (!row) return
    if (row.kind === 'command') { row.run(); onClose() }
    else if (row.kind === 'session') { onOpenSession(row.id); onClose() }
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (rows.length === 0) {
        // Dead-end recovery: an archive/cleanup-flavoured query with no row
        // match runs Organize rather than doing nothing.
        const q = query.trim().toLowerCase()
        if (/(archive|keep|clean|tidy|stale|prune|organi|declutter|which)/.test(q)) {
          onCommand({ type: 'organize' }); onClose()
        }
        return
      }
      runRow(rows[active])
    }
    else if (e.key === 'Escape') { e.preventDefault(); onClose() }
  }

  // Keep the highlighted row in view.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null
  return (
    <div className="cmdbar-backdrop" onClick={onClose}>
      <div className="cmdbar" onClick={(e) => e.stopPropagation()}>
        <div className="cmdbar-input-row">
          <span className="cmdbar-icon">⌘K</span>
          <input ref={inputRef} className="cmdbar-input" value={query}
                 onChange={(e) => setQuery(e.target.value)}
                 onKeyDown={onKeyDown}
                 placeholder="Jump to a session, view, or command…" />
        </div>
        <div className="palette-list" ref={listRef}>
          {rows.length === 0 && (
            (() => {
              const q = query.trim().toLowerCase()
              const wantsOrganize = /(archive|keep|clean|tidy|stale|prune|organi|declutter|which)/.test(q)
              if (wantsOrganize) {
                return (
                  <div className="palette-empty palette-empty-action"
                       onClick={() => { onCommand({ type: 'organize' }); onClose() }}>
                    Organize sessions — archive stale, keep important
                    <span className="palette-hint">↵ run</span>
                  </div>
                )
              }
              return <div className="palette-empty">No matches. Press ⌘J to ask the assistant.</div>
            })()
          )}
          {rows.map((r, i) => (
            <div key={r.id} data-idx={i}
                 className={`palette-row ${i === active ? 'active' : ''}`}
                 onMouseEnter={() => setActive(i)}
                 onClick={() => runRow(r)}>
              <span className={`palette-kind palette-kind-${r.kind}`}>
                {r.kind === 'command' ? '›' : '▸'}
              </span>
              <span className="palette-label">{r.label}</span>
              {r.sub && <span className="palette-sub">{r.sub}</span>}
              {r.hint && <span className="palette-hint">{r.hint}</span>}
            </div>
          ))}
        </div>
        <div className="palette-footer">
          <span>↑↓ navigate · ↵ open · esc close · ⌘J assistant</span>
        </div>
      </div>
    </div>
  )
}

function CommandBar({ open, onClose, onAction, onOpenSession }) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [activity, setActivity] = useState('')
  const [tools, setTools] = useState([])
  const inputRef = useRef(null)
  const activityPoll = useRef(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setResult(null)
      setActivity('')
      setTools([])
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  // Poll for activity while loading
  useEffect(() => {
    if (!loading) {
      if (activityPoll.current) clearInterval(activityPoll.current)
      return
    }
    const poll = () => {
      settingsApi.getAssistActivity()
        .then(d => {
          if (d.activity) setActivity(d.activity)
          if (d.tools) setTools(d.tools)
        })
        .catch(() => {})
    }
    poll()
    activityPoll.current = setInterval(poll, 800)
    return () => clearInterval(activityPoll.current)
  }, [loading])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const submit = (e) => {
    e.preventDefault()
    if (!query.trim() || loading) return
    setLoading(true)
    setResult(null)
    setActivity('Starting…')
    setTools([])
    settingsApi.askAssistant(query.trim())
      .then(data => { setResult(data); setLoading(false); setActivity(''); setTools([]) })
      .catch(() => {
        setResult({ type: 'error', title: 'Connection failed', narrative: 'Could not reach the assistant.', items: [], actions: [] })
        setLoading(false)
        setActivity('')
        setTools([])
      })
  }

  const handleAction = (action) => {
    if (onAction) onAction(action)
    if (action.action === 'dispatch' || action.action === 'resume') {
      onClose()
    }
  }

  const handleItemClick = (item) => {
    // If item has an id, it's a session — open it
    if (item.id && onOpenSession) {
      onOpenSession(item.id)
      onClose()
    }
  }

  if (!open) return null
  return (
    <div className="cmdbar-backdrop" onClick={onClose}>
      <div className="cmdbar" onClick={(e) => e.stopPropagation()}>
        <form className="cmdbar-input-row" onSubmit={submit}>
          <span className="cmdbar-icon">⌘</span>
          <input ref={inputRef} className="cmdbar-input" value={query}
                 onChange={(e) => setQuery(e.target.value)}
                 placeholder="Ask anything… find sessions, stats, launch work…"
                 disabled={loading} />
          {loading && <span className="cmdbar-spinner">⟳</span>}
        </form>
        {loading && (activity || tools.length > 0) && (
          <div className="cmdbar-activity">
            {activity && <div className="cmdbar-activity-text">{activity}</div>}
            {tools.length > 0 && (
              <div className="cmdbar-activity-tools">
                {tools.map((t, i) => (
                  <span key={i} className="cmdbar-tool-chip">
                    {t.type === 'api' ? '🔍 ' : t.type === 'shell' ? '$ ' : t.type === 'read' ? '📖 ' : '⚙ '}
                    {t.detail}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {result && (
          <div className="cmdbar-results">
            <div className="cmdbar-title">{result.title}</div>
            {result.narrative && <p className="cmdbar-narrative">{result.narrative}</p>}
            {result.items && result.items.length > 0 && (
              <div className="cmdbar-items">
                {result.items.map((item, i) => {
                  // Handle stat-style items (key-value pairs)
                  if (item.label && item.value !== undefined) {
                    return (
                      <div key={i} className="cmdbar-stat-row">
                        <span className="cmdbar-stat-label">{item.label}</span>
                        <span className="cmdbar-stat-value">{item.value}</span>
                      </div>
                    )
                  }
                  // Handle items with just name/value (for charts)
                  if (item.name && (item.messages || item.sessions || item.count) && !item.id) {
                    const value = item.messages || item.sessions || item.count
                    const maxValue = Math.max(...result.items.map(x => x.messages || x.sessions || x.count || 0))
                    const pct = maxValue > 0 ? (value / maxValue) * 100 : 0
                    return (
                      <div key={i} className="cmdbar-bar-row">
                        <span className="cmdbar-bar-label">{item.name}</span>
                        <div className="cmdbar-bar-track">
                          <div className="cmdbar-bar-fill" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="cmdbar-bar-value">{value.toLocaleString()}</span>
                      </div>
                    )
                  }
                  // Handle session-style items
                  return (
                    <div key={i} className={`cmdbar-item ${item.id ? 'cmdbar-item-clickable' : ''}`}
                         onClick={() => item.id && handleItemClick(item)}
                         title={item.id ? 'Click to open session' : ''}>
                      <span className="cmdbar-item-title">{item.title || item.name || item.id}</span>
                      {item.cwd && <span className="cmdbar-item-meta">{showPath(item)}</span>}
                      {item.status && <span className={`cmdbar-item-status cmdbar-status-${item.status}`}>{item.status}</span>}
                      {item.updated_at && timeAgo(item.updated_at) && <span className="cmdbar-item-meta">{timeAgo(item.updated_at)}</span>}
                      {(item.sessions || item.turns) && (
                        <span className="cmdbar-item-meta">
                          {item.sessions && `${item.sessions} sessions`}
                          {item.turns && ` · ${item.turns} turns`}
                          {item.last_activity && ` · ${item.last_activity}`}
                        </span>
                      )}
                      {item.id && (
                        <button className="cmdbar-item-open-btn"
                                onClick={e => { e.stopPropagation(); handleItemClick(item) }}
                                title="Open in detail panel">
                          Open
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
            {result.actions && result.actions.length > 0 && (
              <div className="cmdbar-actions">
                {result.actions.map((action, i) => (
                  <button key={i} className="cmdbar-action-btn" onClick={() => handleAction(action)}>
                    {action.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {!result && !loading && (
          <div className="cmdbar-hints">
            <span>Try: "what's running?" · "weekly report" · "find CDK sessions" · "start fixing auth in vptb"</span>
          </div>
        )}
      </div>
    </div>
  )
}

function AttentionBar({ sessions, selectedId, onPick }) {
  const needing = sessions.filter(s =>
    s.id !== selectedId && s.control !== 'starting' &&
    (s.status === 'awaiting-approval' || s.status === 'error'))
  if (!needing.length) return null
  return (
    <div className="attention-bar">
      <span className="attention-label">Needs you</span>
      {needing.slice(0, 6).map(s => {
        const cfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.done
        return (
          <button key={s.id} className="attention-badge" onClick={() => onPick(s)}
                  title={`${s.title || s.name} — ${cfg.label}`}>
            <span className="attention-dot" style={{ background: cfg.color }} />
            <span className="attention-name">{s.folder || s.name}</span>
          </button>
        )
      })}
      {needing.length > 6 && <span className="attention-more">+{needing.length - 6}</span>}
    </div>
  )
}

function NewSessionLauncher({ options, onDispatch, onCancel, initialCwd }) {
  const [task, setTask] = useState('')
  const [cwd, setCwd] = useState(initialCwd || '')
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState('')
  // Remembered across launches: picking an agent is a deliberate choice, and
  // re-picking it every time is the kind of friction that stops people using it.
  const [agent, setAgent] = useState(() => localStorage.getItem('launch-agent') || '')
  const [preCommand, setPreCommand] = useState('')
  const [showPre, setShowPre] = useState(false)
  const [recording, setRecording] = useState(false)
  const [suggested, setSuggested] = useState(null)
  // Template picker
  const [showTemplatePicker, setShowTemplatePicker] = useState(false)
  const [templates, setTemplates] = useState(null) // null = not loaded yet
  const [selectedTemplate, setSelectedTemplate] = useState(null)
  const [templateVars, setTemplateVars] = useState({})
  const [templateTask, setTemplateTask] = useState('')  // for snapshot templates with no built-in task
  const [intakeRunning, setIntakeRunning] = useState(false)
  const recognitionRef = useRef(null)
  const inputRef = useRef(null)
  const {
    attachments: launchAttachments,
    onPaste: onLaunchPaste,
    removeAttachment: removeLaunchAttachment,
    clearAttachments: clearLaunchAttachments,
  } = usePasteAttachments({ sessionId: null })

  useEffect(() => { if (inputRef.current) inputRef.current.focus() }, [])

  // Show where the session will actually land before launching, rather than
  // silently starting somewhere the user did not choose.
  useEffect(() => {
    settingsApi.getCwdSuggestion().then(setSuggested).catch(() => {})
  }, [])

  // A directory can carry its own agents in .kiro/agents, and one of those
  // shadows a global agent of the same name — so the list depends on where the
  // session will start, and has to be re-read when that changes.
  const [localAgents, setLocalAgents] = useState(null)
  useEffect(() => {
    if (!cwd) { setLocalAgents(null); return }
    let live = true
    settingsApi.getAgents(cwd)
      .then(d => { if (live) setLocalAgents(d.agents || null) })
      .catch(() => {})
    return () => { live = false }
  }, [cwd])

  const submit = (e) => {
    e.preventDefault()
    const readyAtts = launchAttachments.filter(a => !a.uploading)
    if (!task.trim() && readyAtts.length === 0) return
    onDispatch({ task, cwd, model, effort, agent, pre_command: preCommand, attachments: readyAtts })
    setTask('')
    clearLaunchAttachments()
  }

  const chooseAgent = (name) => {
    setAgent(name)
    if (name) localStorage.setItem('launch-agent', name)
    else localStorage.removeItem('launch-agent')
  }

  const pickFolder = () => {
    settingsApi.pickFolder()
      .then(data => { if (data.path) setCwd(data.path) })
  }

  const openTemplatePicker = () => {
    if (!showTemplatePicker) {
      api.listTemplates().then(d => setTemplates(d.templates || [])).catch(() => setTemplates([]))
    }
    setShowTemplatePicker(v => !v)
    setSelectedTemplate(null)
    setTemplateVars({})
  }

  const selectTemplate = (t) => {
    setSelectedTemplate(t)
    const vars = {}
    ;(t.vars || []).forEach(v => { vars[v.name] = '' })
    setTemplateVars(vars)
    setTemplateTask('')
  }

  const launchFromTemplate = () => {
    if (!selectedTemplate) return
    setIntakeRunning(true)
    api.intake({
      template: selectedTemplate.id,
      vars: templateVars,
      task: templateTask || undefined,
      cwd: cwd || selectedTemplate.cwd || '',
      model: model || '',
      effort: effort || '',
      agent: agent || '',
    })
      .then(d => {
        if (d.ok) {
          setShowTemplatePicker(false)
          setSelectedTemplate(null)
          setTemplateVars({})
          setTemplateTask('')
          onCancel()
        } else {
          alert(d.error || 'Intake failed')
        }
      })
      .catch(e => alert(e.message || 'Intake failed'))
      .finally(() => setIntakeRunning(false))
  }

  // The description is the only thing that distinguishes one agent from another
  // at a glance, and a <select> has nowhere to put it except the tooltip.
  const agents = localAgents || options.agents || []
  const chosen = agents.find(a => a.name === agent)
  const agentTitle = chosen
    ? `${chosen.name}${chosen.description ? ` — ${chosen.description}` : ''}`
    : 'Agent (prompt, tools and MCP servers the session runs with)'

  const toggleRecording = () => {
    if (recording) {
      if (recognitionRef.current) recognitionRef.current.stop()
      setRecording(false)
      return
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      alert('Speech recognition not supported in this browser')
      return
    }
    const recognition = new SpeechRecognition()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = 'en-US'
    recognition.onresult = (event) => {
      setTask(Array.from(event.results).map(r => r[0].transcript).join(''))
    }
    recognition.onend = () => setRecording(false)
    recognition.onerror = () => setRecording(false)
    recognitionRef.current = recognition
    recognition.start()
    setRecording(true)
  }

  const folderName = cwd ? cwd.split('/').pop() : null

  return (
    <form className="launcher" onSubmit={submit}>
      {launchAttachments.length > 0 && (
        <PasteAttachments attachments={launchAttachments} onRemove={removeLaunchAttachment} />
      )}
      <textarea
        ref={inputRef}
        className="launcher-input"
        value={task}
        rows={2}
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
        onChange={(e) => setTask(e.target.value)}
        onPaste={onLaunchPaste}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) submit(e)
          if (e.key === 'Escape') onCancel()
        }}
        placeholder="What should the new agent do?  (Enter to launch, Shift+Enter for a new line)"
      />
      <div className="launcher-row">
        {folderName ? (
          <span className="dispatch-folder-chip" onClick={pickFolder} title={cwd}>
            📁 {folderName} <span className="chip-clear" onClick={(e) => { e.stopPropagation(); setCwd('') }}>×</span>
          </span>
        ) : (
          <button type="button" className="dispatch-pick" onClick={pickFolder} title={suggested ? suggested.path : 'Choose a directory'}>
            📁 {suggested
              ? `${suggested.path.split('/').pop() || '/'}${suggested.source === 'finder' ? ' (Finder)' : ' (home)'}`
              : 'Pick folder'}
          </button>
        )}
        <select className="launcher-select" value={agent} onChange={(e) => chooseAgent(e.target.value)}
                title={agentTitle}>
          <option value="">
            {options.default_agent ? `agent: ${options.default_agent} (default)` : 'default agent'}
          </option>
          {agents.map(a => (
            <option key={a.name} value={a.name}>
              {a.name}{a.source === 'workspace' ? ' (this folder)' : ''}
            </option>
          ))}
        </select>
        <select className="launcher-select" value={model} onChange={(e) => setModel(e.target.value)} title="Model">
          <option value="">default model</option>
          {(options.models || []).map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select className="launcher-select" value={effort} onChange={(e) => setEffort(e.target.value)} title="Thinking effort">
          <option value="">default effort</option>
          {(options.efforts || []).map(x => <option key={x} value={x}>{x}</option>)}
        </select>
        <button type="button" className="launcher-cancel" onClick={() => setShowPre(v => !v)}
                title="Run a shell command before kiro-cli starts">
          {showPre ? '⌄ shell' : '› shell'}
        </button>
        <button type="button" className={`launcher-cancel${showTemplatePicker ? ' active' : ''}`}
                onClick={openTemplatePicker} title="Start from a saved template">
          📋 template
        </button>
        <span className="launcher-spacer" />
        <button type="button" className={`dispatch-mic ${recording ? 'recording' : ''}`} onClick={toggleRecording} title="Voice input">
          {recording ? '⏹' : '🎤'}
        </button>
        <button type="button" className="launcher-cancel" onClick={onCancel}>Cancel</button>
        <button className="dispatch-btn" type="submit" disabled={!task.trim()}>▶ Launch</button>
      </div>
      {showPre && (
        <input
          className="launcher-input launcher-pre"
          value={preCommand}
          onChange={(e) => setPreCommand(e.target.value)}
          placeholder="Shell to run first, e.g. cd packages/api && nvm use 20"
          spellCheck={false}
        />
      )}
      {showTemplatePicker && (
        <div className="template-picker">
          {templates === null && <p className="template-picker-hint">Loading templates…</p>}
          {templates !== null && templates.length === 0 && (
            <p className="template-picker-hint">
              No templates yet — open a session transcript and click 📋 on a user turn.
            </p>
          )}
          {templates !== null && templates.length > 0 && !selectedTemplate && (
            <div className="template-picker-list">
              {templates.map(t => (
                <button key={t.id} type="button" className="template-picker-item"
                        onClick={() => selectTemplate(t)}>
                  <span className="template-name">{t.name}</span>
                  {t.snapshot_id && <span className="template-badge">📎 context</span>}
                  {t.cwd && <span className="template-cwd">{t.cwd.split('/').pop()}</span>}
                </button>
              ))}
            </div>
          )}
          {selectedTemplate && (
            <div className="template-picker-vars">
              <div className="template-picker-selected">
                <span className="template-name">{selectedTemplate.name}</span>
                <button type="button" className="sat-btn-cancel"
                        onClick={() => setSelectedTemplate(null)}>← back</button>
              </div>
              {selectedTemplate.task && (
                <p className="template-task template-task-preview">{selectedTemplate.task.slice(0, 200)}</p>
              )}
              {!selectedTemplate.task && selectedTemplate.snapshot_id && (
                <div className="template-vars-form">
                  <label className="sat-label">
                    What should the agent do?<span className="template-required"> *</span>
                    <textarea
                      className="sat-input"
                      rows={3}
                      value={templateTask}
                      onChange={e => setTemplateTask(e.target.value)}
                      placeholder="Describe the task to continue from this context…"
                    />
                  </label>
                </div>
              )}
              {(selectedTemplate.vars || []).length > 0 && (
                <div className="template-vars-form">
                  {selectedTemplate.vars.map(v => (
                    <label key={v.name} className="sat-label">
                      {v.name}{v.required && <span className="template-required"> *</span>}
                      {v.description && <span className="sat-hint-inline">{v.description}</span>}
                      <input
                        className="sat-input"
                        value={templateVars[v.name] || ''}
                        onChange={e => setTemplateVars(d => ({ ...d, [v.name]: e.target.value }))}
                        placeholder={v.description || v.name}
                      />
                    </label>
                  ))}
                </div>
              )}
              <button type="button" className="dispatch-btn" disabled={intakeRunning || (!selectedTemplate.task && !!selectedTemplate.snapshot_id && !templateTask.trim())}
                      onClick={launchFromTemplate}>
                {intakeRunning ? 'Launching…' : '▶ Launch from template'}
              </button>
            </div>
          )}
        </div>
      )}
    </form>
  )
}

// Panel width bounds. The floor keeps the composer usable; the ceiling always
// leaves room for at least one column of cards, so dragging can never hide the
// list the panel was opened from.


// ⌘K → "Organize sessions" review panel. Fetches the backend's usage-pattern
// scoring, preselects the archive candidates, and shows *why* each session is
// kept or archived so the user can adjust the selection and give feedback on
// the heuristic. "Archive" = kill the session; it stays resumable in Archive.
function OrganizePanel({ open, onClose, onArchived }) {
  const notify = useToast()
  const [keep, setKeep] = useState(5)
  const [includeCaptain, setIncludeCaptain] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(() => new Set())  // ids to archive
  const [working, setWorking] = useState(false)

  const load = useCallback((k, inclCaptain) => {
    setLoading(true)
    setData(null)
    api.organizePreview(k, inclCaptain)
      .then(d => {
        setData(d)
        // Preselect exactly what the backend proposed to archive.
        setSelected(new Set((d.archive_sessions || []).map(s => s.id)))
      })
      .catch(() => notify('Could not load organize preview', 'error'))
      .finally(() => setLoading(false))
  }, [notify])

  useEffect(() => {
    if (open) { setKeep(5); setIncludeCaptain(false); load(5, false) }
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const toggle = (id) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const changeKeep = (k) => {
    const clamped = Math.max(3, Math.min(7, k))
    setKeep(clamped)
    load(clamped, includeCaptain)
  }

  const toggleCaptain = () => {
    const next = !includeCaptain
    setIncludeCaptain(next)
    load(keep, next)
  }

  const doArchive = () => {
    const ids = [...selected]
    if (ids.length === 0) return
    setWorking(true)
    // Sequential kills with a small gap — each ends a tmux process and races
    // for correlation if fired all at once.
    ids.reduce((chain, id, i) => chain.then(() => new Promise(resolve => {
      setTimeout(() => { api.killSession(id).then(resolve).catch(resolve) }, i * 150)
    })), Promise.resolve()).then(() => {
      setWorking(false)
      notify(`Archived ${ids.length} session${ids.length === 1 ? '' : 's'} — resumable from Archive`)
      if (onArchived) onArchived()
      onClose()
    })
  }

  if (!open) return null

  const rows = data
    ? [...(data.keep_sessions || []), ...(data.archive_sessions || [])]
    : []

  return (
    <div className="cmdbar-backdrop" onClick={onClose}>
      <div className="cmdbar organize-panel" onClick={(e) => e.stopPropagation()}>
        <div className="organize-head">
          <span className="cmdbar-icon">⌘K</span>
          <span className="organize-title">Organize sessions</span>
          <label className="organize-captain-toggle" title="Include machine-owned Captain / Crew sessions in the archive pass">
            <input type="checkbox" checked={includeCaptain} disabled={loading}
                   onChange={toggleCaptain} />
            include Captain
          </label>
          <span className="organize-keep-ctrl">
            keep
            <button className="organize-step" disabled={keep <= 3 || loading}
                    onClick={() => changeKeep(keep - 1)}>−</button>
            <strong>{keep}</strong>
            <button className="organize-step" disabled={keep >= 7 || loading}
                    onClick={() => changeKeep(keep + 1)}>+</button>
            alive
          </span>
        </div>

        {loading && <div className="palette-empty">Scoring sessions by usage pattern…</div>}

        {!loading && data && (
          <>
            <div className="organize-summary">
              Keeping <strong>{(data.keep_sessions || []).length}</strong>,
              archiving <strong>{selected.size}</strong> of {(data.archive_sessions || []).length} proposed.
              Archiving ends the session but keeps it resumable in Archive.
              {!includeCaptain && data.summary?.captain_skipped > 0 && (
                <span className="organize-captain-note">
                  {' '}{data.summary.captain_skipped} Captain session{data.summary.captain_skipped === 1 ? '' : 's'} left out.
                </span>
              )}
            </div>
            <div className="organize-list">
              {rows.map(s => {
                const isArchiveCandidate = s.decision === 'archive'
                const isProtected = s.score === 999
                const checked = selected.has(s.id)
                return (
                  <label key={s.id}
                         className={`organize-row ${isArchiveCandidate ? 'is-archive' : 'is-keep'}`}
                         title={isProtected ? 'Protected — cannot be archived here' : ''}>
                    <input type="checkbox"
                           checked={checked}
                           disabled={isProtected}
                           onChange={() => toggle(s.id)} />
                    <span className={`organize-decision organize-decision-${checked ? 'archive' : 'keep'}`}>
                      {isProtected ? 'protected' : (checked ? 'archive' : 'keep')}
                    </span>
                    <span className="organize-row-main">
                      <span className="organize-row-title">{s.title}</span>
                      <span className="organize-row-reason">{s.reason}</span>
                    </span>
                    <span className="organize-row-meta">
                      {s.cwd_display && <span className="organize-row-cwd">{s.cwd_display}</span>}
                      {typeof s.turns === 'number' && <span>{s.turns} turns</span>}
                      {!isProtected && typeof s.score === 'number' && (
                        <span className="organize-row-score" title="keep-importance score">{s.score.toFixed(1)}</span>
                      )}
                    </span>
                  </label>
                )
              })}
            </div>
            <div className="organize-actions">
              <span className="organize-hint">Uncheck any you want to keep. Feedback on why? The reason column drives it.</span>
              <button className="launcher-cancel" onClick={onClose}>Cancel</button>
              <button className="dispatch-btn" disabled={working || selected.size === 0}
                      onClick={doArchive}>
                {working ? 'Archiving…' : `Archive ${selected.size} selected`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export { QuickCreate, CommandBar, PaletteBar, NewSessionLauncher, OrganizePanel }
