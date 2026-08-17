import React, { useState, useRef, useMemo, useEffect } from 'react'
import * as api from '../api/sessions'
import { useToast } from '../state/ToastContext'
import { timeAgo, showPath, STATUS_CONFIG, readHistory, writeHistory, historyKey, HISTORY_LIMIT, loadHistoryFromPrefs } from '../utils'
import { usePasteAttachments } from '../hooks/usePasteAttachments'
import { PasteAttachments } from './PasteAttachments'

const CARD_DOUBLE_CLICK_MS = 180
const CONTROL_LABEL = {
  starting: { text: '◆ starting…', title: 'Process launched; waiting for kiro-cli to report its session id' },
  managed: { text: '◆ managed', title: 'Running under tmux — you can send input' },
  foreign: { text: '◇ foreign', title: 'Started outside the app — read-only until taken over' },
  archived: { text: '· archived', title: 'Not running' },
  crew: { text: '⬡ crew', title: 'KiroCrew ACP session — read-only' },
  acp: { text: '◉ ACP', title: 'V3 session — input routed via ACP' },
}

function CardReply({ session, held, onRespondApproval, onRespondPrompt, onSendText }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  // Per-session input history, shared with the DetailPanel composer via the
  // same localStorage key so ↑/↓ works consistently wherever you reply from.
  const [history, setHistory] = useState(() => readHistory(session.id))
  const [historyIndex, setHistoryIndex] = useState(-1)
  const {
    attachments: cardAttachments,
    onPaste: onCardPaste,
    removeAttachment: removeCardAttachment,
    clearAttachments: clearCardAttachments,
  } = usePasteAttachments({ sessionId: session.id })
  const stop = (e) => e.stopPropagation()

  // Keep history in sync if the session id somehow changes without remounting.
  useEffect(() => {
    loadHistoryFromPrefs(session.id).then(fromBackend => {
      setHistory(fromBackend || readHistory(session.id))
    })
    setHistoryIndex(-1)
  }, [session.id])

  // A held tool call is structural — the hook is waiting on a decision, and the
  // agent is stopped until it gets one.
  if (held) {
    return (
      <div className="card-reply" onClick={stop}>
        <button className="card-reply-allow"
                onClick={() => onRespondApproval(session.id, held.request_id, true)}>
          Allow
        </button>
        <button className="card-reply-deny"
                onClick={() => onRespondApproval(session.id, held.request_id, false)}>
          Deny
        </button>
        <button className="card-reply-trust"
                title="Allow this and all future tool calls for 30 minutes"
                onClick={() => { api.setTrust(session.id, 30); onRespondApproval(session.id, held.request_id, true) }}>
          Trust 30m
        </button>
        <span className="card-reply-what" title={JSON.stringify(held.tool_input)}>
          {held.tool_name || 'tool'}
        </span>
      </div>
    )
  }

  // The TUI permission menu. Answered by key sequence, not by text — sending
  // prose into an arrow-key menu picks an option at random, which is why this
  // offers buttons and no field.
  if (session.status === 'awaiting-approval') {
    return (
      <div className="card-reply" onClick={stop}>
        <button className="card-reply-allow" onClick={() => onRespondPrompt(session.id, 'allow')}>
          Allow once
        </button>
        <button onClick={() => onRespondPrompt(session.id, 'trust')}
                title="Trust this tool for the rest of the session">
          Trust
        </button>
        <button className="card-reply-deny" onClick={() => onRespondPrompt(session.id, 'deny')}>
          Deny
        </button>
      </div>
    )
  }

  // Finished and waiting on you. Only managed sessions can be typed into; a
  // foreign one has to be taken over first, and its card says so.
  if (session.status === 'idle' && session.control === 'managed') {
    const submit = (e) => {
      if (e) { e.preventDefault(); e.stopPropagation() }
      const body = text.trim()
      const readyAtts = cardAttachments.filter(a => !a.uploading)
      if ((!body && readyAtts.length === 0) || busy) return
      setBusy(true)
      // Save to history before clearing — same dedup logic as the composer.
      if (body) {
        const next = (history[0] === body ? history : [body, ...history]).slice(0, HISTORY_LIMIT)
        setHistory(next)
        writeHistory(session.id, next)  // write immediately — don't rely on effect timing
      }
      setHistoryIndex(-1)
      setText('')
      clearCardAttachments()
      Promise.resolve(onSendText(session.id, body, readyAtts))
        .then(ok => { if (!ok) setText(body) })
        .finally(() => setBusy(false))
    }
    const recall = (e, direction) => {
      if (!history.length) return
      const next = historyIndex + direction
      if (next < -1 || next >= history.length) return
      e.preventDefault()
      setHistoryIndex(next)
      setText(next === -1 ? '' : history[next])
    }
    return (
      <form className="card-reply" onClick={stop} onSubmit={submit}>
        {cardAttachments.length > 0 && (
          <PasteAttachments attachments={cardAttachments} onRemove={removeCardAttachment} />
        )}
        <input className="card-reply-input" value={text} disabled={busy}
               placeholder="Reply…" onClick={stop}
               onChange={(e) => { setText(e.target.value); setHistoryIndex(-1) }}
               onPaste={onCardPaste}
               onKeyDown={(e) => {
                 if (e.key === 'Enter') { submit(e); return }
                 if (e.key === 'ArrowUp') recall(e, 1)
                 if (e.key === 'ArrowDown') recall(e, -1)
               }} />
        <button className="card-reply-send" type="submit" disabled={busy || !text.trim()}>
          {busy ? '⟳' : '↩'}
        </button>
      </form>
    )
  }
  return null
}

function SessionCard({ session, onClick, onOpenFull, isSelected, onKill, onRestart, onCancelPending, onTakeover, onAck, notified, acked, ending, attention, held, onRespondApproval, onRespondPrompt, onSendText, onCorrect }) {
  const cfg = STATUS_CONFIG[session.status] || STATUS_CONFIG.done
  const ctrl = CONTROL_LABEL[session.control]
  const openTimer = useRef(null)
  const notify = useToast()
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const renameInputRef = useRef(null)
  // Duration: task record (type_tag) and estimate
  const [durationRecord, setDurationRecord] = useState(null)
  const [durationEstimate, setDurationEstimate] = useState(null)
  const [tagDropdownOpen, setTagDropdownOpen] = useState(false)
  useEffect(() => () => clearTimeout(openTimer.current), [])

  // Fetch the duration record for this session on mount.
  // Also fetch the estimate for this session's project+type_tag.
  useEffect(() => {
    api.getSessionDuration(session.id)
      .then(d => {
        if (d?.record) {
          setDurationRecord(d.record)
          // Now fetch the estimate for this project+type_tag combination
          const project = d.record.features?.project || ''
          const typeTag = d.record.features?.type_tag || ''
          if (project && typeTag) {
            api.getDurationStats(project, typeTag)
              .then(est => { if (est && est.display !== undefined) setDurationEstimate(est) })
              .catch(() => {})
          }
        }
      })
      .catch(() => {})
  }, [session.id])

  const handleTagChange = (e, newTag) => {
    e.stopPropagation()
    setTagDropdownOpen(false)
    api.setSessionTypeTag(session.id, newTag)
      .then(d => {
        if (d?.ok) {
          setDurationRecord(prev => prev ? {
            ...prev,
            features: { ...prev.features, type_tag: newTag }
          } : prev)
          // Re-fetch estimate with new tag
          const project = durationRecord?.features?.project || ''
          if (project) {
            api.getDurationStats(project, newTag)
              .then(est => { if (est && est.display !== undefined) setDurationEstimate(est) })
              .catch(() => {})
          }
        }
      })
      .catch(() => {})
  }

  const startRename = (e) => {
    e.stopPropagation()
    setNameDraft(session.title || session.name || '')
    setRenaming(true)
    setTimeout(() => renameInputRef.current?.select(), 30)
  }

  const saveRename = (e) => {
    if (e) e.stopPropagation()
    const title = nameDraft.trim()
    setRenaming(false)
    if (!title || title === (session.title || session.name)) return
    api.renameSession(session.id, title)
      .then(d => { if (d.error) notify(`Rename failed: ${d.error}`, 'error') })
  }

  // Opening the panel reflows the grid, which used to move the card out from
  // under the pointer between the two halves of a double-click — so the second
  // click landed elsewhere and `dblclick` never reached the card. Holding the
  // single-click action for a beat keeps the layout still long enough for the
  // second click to arrive. `e.detail` is the browser's own click counter.
  const handleCardClick = (e) => {
    if (session.control === 'starting') return
    clearTimeout(openTimer.current)
    if (e.detail >= 2) { onOpenFull(session); return }
    openTimer.current = setTimeout(() => onClick(session), CARD_DOUBLE_CLICK_MS)
  }

  const classes = ['card']
  if (isSelected) classes.push('card-selected')
  if (session.status === 'awaiting-approval' && !acked) classes.push('card-awaiting')
  if (session.status === 'thinking' || session.status === 'running') classes.push('card-thinking')
  if (notified) classes.push('card-notified')

  return (
    <div className={classes.join(' ')} style={{ borderColor: cfg.color, backgroundColor: cfg.bg }}
         onClick={handleCardClick}>
      <div className="card-header">
        {/* The action, not the state — "Finished — your turn" answers the
            question the grid is being read to answer. The raw status stays as
            the tooltip, because it is still what you check when the action
            reads wrong. */}
        <span className="card-status" style={{ color: cfg.color }}
              title={cfg.label.replace(/^\W+\s*/, '')}>
          {attention ? attention.action : cfg.label}
        </span>
        <div className="card-header-right">
          <span className="card-time">{timeAgo(session.updated_at)}</span>
          {session.trust_until && session.trust_until * 1000 > Date.now() && (
            <span className="card-trust" title="Trust TTL active — tool calls auto-allowed">
              🔓 {Math.round((session.trust_until * 1000 - Date.now()) / 60000)}m
            </span>
          )}
          {session.status === 'awaiting-approval' && !acked && (
            <button className="card-ack" onClick={(e) => { e.stopPropagation(); onAck(session.id) }} title="Acknowledge">✓</button>
          )}
          {onCorrect && (
            <button className="card-correct" title="Log a correction — agent did something wrong"
                    onClick={(e) => { e.stopPropagation(); onCorrect(session.id) }}>⚑</button>
          )}
          {session.control === 'foreign' && (
            session.handoverable === false
              ? <span className="card-no-takeover" title={`Owned by '${session.owner || 'unknown'}' — set released=true to hand over`}>⊘</span>
              : <button className="card-focus" onClick={(e) => { e.stopPropagation(); onTakeover(session) }} title="Take over: kill the process and restart it under tmux">⇩</button>
          )}
          {/* A spawn that never correlated has no session id, so it needs its
              own dismissal path — without one the card was unremovable. */}
          {session.nonce ? (
            <button className="card-kill" onClick={(e) => { e.stopPropagation(); onCancelPending(session.nonce) }}
                    title="Give up on this spawn and kill its tmux session">×</button>
          ) : session.status !== 'done' && (
            <>
              {onRestart && session.control === 'managed' && (
                <button className="card-restart" onClick={(e) => { e.stopPropagation(); onRestart(session.id, e) }}
                        disabled={ending} title="Restart session (kill then resume)">↺</button>
              )}
              <button className="card-maximize" onClick={(e) => { e.stopPropagation(); onOpenFull(session) }}
                      title="Open full session">↗</button>
              <button className="card-kill" onClick={(e) => onKill(session.id, e)}
                      disabled={ending} title={session.control === 'crew' ? 'Archive (remove from active list)' : 'End session (asks it to quit cleanly)'}>
                {ending ? '⟳' : '×'}
              </button>
            </>
          )}
        </div>
      </div>
      {/* Headline is what the user actually asked for; the directory is context,
          so it sits underneath rather than standing in as the name. */}
      <div className="card-name-row">
        {renaming ? (
          <input
            ref={renameInputRef}
            className="card-rename-input"
            value={nameDraft}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={saveRename}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') saveRename(e)
              if (e.key === 'Escape') { e.stopPropagation(); setRenaming(false) }
            }}
            autoFocus
          />
        ) : (
          <span className="card-warp-name" title="Click to rename"
                onClick={e => { e.stopPropagation(); startRename(e) }}>{session.name}</span>
        )}
        {session.gated && (
          <span className="card-gated" title="Every tool call in this session is held for your approval">🔒</span>
        )}
        {ctrl && <span className="card-control" title={ctrl.title}>{ctrl.text}</span>}
        {session.kiro_profile && (
          <span
            className={`card-profile ${session.profile_verified === false ? 'card-profile-unverified' : ''}`}
            title={session.profile_verified === false
              ? `Profile recorded at launch: "${session.kiro_profile}". A switch happened since — the running agent may be on a different profile.`
              : `Dispatched under profile "${session.kiro_profile}"`}
          >
            {session.profile_verified === false ? '○' : '◉'} {session.kiro_profile}
          </span>
        )}
        {session.delivery_notes && session.delivery_notes.some(n => n.includes('zero') || n.includes('no steering')) && (
          <span className="card-no-steering" title={session.delivery_notes.join(' | ')}>⚠ rules</span>
        )}
      </div>
      <div className="card-title" title={session.cwd}>📁 {session.folder || showPath(session)}</div>
      {/* Duration: type tag chip (only when classified) + estimate (when n>=6) */}
      {durationRecord && durationRecord.features?.type_tag && durationRecord.features.type_tag !== 'unknown' && (
        <div className="card-duration-row">
          <span className="card-type-tag"
                title="Task type — click to correct"
                onClick={e => { e.stopPropagation(); setTagDropdownOpen(o => !o) }}>
            {durationRecord.features.type_tag}
          </span>
          {tagDropdownOpen && (
            <div className="card-tag-dropdown" onClick={e => e.stopPropagation()}>
              {['coding', 'research', 'writing', 'infra', 'review', 'unknown'].map(t => (
                <span key={t}
                      className={`card-tag-option${t === durationRecord.features?.type_tag ? ' card-tag-option-selected' : ''}`}
                      onClick={e => handleTagChange(e, t)}>
                  {t}
                </span>
              ))}
            </div>
          )}
          {durationEstimate?.display && (
            <span className="card-duration-est" title={`p50–p90 from ${durationEstimate.n} sessions`}>
              {durationEstimate.display}
            </span>
          )}
        </div>
      )}
      {/* Stall warning — session is thinking/running but JSONL hasn't grown */}
      {session.stalled && (
        <div className="card-stalled" title="No output for an extended period">⚠ stalled</div>
      )}
      {/* AI-generated one-line summary — shown for managed idle/done sessions
          (concierge ran after the last stop event) and for attention sessions. */}
      {session.summary && session.control === 'managed' &&
        (attention?.needs || session.status === 'idle' || session.status === 'done') && (
        <div className="card-summary" title="Generated by concierge">💬 {session.summary}</div>
      )}
      {/* Sub-agent count — shown when one or more sub-agents are active */}
      {session.subagent_count > 0 && (
        <div className="card-subagents" title={`${session.subagent_count} sub-agent${session.subagent_count !== 1 ? 's' : ''} running`}>
          ⬡ {session.subagent_count} sub-agent{session.subagent_count !== 1 ? 's' : ''} active
        </div>
      )}
      {/* Slash queue depth — how many commands are waiting to send after this turn */}
      {session.sq_depth > 0 && (
        <div className="card-slash-queue" title={`${session.sq_depth} command${session.sq_depth !== 1 ? 's' : ''} queued`}>
          ⏎ {session.sq_depth} queued
        </div>
      )}
      {/* What it last said. Skip when a concierge summary is already shown — 
          the summary is more useful and showing both is redundant. */}
      {session.last_message &&
        !/^\[pasted document:/i.test(session.last_message.trim()) &&
        !(session.summary && session.control === 'managed' &&
          (attention?.needs || session.status === 'idle' || session.status === 'done')) && (
        <div className={`card-last${session.control === 'managed' && (session.status === 'idle' || session.status === 'done') ? ' card-last-summary' : ''}`}
             title={session.last_message}>{session.last_message}</div>
      )}
      {/* One-line hint extracted from last_output for idle/waiting sessions
          that have no last_message but do have a recent output snippet. */}
      {!session.last_message && session.last_output &&
        (session.status === 'idle' || session.status === 'awaiting-approval') && (
        <div className="card-hint" title={session.last_output}>
          {session.last_output.replace(/\s+/g, ' ').trim().slice(0, 90)}
        </div>
      )}
      {/* Only where something is actually waiting. A working agent needs
          nothing, and a field under it would be an invitation to interrupt. */}
      {attention?.needs && onSendText && (
        <CardReply session={session} held={held}
                   onRespondApproval={onRespondApproval}
                   onRespondPrompt={onRespondPrompt}
                   onSendText={onSendText} />
      )}
    </div>
  )
}

// Minimal markdown renderer producing React elements. Deliberately not using
// dangerouslySetInnerHTML: agent output is arbitrary text, and letting React
// Quick-create recall. Unlike the composer's history this one is global rather
// than keyed by session: the box launches new sessions, so there is no session
// to key it by.
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
  const where = suggestion
    ? `${suggestion.path.split('/').pop() || '/'}${suggestion.source === 'finder' ? ' (Finder)' : ''}`
    : ''

  // Derive top 4 folders from recent sessions
  const popularFolders = useMemo(() => {
    if (!sessions?.length) return []
    const counts = {}
    for (const s of sessions) {
      if (!s.cwd || s.cwd === process.env.HOME) continue
      const folder = s.folder || s.cwd.split('/').pop()
      if (!folder) continue
      counts[s.cwd] = (counts[s.cwd] || { cwd: s.cwd, folder, count: 0 })
      counts[s.cwd].count++
    }
    return Object.values(counts).sort((a, b) => b.count - a.count).slice(0, 4)
  }, [sessions])

  const handleKeyDown = (e) => {
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

  return (
    <div className="quick-create-wrap">
      <form className="quick-create" onSubmit={(e) => {
        e.preventDefault()
        if (!task.trim()) return
        const updated = (task === qcHistory[0] ? qcHistory : [task, ...qcHistory]).slice(0, QC_HISTORY_LIMIT)
        setQcHistory(updated)
        localStorage.setItem(QC_HISTORY_KEY, JSON.stringify(updated))
        setQcHistIdx(-1)
        onDispatch({ task, cwd: selectedCwd || '', model: '', effort: '',
                     agent: localStorage.getItem('launch-agent') || '' })
        setTask('')
        setSelectedCwd('')
      }}>
        <input className="quick-create-input" value={task} onChange={(e) => { setTask(e.target.value); setQcHistIdx(-1) }}
               onKeyDown={handleKeyDown}
               placeholder={selectedCwd ? `In ${selectedCwd.split('/').pop()}…` : where ? `New session in ${where}…` : 'New session…'} />
        <button className="quick-create-btn" type="submit" disabled={!task.trim()}>▶</button>
      </form>
      {popularFolders.length > 0 && (
        <div className="quick-create-folders">
          {popularFolders.map(f => (
            <button
              key={f.cwd}
              className={`qc-folder-badge ${selectedCwd === f.cwd ? 'active' : ''}`}
              onClick={() => setSelectedCwd(selectedCwd === f.cwd ? '' : f.cwd)}
              title={f.cwd}
            >
              📁 {f.folder}
            </button>
          ))}
        </div>
      )}
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

function ListRow({ s, a, selected, onSelect, onOpenFull, onKill, onCancelPending, killing, held, onRespondApproval }) {
  const cfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.done
  const notify = useToast()
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')

  const startRename = (e) => {
    e.stopPropagation()
    setNameDraft(s.title || s.name || '')
    setRenaming(true)
  }

  const saveRename = (e) => {
    if (e) e.stopPropagation()
    const title = nameDraft.trim()
    setRenaming(false)
    if (!title || title === (s.title || s.name)) return
    api.renameSession(s.id, title)
      .then(d => { if (d.error) notify(`Rename failed: ${d.error}`, 'error') })
  }

  return (
    <li
      className={`list-row${selected?.id === s.id ? ' list-row-selected' : ''}${s.status === 'awaiting-approval' ? ' list-row-waiting' : ''}`}
      onClick={() => s.control !== 'starting' && onSelect(s)}
      onDoubleClick={() => s.control !== 'starting' && !renaming && onOpenFull(s)}>
      <span className="list-dot" style={{ background: cfg.color }} title={cfg.label} />
      {renaming ? (
        <input
          className="list-rename-input"
          value={nameDraft}
          autoFocus
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={saveRename}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') saveRename(e)
            if (e.key === 'Escape') setRenaming(false)
          }}
        />
      ) : (
        <span className="list-name" title="Click to rename"
              onClick={e => { e.stopPropagation(); startRename(e) }}>{s.name}</span>
      )}
      {s.stalled && <span className="list-stalled" title="No output for an extended period">⚠</span>}
      {s.gated && <span className="list-gated" title="Gated">🔒</span>}
      <span className="list-state">{a?.action || cfg.label.replace(/^\W+\s*/, '')}</span>
      {held && onRespondApproval && (
        <span className="list-approval" onClick={e => e.stopPropagation()}>
          <button className="list-approval-allow"
                  onClick={() => onRespondApproval(s.id, held.request_id, true)}>Allow</button>
          <button className="list-approval-deny"
                  onClick={() => onRespondApproval(s.id, held.request_id, false)}>Deny</button>
          <span className="list-approval-tool" title={JSON.stringify(held.tool_input)}>
            {held.tool_name || 'tool'}
          </span>
        </span>
      )}
      <span className="list-path" title={s.cwd}>{s.folder || showPath(s)}</span>
      <span className="list-time">{timeAgo(s.updated_at)}</span>
      {s.nonce ? (
        <button className="list-kill" title="Give up on this spawn"
                onClick={(e) => { e.stopPropagation(); onCancelPending(s.nonce) }}>×</button>
      ) : s.status !== 'done' && (
        <button className="list-kill" disabled={killing.has(s.id)}
                title="End session"
                onClick={(e) => { e.stopPropagation(); onKill(s.id, e) }}>
          {killing.has(s.id) ? '⟳' : '×'}
        </button>
      )}
    </li>
  )
}
const STATUS_ORDER = { 'awaiting-approval': 0, 'thinking': 1, 'running': 2, 'idle': 3, 'done': 4, 'error': 5 }

function ListView({ needsYou, working, selected, onSelect, onOpenFull, onKill, onCancelPending, onTakeover, killing, heldBySession, onRespondApproval }) {
  const all = [...needsYou.map(({ s, a }) => ({ s, a })), ...working.map(({ s, a }) => ({ s, a }))]
  const listRef = useRef(null)
  const [sort, setSort] = useState({ key: null, dir: 1 }) // null = default (attention-first)

  const sorted = useMemo(() => {
    if (!sort.key) return all
    return [...all].sort((a, b) => {
      let av, bv
      if (sort.key === 'status') {
        av = STATUS_ORDER[a.s.status] ?? 9
        bv = STATUS_ORDER[b.s.status] ?? 9
      } else if (sort.key === 'age') {
        av = new Date(a.s.updated_at || 0).getTime()
        bv = new Date(b.s.updated_at || 0).getTime()
      } else if (sort.key === 'cwd') {
        av = (a.s.folder || a.s.cwd || '').toLowerCase()
        bv = (b.s.folder || b.s.cwd || '').toLowerCase()
      }
      if (av < bv) return -sort.dir
      if (av > bv) return sort.dir
      return 0
    })
  }, [all, sort])

  const toggleSort = (key) => setSort(prev =>
    prev.key === key ? { key, dir: -prev.dir } : { key, dir: 1 }
  )

  const sortIcon = (key) => sort.key !== key ? '⇅' : sort.dir === 1 ? '↑' : '↓'

  // Keyboard navigation: j/k move selection, Enter opens full, Escape deselects.
  // Only active when no input/textarea has focus.
  useEffect(() => {
    if (!all.length) return
    const onKey = (e) => {
      const el = e.target
      if (el?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el?.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      const ids = all.map(({ s }) => s.id)
      const curIdx = selected ? ids.indexOf(selected.id) : -1

      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        const next = Math.min(curIdx + 1, ids.length - 1)
        const s = all[next]?.s
        if (s && s.control !== 'starting') onSelect(s)
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        if (curIdx <= 0) return
        const s = all[curIdx - 1]?.s
        if (s && s.control !== 'starting') onSelect(s)
      } else if (e.key === 'Enter' && selected) {
        e.preventDefault()
        onOpenFull(selected)
      } else if (e.key === 'Escape' && selected) {
        e.preventDefault()
        onSelect(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [all, selected, onSelect, onOpenFull])

  if (!all.length) return null
  return (
    <div className="list-view" ref={listRef}>
      <div className="list-sort-bar">
        <button className={`list-sort-btn${sort.key === 'status' ? ' active' : ''}`}
                onClick={() => toggleSort('status')}>Status {sortIcon('status')}</button>
        <button className={`list-sort-btn${sort.key === 'cwd' ? ' active' : ''}`}
                onClick={() => toggleSort('cwd')}>Folder {sortIcon('cwd')}</button>
        <button className={`list-sort-btn${sort.key === 'age' ? ' active' : ''}`}
                onClick={() => toggleSort('age')}>Age {sortIcon('age')}</button>
        {sort.key && <button className="list-sort-reset" onClick={() => setSort({ key: null, dir: 1 })}>✕</button>}
        <span className="list-sort-count">{sorted.length}</span>
      </div>
      <ul className="list-rows">
        {sorted.map(({ s, a }) => (
          <ListRow key={s.id} s={s} a={a} selected={selected}
                   onSelect={onSelect} onOpenFull={onOpenFull}
                   onKill={onKill} onCancelPending={onCancelPending}
                   killing={killing}
                   held={heldBySession?.get(s.id)}
                   onRespondApproval={onRespondApproval} />
        ))}
      </ul>
    </div>
  )
}

export { CardReply, SessionCard, AttentionBar, ListView }
