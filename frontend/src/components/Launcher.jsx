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

  // The folder shown in the prompt prefix
  const activeFolderName = selectedCwd
    ? selectedCwd.split('/').pop()
    : suggestion
      ? (suggestion.path.split('/').pop() || '/')
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
      cwd: cwd !== undefined ? cwd : (selectedCwd || (suggestion ? suggestion.path : '') || ''),
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
          onClick={() => { setFolderOpen(v => !v); setHistoryOpen(false) }}
          title={selectedCwd || (suggestion ? suggestion.path : 'Choose folder')}
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
            {/* "Inferred" option */}
            {suggestion && (
              <button
                type="button"
                className={`qc-folder-item${!selectedCwd ? ' active' : ''}`}
                onClick={() => { setSelectedCwd(''); setFolderOpen(false) }}
              >
                <span className="qc-folder-icon">⟳</span>
                <span className="qc-folder-name">{suggestion.path.split('/').pop() || '/'}</span>
                <span className="qc-folder-hint">auto</span>
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
  }

  const launchFromTemplate = () => {
    if (!selectedTemplate) return
    setIntakeRunning(true)
    api.intake({
      template: selectedTemplate.id,
      vars: templateVars,
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
              <button type="button" className="dispatch-btn" disabled={intakeRunning}
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


export { QuickCreate, CommandBar, NewSessionLauncher }
