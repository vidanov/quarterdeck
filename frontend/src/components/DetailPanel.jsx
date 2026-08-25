import React, { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { errorOf } from '../api/client'
import * as api from '../api/sessions'
import * as secretsApi from '../api/secrets'
import * as settingsApi from '../api/settings'
import * as cliApi from '../api/cli'
import { useCLI } from '../hooks/useCLI'
import * as shellsApi from '../api/shells'
import { usePasteAttachments } from '../hooks/usePasteAttachments'
import { PasteAttachments } from './PasteAttachments'
import SideChat from './SideChat'
import { DocCard } from './DocCard'
import XtermPane from './XtermPane'
import ShellInputBar from './ShellInputBar'

// ---------------------------------------------------------------------------
// Save-as-template modal
// ---------------------------------------------------------------------------
function SaveAsTemplateModal({ session, afterSeq, onClose, notify }) {
  const [name, setName] = useState(session?.title || '')
  const [task, setTask] = useState('')
  const [saving, setSaving] = useState(false)

  // Close on Escape
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  const handleSave = async () => {
    if (!name.trim()) { notify('Template name is required', 'error'); return }
    setSaving(true)
    try {
      const d = await api.saveAsTemplate(session.id, {
        after_seq: afterSeq,
        name: name.trim(),
        task: task.trim(),
        cwd: session.cwd || '',
      })
      if (d.ok) {
        notify(`Template "${name.trim()}" saved`, 'info')
        onClose()
      } else {
        notify(d.error || 'Save failed', 'error')
      }
    } catch (e) {
      notify(e.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  return createPortal(
    <div className="sat-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sat-modal" role="dialog" aria-modal="true" aria-label="Save as template">
        <div className="sat-header">
          <span className="sat-title">Save as template</span>
          <button className="sat-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="sat-hint">
          Snapshot of this conversation up to turn&nbsp;<strong>{afterSeq}</strong> will be
          saved. Every time you use this template a new session starts from this exact context.
        </p>
        <label className="sat-label">
          Template name
          <input
            className="sat-input"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. Refactor module"
            autoFocus
          />
        </label>
        <label className="sat-label">
          Task / prompt
          <span className="sat-hint-inline">Use {'{{var}}'} for variable slots</span>
          <textarea
            className="sat-textarea"
            value={task}
            onChange={e => setTask(e.target.value)}
            placeholder={'e.g. Refactor {{file}} using the patterns established in this session.'}
            rows={4}
          />
        </label>
        <div className="sat-footer">
          <button className="sat-btn-cancel" onClick={onClose}>Cancel</button>
          <button className="sat-btn-save" onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : '📋 Save template'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// Error boundary so a transcript rendering crash shows a recovery button
// instead of a blank white screen with no way out.
class TranscriptErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(e) { return { error: e } }
  componentDidCatch(e) { console.error('[Quarterdeck] transcript render error:', e) }
  render() {
    if (this.state.error) {
      return (
        <div className="transcript-error-boundary">
          <div className="transcript-error-msg">⚠ Transcript could not be rendered</div>
          <div className="transcript-error-detail">{String(this.state.error?.message || '')}</div>
          <button className="transcript-error-retry"
                  onClick={() => { this.setState({ error: null }); this.props.onRetry?.() }}>
            ↺ Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
import { useToast } from '../state/ToastContext'
import { useConfirm } from '../state/ConfirmContext'
import Markdown from './Markdown'
import {
  STATUS_CONFIG, showPath, PANEL_MIN, PANEL_DEFAULT, CELL_SAMPLE,
  PANE_MIN_COLS, PANE_MIN_ROWS, PANE_SCROLLBACK, PANE_FOLLOW_SLACK,
  clampPanelWidth, HISTORY_LIMIT, readHistory, writeHistory, loadHistoryFromPrefs,
  parseUserMessage,
} from '../utils'

function StackItem({ item, index, count, sessionId, setStack, onDelete, onMove }) {
  const notify = useToast()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(item.text)

  const saveEdit = () => {
    if (!draft.trim()) { setEditing(false); setDraft(item.text); return }
    api.editStackItem(sessionId, item.id, draft.trim())
      .then(d => {
        if (d.items) setStack(d.items)
        else notify(d.error || 'Could not save the edit', 'error')
      })
      .catch(() => notify('Could not save the edit', 'error'))
    setEditing(false)
  }

  return (
    <li
      className="stack-item"
      onDragOver={e => e.preventDefault()}
      onDrop={e => {
        e.preventDefault()
        const fromIdx = parseInt(e.dataTransfer.getData('text/plain'), 10)
        if (Number.isNaN(fromIdx) || fromIdx === index) return
        onMove(fromIdx, index)
      }}
    >
      {/* Only the handle is draggable. With the whole row draggable, WebKit —
          which is what the packaged app runs — starts a text-selection drag
          from the item's own text and the reorder never begins. The handle
          carries no selectable text, so a drag from it is unambiguous. */}
      <span className="stack-drag" title="Drag to reorder" draggable
            onDragStart={e => {
              e.dataTransfer.setData('text/plain', String(index))
              e.dataTransfer.effectAllowed = 'move'
            }}>⠿</span>
      <span className="stack-index">{index + 1}</span>
      {editing ? (
        <input
          className="stack-edit-input"
          value={draft}
          autoFocus
          onChange={e => setDraft(e.target.value)}
          onBlur={saveEdit}
          onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') { setEditing(false); setDraft(item.text) } }}
        />
      ) : (
        <span className="stack-text" onDoubleClick={() => setEditing(true)} title="Double-click to edit">{item.text}</span>
      )}
      {/* Buttons rather than drag alone: a drag is unavailable on the phone,
          which is where most of the queueing happens, and double-click on text
          competes with selecting a word. */}
      {!editing && (
        <>
          <button className="stack-move" disabled={index === 0} title="Move up"
                  onClick={() => onMove(index, index - 1)}>↑</button>
          <button className="stack-move" disabled={index === count - 1} title="Move down"
                  onClick={() => onMove(index, index + 1)}>↓</button>
          <button className="stack-edit" title="Edit" onClick={() => setEditing(true)}>✎</button>
        </>
      )}
      <button className="stack-delete" onClick={() => onDelete(item.id)} title="Remove">×</button>
    </li>
  )
}

// ── Composer chips — import shared defs from SettingsPanel ───────────────
import { DEFAULT_COMPOSER_CHIPS, CHIP_MODES, validChip } from './SettingsPanel.jsx'
import { getProjectSettings, saveProjectSettings } from '../api/projectSettings.js'

// Parse a pasted block into [{name, value}] pairs.
// Handles: KEY=VALUE, export KEY=VALUE, KEY="VALUE", AWS credentials file format,
// JSON {"key":"value"}, and dotenv style.
function parseSecretBlock(text) {
  const results = []
  const lines = text.split('\n')
  // Try JSON first
  try {
    const obj = JSON.parse(text.trim())
    if (typeof obj === 'object' && !Array.isArray(obj)) {
      for (const [k, v] of Object.entries(obj)) {
        if (typeof v === 'string' && v) results.push({ name: k.toUpperCase().replace(/[^A-Z0-9_]/g, '_'), value: v })
      }
      if (results.length) return results
    }
  } catch {}
  // AWS credentials file: aws_access_key_id = VALUE
  for (const line of lines) {
    const awsMatch = line.match(/^\s*(aws_[a-z_]+)\s*=\s*(.+)$/)
    if (awsMatch) {
      results.push({ name: awsMatch[1].toUpperCase(), value: awsMatch[2].trim() })
      continue
    }
    // export KEY=VALUE or KEY=VALUE (with optional quotes)
    const envMatch = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']?([^"'\n]+)["']?\s*$/)
    if (envMatch) {
      results.push({ name: envMatch[1].toUpperCase(), value: envMatch[2].trim() })
    }
  }
  return results
}

// ── ChipsPanel modal — per-project chips editor ───────────────────────────
function ChipsPanel({ cwd, onClose }) {
  const notify = useToast()
  const [projectChips, setProjectChips] = React.useState(null)
  const [includeGlobal, setIncludeGlobal] = React.useState(true)
  const [globalChips, setGlobalChips] = React.useState(DEFAULT_COMPOSER_CHIPS)

  React.useEffect(() => {
    getProjectSettings(cwd).then(s => {
      const v = s['composer-chips']
      setProjectChips(Array.isArray(v) ? v.filter(validChip) : [])
      setIncludeGlobal(s['chips-include-global'] !== false)
    }).catch(() => setProjectChips([]))
  }, [cwd])

  React.useEffect(() => {
    settingsApi.getSettings().then(s => {
      const v = s['composer-chips']
      if (Array.isArray(v) && v.length) setGlobalChips(v.filter(validChip))
    }).catch(() => {})
  }, [])

  React.useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const save = (chips, include) => {
    saveProjectSettings(cwd, { 'composer-chips': chips, 'chips-include-global': include })
      .catch(() => notify('Could not save', 'error'))
  }

  const updateChips = (next) => { setProjectChips(next); save(next, includeGlobal) }
  const updateInclude = (v) => { setIncludeGlobal(v); save(projectChips || [], v) }

  const setChip = (i, patch) => updateChips((projectChips || []).map((x, j) => j === i ? { ...x, ...patch } : x))
  const move = (i, dir) => {
    const next = [...(projectChips || [])]
    const j = i + dir
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    updateChips(next)
  }

  const chips = projectChips || []

  return (
    <div className="secrets-modal-overlay" onClick={onClose}>
      <div className="secrets-modal-box chips-panel-box" onClick={e => e.stopPropagation()}>
        <div className="secrets-modal-header">
          <span className="secrets-modal-title">🧩 Project Chips</span>
          <button className="secrets-modal-close" onClick={onClose}>✕</button>
        </div>
        <p className="secrets-hint">
          Starter chips shown above the composer for this project.
          When empty, global chips from <strong>Settings → Chips</strong> are used.
        </p>

        <div className="chips-panel-include-row">
          <label className="chips-panel-include-label">
            <input type="checkbox" checked={includeGlobal}
              onChange={e => updateInclude(e.target.checked)} />
            {' '}Include global chips after project chips
          </label>
        </div>

        {chips.length === 0 && (
          <p className="project-chips-hint" style={{ marginBottom: 12 }}>
            No project chips yet — global chips will be shown.{' '}
            Add one below to override.
          </p>
        )}

        <div className="chip-list">
          {chips.map((c, i) => (
            <div className="chip-row" key={i}>
              <div className="chip-row-top">
                <input className="chip-label" value={c.label} placeholder="label"
                  onChange={e => setChip(i, { label: e.target.value })} />
                <select className="chip-mode" value={c.mode}
                  onChange={e => setChip(i, { mode: e.target.value })}>
                  {CHIP_MODES.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
                <button className="chip-move" title="Move up" disabled={i === 0}
                  onClick={() => move(i, -1)}>↑</button>
                <button className="chip-move" title="Move down" disabled={i === chips.length - 1}
                  onClick={() => move(i, 1)}>↓</button>
                <button className="chip-del" title="Remove"
                  onClick={() => updateChips(chips.filter((_, j) => j !== i))}>×</button>
              </div>
              <textarea className="chip-prompt" rows={2} value={c.prompt}
                placeholder="Prompt text"
                onChange={e => setChip(i, { prompt: e.target.value })} />
            </div>
          ))}
        </div>

        <div className="settings-row" style={{ marginTop: 10, gap: 6 }}>
          <button className="launcher-btn"
            onClick={() => updateChips([...chips, { label: 'New chip', prompt: '', mode: 'send' }])}>
            + Add chip
          </button>
          {chips.length > 0 && (
            <button className="launcher-btn" onClick={() => updateChips([])}>
              Clear (use global only)
            </button>
          )}
        </div>

        {globalChips.length > 0 && (
          <div className="chips-panel-global-preview">
            <div className="secrets-section-label" style={{ marginBottom: 6 }}>
              Global chips {includeGlobal ? '(will appear after project chips)' : '(not included)'}
            </div>
            <div className="secrets-chips-row">
              {globalChips.map((c, i) => (
                <span key={i} className={`secrets-chip-btn ${includeGlobal ? '' : 'chip-excluded'}`}
                  title={c.prompt}>{c.label}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Per-project composer chips editor ────────────────────────────────────
function ProjectChipsSettings({ cwd, chips, onChange }) {
  const setChip = (i, patch) => onChange(chips.map((x, j) => j === i ? { ...x, ...patch } : x))

  const move = (i, dir) => {
    const next = [...chips]
    const j = i + dir
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }

  return (
    <div className="project-chips-editor">
      <p className="project-chips-hint">
        Project chips appear instead of global chips when this project is open.
        Leave empty to use global chips.
      </p>
      <div className="chip-list chip-list-compact">
        {chips.map((c, i) => (
          <div className="chip-row" key={i}>
            <div className="chip-row-top">
              <input className="chip-label" value={c.label} placeholder="label"
                onChange={e => setChip(i, { label: e.target.value })} />
              <select className="chip-mode" value={c.mode}
                onChange={e => setChip(i, { mode: e.target.value })}>
                {CHIP_MODES.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <button className="chip-move" title="Move up" disabled={i === 0}
                onClick={() => move(i, -1)}>↑</button>
              <button className="chip-move" title="Move down" disabled={i === chips.length - 1}
                onClick={() => move(i, 1)}>↓</button>
              <button className="chip-del" title="Remove"
                onClick={() => onChange(chips.filter((_, j) => j !== i))}>×</button>
            </div>
            <textarea className="chip-prompt" rows={2} value={c.prompt}
              placeholder="Prompt text"
              onChange={e => setChip(i, { prompt: e.target.value })} />
          </div>
        ))}
      </div>
      <div className="settings-row" style={{ marginTop: 6, gap: 6 }}>
        <button className="launcher-btn" onClick={() => onChange([...chips, { label: 'New chip', prompt: '', mode: 'send' }])}>
          + Add chip
        </button>
        {chips.length > 0 && (
          <button className="launcher-btn" onClick={() => onChange([])} title="Clear project chips, use global">
            Clear (use global)
          </button>
        )}
      </div>
    </div>
  )
}

function SecretsPanel({ cwd, onClose, modal, children }) {
  const notify = useToast()
  const [secrets, setSecrets] = React.useState(null)
  const [name, setName] = React.useState('')
  const [value, setValue] = React.useState('')
  const [showValue, setShowValue] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [pasteText, setPasteText] = React.useState('')
  const [parsed, setParsed] = React.useState([])   // [{name, value}] from paste
  const [savingBatch, setSavingBatch] = React.useState(false)

  React.useEffect(() => {
    if (!cwd) return
    secretsApi.listSecrets(cwd).then(d => setSecrets(d.secrets || [])).catch(() => setSecrets([]))
  }, [cwd])

  React.useEffect(() => {
    if (!modal || !onClose) return
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [modal, onClose])

  const reload = () => secretsApi.listSecrets(cwd).then(d => setSecrets(d.secrets || [])).catch(() => {})

  const handleAdd = () => {
    if (!name.trim() || !value.trim()) return
    setSaving(true)
    secretsApi.setSecret(cwd, name.trim(), value.trim())
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setName(''); setValue(''); setShowValue(false)
        reload()
      })
      .catch(() => notify('Could not save', 'error'))
      .finally(() => setSaving(false))
  }

  const handlePasteChange = (text) => {
    setPasteText(text)
    setParsed(text.trim() ? parseSecretBlock(text) : [])
  }

  const handleSaveBatch = () => {
    if (!parsed.length) return
    setSavingBatch(true)
    Promise.all(parsed.map(p => secretsApi.setSecret(cwd, p.name, p.value)))
      .then(() => { setPasteText(''); setParsed([]); reload(); notify(`${parsed.length} secrets saved`, 'info') })
      .catch(() => notify('Some secrets could not be saved', 'error'))
      .finally(() => setSavingBatch(false))
  }

  const handleDelete = (secretName) => {
    secretsApi.deleteSecret(cwd, secretName)
      .then(d => {
        if (d.ok) setSecrets(prev => prev.filter(s => s.name !== secretName))
        else notify('Could not remove', 'error')
      })
      .catch(() => notify('Could not remove', 'error'))
  }

  const existingNames = new Set((secrets || []).map(s => s.name))

  const content = (
    <div className="secrets-modal-box" onClick={e => e.stopPropagation()}>
      <div className="secrets-modal-header">
        <span className="secrets-modal-title">🔑 Project Secrets</span>
        <button className="secrets-modal-close" onClick={onClose}>✕</button>
      </div>
      <p className="secrets-hint">Injected as env vars at session start. Values in macOS keychain — agent cannot read them.</p>

      {/* Existing secrets */}
      {secrets === null ? (
        <div className="secrets-loading">Loading…</div>
      ) : secrets.length > 0 && (
        <ul className="secrets-list">
          {secrets.map(s => (
            <li key={s.name} className="secrets-row">
              <code className="secrets-name">{s.name}</code>
              <span className="secrets-value">••••••</span>
              <button className="secrets-delete" title="Remove" onClick={() => handleDelete(s.name)}>×</button>
            </li>
          ))}
        </ul>
      )}

      <div className="secrets-add-section">
        <div className="secrets-section-label">Add secret</div>

        {/* Smart paste */}
        <textarea
          className="secrets-paste-area"
          placeholder={'Paste credentials block, .env lines, or JSON:\nexport AWS_ACCESS_KEY_ID=AKIA...\nAWS_SECRET_ACCESS_KEY=abc...\n{"OPENAI_API_KEY":"sk-..."}'}
          value={pasteText}
          onChange={e => handlePasteChange(e.target.value)}
          rows={4}
        />
        {parsed.length > 0 && (
          <div className="secrets-parsed-preview">
            <span className="secrets-parsed-label">Detected {parsed.length} secret{parsed.length > 1 ? 's' : ''}:</span>
            {parsed.map(p => (
              <span key={p.name} className={`secrets-parsed-item ${existingNames.has(p.name) ? 'will-overwrite' : ''}`}
                    title={existingNames.has(p.name) ? 'Will overwrite existing' : ''}>
                {p.name}{existingNames.has(p.name) ? ' ↺' : ''}
              </span>
            ))}
            <button className="secrets-save-btn" disabled={savingBatch} onClick={handleSaveBatch}>
              {savingBatch ? '…' : `Save ${parsed.length}`}
            </button>
            <button className="secrets-cancel-btn" onClick={() => { setPasteText(''); setParsed([]) }}>Clear</button>
          </div>
        )}

        {/* Manual single entry */}
        <div className="secrets-manual-row">
          <input className="secrets-input" placeholder="NAME" value={name} autoFocus={!pasteText}
                 onChange={e => setName(e.target.value.toUpperCase().replace(/\s/g, '_'))}
                 onKeyDown={e => e.key === 'Enter' && document.querySelector('.secrets-input-value')?.focus()} />
          <div className="secrets-value-wrap">
            <input className="secrets-input secrets-input-value" placeholder="value"
                   type={showValue ? 'text' : 'password'}
                   value={value} onChange={e => setValue(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter') handleAdd() }} />
            <button type="button" className="secrets-eye-btn" title={showValue ? 'Hide' : 'Show'}
                    onClick={() => setShowValue(v => !v)}>
              {showValue ? '🙈' : '👁'}
            </button>
          </div>
          <button className="secrets-save-btn" disabled={saving || !name.trim() || !value.trim()} onClick={handleAdd}>
            {saving ? '…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )

  if (modal) {
    return (
      <div className="secrets-modal-overlay" onClick={onClose}>
        {content}
      </div>
    )
  }

  if (children) return children({ toggleBtn: null, body: content })
  return content
}

function TaskStack({ sessionId, stack, setStack, canSend }) {
  const notify = useToast()
  const [autoAdvance, setAutoAdvanceState] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!sessionId) return
    api.getAutoAdvance(sessionId)
      .then(d => setAutoAdvanceState(!!d.enabled)).catch(() => {})
  }, [sessionId])

  const toggleAutoAdvance = () => {
    const next = !autoAdvance
    setAutoAdvanceState(next)
    api.setAutoAdvance(sessionId, next).catch(() => {})
  }

  const deleteItem = (itemId) => {
    api.deleteStackItem(sessionId, itemId)
      .then(d => { if (d.items) setStack(d.items) }).catch(() => {})
  }

  const moveItem = (fromIdx, toIdx) => {
    if (fromIdx === toIdx || toIdx < 0 || toIdx >= stack.length) return
    const next = [...stack]
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    setStack(next)
    api.reorderStack(sessionId, next.map(i => i.id))
      .then(d => { if (d.items) setStack(d.items) })
      .catch(() => notify('Could not reorder the queue', 'error'))
  }

  const sendNext = () => {
    api.sendNextStackItem(sessionId)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setStack(d.remaining || [])
      })
      .catch(() => notify('Could not send', 'error'))
  }

  if (!stack.length) return null

  return (
    <div className="task-stack">
      {/* Compact toggle bar — no full-width header */}
      <div className="stack-toggle-row">
        <button className="stack-toggle-btn" onClick={() => setExpanded(v => !v)}
                title={expanded ? 'Collapse queue' : 'Expand queue'}>
          {expanded ? '▾' : '▸'} Queue ({stack.length})
        </button>
        <label className="stack-auto" title="Automatically send next item when session goes idle">
          <input type="checkbox" checked={autoAdvance} onChange={toggleAutoAdvance} />
          auto
        </label>
        {canSend && (
          <button className="stack-send-next" onClick={sendNext} title="Send next item now">
            ↗ Send next
          </button>
        )}
      </div>
      {expanded && (
        <ul className="stack-list">
          {stack.map((item, i) => (
            <StackItem
              key={item.id}
              item={item}
              index={i}
              count={stack.length}
              sessionId={sessionId}
              setStack={setStack}
              onDelete={deleteItem}
              onMove={moveItem}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  if (!text) return null
  const copy = () => {
    const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1500) }
    // navigator.clipboard requires a secure context (HTTPS).
    // Over plain HTTP (Tailscale without TLS) fall back to execCommand.
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback())
    } else {
      fallback()
    }
    function fallback() {
      const el = document.createElement('textarea')
      el.value = text
      el.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0'
      document.body.appendChild(el)
      el.focus()
      el.select()
      try { document.execCommand('copy'); done() } catch (_) {}
      document.body.removeChild(el)
    }
  }
  return (
    <button className={`chat-copy-btn${copied ? ' chat-copy-btn--copied' : ''}`} onClick={copy} title="Copy to clipboard">
      {copied ? '✓' : '⎘'}
    </button>
  )
}

function ContextPct({ pct, onCompact }) {
  if (!pct) return null
  const n = parseFloat(pct)
  let cls = 'detail-context-pct'
  if (n >= 50) cls += ' ctx-warn'
  else if (n >= 25) cls += ' ctx-medium'
  return (
    <span className={cls}
          data-tooltip={onCompact ? 'Compact?' : `Context: ${pct}`}
          onClick={onCompact || undefined}
          style={onCompact ? { cursor: 'pointer' } : {}}>
      ◔ {pct}{n >= 50 ? ' · compact?' : ''}
    </span>
  )
}

function DetailPanel({ session, onClose, onTakeover, onResume, onRefresh, onSelect, options, expanded, onToggleExpand, focusMode, onToggleFocus, paneTheme, sessions, onNewSession, onRestartHere, fromWall, favourites, onToggleFavourite }) {
  const notify = useToast()
  const askConfirm = useConfirm()
  // xterm.js requires canvas — doesn't work in mobile browsers
  const isMobileBrowser = window.innerWidth <= 768 || navigator.maxTouchPoints > 1
  const [detail, setDetail] = useState(null)
  // Mirrors the derived view below. Kept because the pane-poll and auto-scroll
  // Which terminal `Hand off` uses. Configurable in Settings → General; the
  // button applies it directly rather than making you pick every time.
  const [handoffTerminal, setHandoffTerminal] = useState(
    () => localStorage.getItem('handoff-terminal') || 'terminal')
  useEffect(() => {
    settingsApi.getSettings()
      .then(s => { if (s['handoff-terminal']) setHandoffTerminal(s['handoff-terminal']) })
      .catch(() => {})
  }, [])
  // The unified view picks itself from session state: streaming while the agent
  // works, transcript once it stops. `viewOverride` is the manual pin, for
  // reading back through turns (and forking) while a session is still running.
  const [viewOverride, setViewOverride] = useState(null)  // null | 'live' | 'transcript'
  const [pane, setPane] = useState('')
  const [width, setWidth] = useState(() =>
    clampPanelWidth(Number(localStorage.getItem('detail-width')) || PANEL_DEFAULT))
  const widthRef = useRef(width)
  const [draft, setDraft] = useState(() => localStorage.getItem(`draft:${session?.id}`) || '')
  // Paste attachment tiles — large pastes are stored as files, tiles replace the text wall
  const { attachments, onPaste: onPasteAttachment, removeAttachment, clearAttachments, setAttachments } =
    usePasteAttachments({ sessionId: session?.id })
  // Per-session composer history, survives closing the panel and a reload.
  const [history, setHistory] = useState(
    () => readHistory(session.id))
  const [historyIndex, setHistoryIndex] = useState(-1)
  const preDraftRef = useRef('')  // draft saved before first ↑ recall, restored on ↓ to -1
  const draftInitializedRef = useRef(false)  // true once loadHistoryFromPrefs has run
  const [echo, setEcho] = useState('')
  const [prompting, setPrompting] = useState(false)
  // When a prompt was last answered, and the pane loader, so answering can hide
  // the menu at once and re-capture rather than waiting out the poll. See
  // `respond` below for why both are refs.
  const answeredPromptRef = useRef(0)
  const loadPaneRef = useRef(null)
  const autoApproveKey = `auto-approve:${session?.id}`
  const [autoApprove, setAutoApprove] = useState(() => !!localStorage.getItem(`auto-approve:${session?.id}`))
  const toggleAutoApprove = () => {
    const next = !autoApprove
    setAutoApprove(next)
    if (next) localStorage.setItem(autoApproveKey, '1')
    else localStorage.removeItem(autoApproveKey)
  }

  // Corrections — one-press logging, confirm/withdraw after the fact
  const [corrections, setCorrections] = useState([])
  const [showCorrections, setShowCorrections] = useState(false)
  // Save-as-template modal state
  const [satModal, setSatModal] = useState(null) // { afterSeq: number } | null
  useEffect(() => {
    if (!session?.id || !showCorrections) return
    api.getCorrections(session.id)
      .then(d => { if (d.corrections) setCorrections(d.corrections) })
      .catch(() => {})
  }, [session?.id, showCorrections])
  const logCorrection = () => {
    api.addCorrection(session.id)
      .then(rec => {
        if (rec.ok) {
          notify('Correction logged — confirm and add a note below', 'info')
          setCorrections(c => [rec, ...c])
          setShowCorrections(true)
        } else {
          notify(rec.error || 'Could not log correction', 'error')
        }
      })
      .catch(() => notify('Backend unreachable', 'error'))
  }
  const setStatus = (id, status, note = '') => {
    api.updateCorrection(id, status, note)
      .then(rec => {
        if (rec.ok) setCorrections(c => c.map(x => x.id === id ? { ...x, status, note } : x))
        else notify(rec.error || 'Update failed', 'error')
      })
      .catch(() => notify('Backend unreachable', 'error'))
  }

  // Applies to Live, Activity and Last Output alike, and persists.
  // paneTheme is now lifted to App and passed as a prop.
  const liveRef = useRef(null)
  const metricRef = useRef(null)
  const paneBoxRef = useRef(null)
  // Whether the Live view is following the newest output. Set false when the
  // user scrolls up, so polling does not fight them for the scrollbar.
  const followPaneRef = useRef(true)
  const followTranscriptRef = useRef(true)
  const prevMsgCountRef = useRef(0)
  const [atBottom, setAtBottom] = useState(true)
  const [atTranscriptBottom, setAtTranscriptBottom] = useState(true)
  // Last geometry we asked tmux for, so an unchanged measurement costs nothing.
  const sentSizeRef = useRef({ cols: 0, rows: 0 })
  const [paneRows, setPaneRows] = useState(0)
  const [renaming, setRenaming] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [stack, setStack] = useState([])
  const [slashQueue, setSlashQueue] = useState([])
  const [slashDraft, setSlashDraft] = useState('')
  const [delivery, setDelivery] = useState(null)  // steering delivery record
  const [durationRecord, setDurationRecord] = useState(null)  // task 7: duration data
  const [pendingScreenshots, setPendingScreenshots] = useState([])
  const [chipPreview, setChipPreview] = useState(null) // {url, x, y}
  const [chipPreviewBlob, setChipPreviewBlob] = useState(null) // blob URL for the preview img
  const [chipPreviewError, setChipPreviewError] = useState(null)
  // Fetch screenshot via JS (carries X-Local-Token injected by app.py) and
  // create a blob URL — <img src> bypasses fetch/XHR and cannot carry auth headers.
  useEffect(() => {
    if (!chipPreview?.url) { setChipPreviewBlob(null); setChipPreviewError(null); return }
    let revoked = false
    setChipPreviewError(null)
    fetch(chipPreview.url)
      .then(r => {
        if (!r.ok) { setChipPreviewError(`HTTP ${r.status}`); return null }
        return r.blob()
      })
      .then(blob => {
        if (!blob || revoked) return
        setChipPreviewBlob(URL.createObjectURL(blob))
      })
      .catch(e => { if (!revoked) setChipPreviewError(e.message) })
    return () => {
      revoked = true
      setChipPreviewBlob(prev => { if (prev) URL.revokeObjectURL(prev); return null })
    }
  }, [chipPreview?.url])
  const dismissedScreenshots = useRef(new Set()) // names dismissed this session
  const [autoAdvance, setAutoAdvance] = useState(false)

  // --- CLI binding ---
  const {
    cliStatus, cliSendMode, setCLISendMode,
    openCliBinder, cliBindOpen, setCliBindOpen,
    cliInstances, bindCli, unbindCli,
  } = useCLI(session?.id, notify)
  const sendToCli = (text) => {
    cliApi.sendToCLI(session.id, text)
      .then(d => {
        if (d.ok) {
          notify('Sent to CLI', 'info')
          setDraft(''); if (draftRef.current) draftRef.current.value = ''
          setHistory(prev => { const h = [text, ...prev.filter(x => x !== text)].slice(0, HISTORY_LIMIT); writeHistory(session.id, h); return h })
          setHistoryIndex(-1)
        } else if (d.busy) {
          notify('CLI is busy — use "New session here" to start a parallel session', 'warn')
        } else {
          notify(d.error || 'Send failed', 'error')
        }
      })
  }
  // Chips bar: auto-open when session is active, collapsible when idle
  const [chipsOpen, setChipsOpen] = useState(() => localStorage.getItem('detail-chips-open') === '1')
  // Restore from backend on mount — localStorage is wiped when WKWebView restarts
  useEffect(() => {
    settingsApi.getSettings().then(d => {
      if (d['detail-chips-open'] !== undefined) {
        const v = d['detail-chips-open'] === true || d['detail-chips-open'] === '1'
        setChipsOpen(v)
        localStorage.setItem('detail-chips-open', v ? '1' : '0')
      }
    }).catch(() => {})
  }, [])
  const termRef = useRef(null)
  const draftRef = useRef(null)
  // Transcript with seq numbers for branch-at-turn
  const [messages, setMessages] = useState(null)
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [messagesError, setMessagesError] = useState(null)
  const transcriptMaxSeq = useRef(-1)
  // In-memory transcript cache — keyed by session id, capped at 20 sessions.
  // Allows instant display when switching back to a previously-viewed session.
  const transcriptCache = useRef({})
  // Guard ref: tracks which session id we've already triggered a load for,
  // so the effect fires exactly once per session rather than on every render
  // that finds messages===null (which caused an infinite retry storm on error).
  const transcriptLoadedFor = useRef(null)

  const sideChatRef = useRef(null)
  const [overflowOpen, setOverflowOpen] = useState(false)
  const [secretsModalOpen, setSecretsModalOpen] = useState(false)
  const [chipsModalOpen, setChipsModalOpen] = useState(false)
  // Merged starter chips shown above composer: project chips (+ global if includeGlobal), or global only
  const [starterChips, setStarterChips] = useState([])
  // Close overflow menu when clicking outside it
  useEffect(() => {
    if (!overflowOpen) return
    const handler = (e) => {
      if (!e.target.closest('.detail-overflow-wrap')) setOverflowOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [overflowOpen])

  // Derived from detail (once loaded) or the session prop while loading.
  // Declared here — before any effect — so every effect can reference them.
  const control = detail?.control || session.control
  const status = detail?.status || session.status
  const canSend = control === 'managed' || control === 'acp'

  // One view, two renderings. A working agent gets the tmux pane; a stopped one
  // gets the transcript (where turns can be forked). The pin overrides both.
  const isWorking = status === 'thinking' || status === 'running' || status === 'awaiting-approval'
  const canLive = canSend
  const effectiveView = viewOverride
    ? (viewOverride === 'live' && !canLive ? 'transcript' : viewOverride)
    : (canLive && isWorking ? 'live' : 'transcript')

  // --- Multi-shell state (per-folder named sessions) ---
  const [activeShellId, setActiveShellId] = useState(null) // which shell tab is open
  const [shells, setShells] = useState([])          // list of known shells for this cwd
  const [shellPane, setShellPane] = useState('')
  const [shellSt, setShellSt] = useState(null)
  const [shellCmd, setShellCmd] = useState('')
  const [shellBusy, setShellBusy] = useState(false)
  const [shellRawMode, setShellRawMode] = useState(() => localStorage.getItem('shell-raw-mode') === '1')
  const shellRawRef = useRef(null)
  const shellLastEscRef = useRef(0)  // timestamp of last Esc press for double-Esc exit

  const setShellRaw = (val) => {
    setShellRawMode(val)
    localStorage.setItem('shell-raw-mode', val ? '1' : '0')
  }
  const shellPaneRef = useRef(null)
  const shellMetricRef = useRef(null)
  const shellSentSizeRef = useRef({ cols: 0, rows: 0 })

  // Refresh shell list when session changes — useLayoutEffect for instant clear before paint
  useLayoutEffect(() => {
    setActiveShellId(null)
    setShellPane('')
    setShellSt(null)
    shellsApi.listShells().then(d => {
      const all = d.shells || []
      setShells(all)
      const cwd = session?.cwd || ''
      const match = all.find(sh => sh.cwd === cwd || sh.cwd.startsWith(cwd + '/') || cwd.startsWith(sh.cwd + '/'))
      if (match) setActiveShellId(match.shell_id)
    }).catch(() => {})
  }, [session?.id])

  // Poll active shell pane — only when shell view is visible.
  // In raw mode poll faster for responsive feel, but NEVER resurrect 150ms
  // on load — require the user to have the shell tab open.
  useEffect(() => {
    if (!activeShellId || effectiveView !== 'shell') return
    let active = true
    const poll = () => shellsApi.getShellPane(activeShellId)
      .then(d => { if (!active) return; setShellSt(d); setShellPane(d.pane || '') })
      .catch(() => {})
    poll()
    const iv = setInterval(poll, shellRawMode ? 150 : 1200)
    return () => { active = false; clearInterval(iv) }
  }, [activeShellId, shellRawMode, effectiveView])

  // Auto-scroll shell pane
  useEffect(() => {
    const box = shellPaneRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [shellPane])

  // Resize shell tmux window to match rendered box
  useEffect(() => {
    if (!activeShellId || !shellSt?.alive) return
    const box = shellPaneRef.current, probe = shellMetricRef.current
    if (!box || !probe) return
    let timer
    const measure = () => {
      const cell = probe.getBoundingClientRect()
      const cw = cell.width / CELL_SAMPLE
      if (!(cw > 0) || !(cell.height > 0)) return
      const style = getComputedStyle(box)
      const usable = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      const cols = Math.max(PANE_MIN_COLS, Math.floor(usable / cw))
      const rows = Math.max(PANE_MIN_ROWS, Math.floor(box.clientHeight / cell.height))
      const sent = shellSentSizeRef.current
      if (sent.cols === cols && sent.rows === rows) return
      shellSentSizeRef.current = { cols, rows }
      shellsApi.shellResize(activeShellId, cols, rows)
        .catch(() => { shellSentSizeRef.current = { cols: 0, rows: 0 } })
    }
    const observer = new ResizeObserver(() => { clearTimeout(timer); timer = setTimeout(measure, 120) })
    observer.observe(box)
    measure()
    return () => { clearTimeout(timer); observer.disconnect() }
  }, [activeShellId, shellSt?.alive])

  const openShellForCwd = (cwd) => {
    setShellBusy(true)
    shellsApi.openShell(cwd || session?.cwd || '~')
      .then(d => {
        if (d.ok) {
          setActiveShellId(d.shell_id)
          setShellSt(d)
          setShellPane('')
          shellsApi.listShells().then(r => setShells(r.shells || [])).catch(() => {})
        } else {
          notify(d.error || 'Failed to open shell', 'error')
        }
      })
      .catch(() => notify('Shell open failed', 'error'))
      .finally(() => setShellBusy(false))
  }

  const closeShellTab = (shellId) => {
    shellsApi.closeShell(shellId).then(() => {
      shellsApi.listShells().then(d => {
        const sessionCwd = session?.cwd || ''
        const remaining = (d.shells || []).filter(sh =>
          !sessionCwd || sh.cwd === sessionCwd ||
          sh.cwd.startsWith(sessionCwd + '/') ||
          sessionCwd.startsWith(sh.cwd + '/')
        )
        setShells(d.shells || [])
        if (activeShellId === shellId) {
          if (remaining.length > 0) {
            setActiveShellId(remaining[remaining.length - 1].shell_id)
          } else {
            setActiveShellId(null)
            setViewOverride(null) // back to chat
          }
        }
      }).catch(() => {})
    }).catch(() => {})
  }

  const shellAct = (path, body) => {
    if (!activeShellId) return
    setShellBusy(true)
    const fn = path === 'input'
      ? shellsApi.shellInput(activeShellId, body.text)
      : path === 'key'
        ? shellsApi.shellKey(activeShellId, body.key)
        : path === 'close'
          ? shellsApi.closeShell(activeShellId)
          : Promise.resolve({})
    fn.then(() => shellsApi.getShellPane(activeShellId)
        .then(d => { setShellSt(d); setShellPane(d.pane || '') }).catch(() => {}))
      .catch(() => {})
      .finally(() => setShellBusy(false))
  }

  const shellSend = (text) => {
    if (!text.trim() || !activeShellId) return
    setShellCmd('')
    setShellBusy(true)
    shellsApi.shellInput(activeShellId, text)
      .finally(() => setShellBusy(false))
  }

  // Translate a browser KeyboardEvent to a tmux send-keys string.
  // Returns null for keys we want to let the browser handle (F5, browser shortcuts).
  const keyEventToTmux = (e) => {
    const c = e.ctrlKey, a = e.altKey, s = e.shiftKey, k = e.key
    // Ctrl combos
    if (c && !a) {
      const map = {
        'a':'C-a','b':'C-b','c':'C-c','d':'C-d','e':'C-e','f':'C-f',
        'g':'C-g','h':'C-h','i':'C-i','j':'C-j','k':'C-k','l':'C-l',
        'm':'C-m','n':'C-n','o':'C-o','p':'C-p','q':'C-q','r':'C-r',
        's':'C-s','t':'C-t','u':'C-u','v':'C-v','w':'C-w','x':'C-x',
        'y':'C-y','z':'C-z',
        '[':'Escape','\\':'C-\\',']':'C-]',
      }
      if (map[k.toLowerCase()]) return map[k.toLowerCase()]
    }
    // Alt/Meta combos — tmux uses M- prefix
    if (a && !c) {
      if (k.length === 1) return `M-${k}`
      const amap = { 'ArrowLeft':'M-Left','ArrowRight':'M-Right','ArrowUp':'M-Up','ArrowDown':'M-Down','Backspace':'M-BSpace' }
      if (amap[k]) return amap[k]
    }
    // Special keys
    const specials = {
      'Enter':'Enter','Escape':'Escape','Tab': s ? 'BTab' : 'Tab',
      'Backspace':'BSpace','Delete':'DC','Insert':'IC',
      'ArrowUp':'Up','ArrowDown':'Down','ArrowLeft':'Left','ArrowRight':'Right',
      'Home':'Home','End':'End','PageUp':'PageUp','PageDown':'PageDown',
      'F1':'F1','F2':'F2','F3':'F3','F4':'F4','F5':'F5',
      'F6':'F6','F7':'F7','F8':'F8','F9':'F9','F10':'F10',
      'F11':'F11','F12':'F12',
    }
    if (specials[k]) return specials[k]
    // Printable single chars
    if (k.length === 1 && !c) return k
    return null
  }

  // When raw mode is active, focus the capture div and relay all keys to tmux
  useEffect(() => {
    if (shellRawMode && shellRawRef.current) shellRawRef.current.focus()
  }, [shellRawMode])

  // Reset transcript when the session changes so stale messages never bleed across.
  // useLayoutEffect runs before paint — prevents one render frame of old content showing.
  useLayoutEffect(() => {
    // Seed from cache for instant display; fall back to null (loading state)
    const cached = session?.id ? (transcriptCache.current[session.id] || null) : null
    setMessages(cached)
    setLoadingMessages(!cached)
    setMessagesError(null)
    transcriptMaxSeq.current = -1
    transcriptLoadedFor.current = null
    setViewOverride(null)  // clear any manual pin when switching sessions
    setCorrections([])     // clear stale corrections before the fetch for the new session
    // Close side chat when switching sessions
    sideChatRef.current?.close()
  }, [session.id])

  // Auto-open chips only when awaiting approval — user needs keys then.
  // Don't auto-open during thinking/running: it eats vertical space and the
  // user can open manually with the ⌃ toggle when needed.
  useEffect(() => {
    if (status === 'awaiting-approval') setChipsOpen(true)
  }, [status])

  // Keep a ref to messages so the polling interval can read the latest seq
  // without restarting on every append.
  const messagesRef = useRef(null)
  useEffect(() => { messagesRef.current = messages }, [messages])

  // Incremental transcript polling: while the session is active and the
  // transcript view is open, fetch only new lines and append.
  const messagesLoaded = messages !== null
  useEffect(() => {
    if (effectiveView !== 'transcript') return
    if (!messagesLoaded) return  // wait for initial load
    const activeStatus = detail?.status || session.status
    const isActive = activeStatus === 'thinking' || activeStatus === 'running' || activeStatus === 'awaiting-approval'
    if (!isActive) return

    const interval = setInterval(() => {
      const after = transcriptMaxSeq.current
      api.getMessages(session.id, after, 200).then(d => {
        const newMsgs = d.messages || []
        if (!newMsgs.length) return
        setMessages(prev => {
          if (!prev) return newMsgs
          const maxSeen = prev.length ? prev[prev.length - 1].seq : -1
          const fresh = newMsgs.filter(m => m.seq > maxSeen)
          if (!fresh.length) return prev
          transcriptMaxSeq.current = fresh[fresh.length - 1].seq
          const updated = [...prev, ...fresh]
          transcriptCache.current[session.id] = updated
          return updated
        })
      }).catch(() => {})
    }, 2000)

    return () => clearInterval(interval)
  }, [effectiveView, messagesLoaded, session.id, session.status, detail?.status])

  // When a session finishes a turn (goes idle/done), do one final fetch to
  // catch anything the 2s polling interval might have missed.
  const prevActiveRef = useRef(false)
  useEffect(() => {
    const activeNow = isWorking
    const wasActive = prevActiveRef.current
    prevActiveRef.current = activeNow
    if (wasActive && !activeNow) {
      // Final fetch: always run when the session goes idle, regardless of
      // whether the transcript view is active or messages were pre-loaded.
      // This closes the race where the last answer lands in the JSONL after
      // the incremental poller last ran but before status changed to idle.
      // If the transcript was never loaded (live-pane view), fetch everything;
      // otherwise fetch only what's new since transcriptMaxSeq.
      const after = messagesRef.current !== null ? transcriptMaxSeq.current : -1
      api.getMessages(session.id, after, 2000).then(d => {
        const newMsgs = d.messages || []
        if (!newMsgs.length) return
        setMessages(prev => {
          if (!prev) return newMsgs
          const maxSeen = prev.length ? prev[prev.length - 1].seq : -1
          const fresh = newMsgs.filter(m => m.seq > maxSeen)
          if (!fresh.length) return prev
          transcriptMaxSeq.current = fresh[fresh.length - 1].seq
          const updated = [...prev, ...fresh]
          transcriptCache.current[session.id] = updated
          return updated
        })
      }).catch(() => {})
    }
    // When session goes idle, refresh the slash queue — the backend may have
    // just drained an item and the local state is stale.
    if (wasActive && !activeNow) {
      api.getSlashQueue(session.id).then(d => setSlashQueue(d.items || [])).catch(() => {})
    }
  }, [isWorking])

  // Keep slash queue display in sync with the session-list poll.
  // session.sq_depth is updated every 2s by App; when it diverges from what
  // we have locally, re-fetch the full list so the UI stays accurate.
  const prevSqDepthRef = useRef(null)
  useEffect(() => {
    const depth = session.sq_depth ?? null
    if (depth === null) return
    if (prevSqDepthRef.current !== null && prevSqDepthRef.current !== depth) {
      api.getSlashQueue(session.id).then(d => setSlashQueue(d.items || [])).catch(() => {})
    }
    prevSqDepthRef.current = depth
  }, [session.sq_depth])

  // Keep transcript scrolled to bottom when new messages arrive while active,
  // but only if the user hasn't scrolled up to read something.
  useLayoutEffect(() => {
    if (effectiveView !== 'transcript' || !termRef.current) return
    if (!messages) return  // null during load or session switch
    const isActive = session.status === 'thinking' || session.status === 'running'
    if (!isActive) return
    const count = messages.length
    if (count <= prevMsgCountRef.current) return   // no new messages, don't scroll
    prevMsgCountRef.current = count
    if (followTranscriptRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [messages, effectiveView, session.status])

  // When switching to transcript view and messages are already loaded (e.g. toggling
  // live → transcript or auto switching), jump to the end immediately.
  const prevEffectiveViewRef = useRef(null)
  useLayoutEffect(() => {
    if (effectiveView !== 'transcript') { prevEffectiveViewRef.current = effectiveView; return }
    if (prevEffectiveViewRef.current === 'transcript') return  // already on transcript, no jump needed
    prevEffectiveViewRef.current = 'transcript'
    if (!messages?.length || !termRef.current) return
    termRef.current.scrollTop = termRef.current.scrollHeight
  }, [effectiveView, messages])
  // Configurable poll intervals (ms). Loaded from settings once.
  const pollBusyMs = useRef(400)
  const pollIdleMs = useRef(1200)
  useEffect(() => {
    settingsApi.getSettings().then(s => {
      if (s['poll-busy-ms'] > 0) pollBusyMs.current = s['poll-busy-ms']
      if (s['poll-idle-ms'] > 0) pollIdleMs.current = s['poll-idle-ms']
    }).catch(() => {})
  }, [])

  // Drag the left edge. Pointer events rather than mouse events so a trackpad
  // or touch drag behaves the same, and the listeners live on the window so the
  // drag survives the pointer leaving the 8px handle.
  const startResize = (e) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = widthRef.current
    const onMove = (ev) => {
      const next = clampPanelWidth(startWidth + (startX - ev.clientX))
      widthRef.current = next
      setWidth(next)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      window.removeEventListener('blur', onUp)
      document.body.classList.remove('resizing')
      // Written once, on release — a write per pointermove would be hundreds.
      localStorage.setItem('detail-width', String(widthRef.current))
    }
    // Capture the pointer so the release is delivered even if it happens
    // outside the window. Without it a drag that ended off-window left
    // `body.resizing` — and its `user-select: none` — stuck on, which killed
    // text selection everywhere until a reload. pointercancel and blur are
    // belt-and-braces for the cases capture cannot cover.
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* not fatal */ }
    document.body.classList.add('resizing')
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    window.addEventListener('blur', onUp)
  }

  const resetWidth = () => {
    widthRef.current = PANEL_DEFAULT
    setWidth(PANEL_DEFAULT)
    localStorage.setItem('detail-width', String(PANEL_DEFAULT))
  }

  // A window that shrank below the stored width would push the card list off
  // screen, so re-clamp rather than keeping a width that no longer fits.
  useEffect(() => {
    // Insurance: if a previous drag ever left this on, selection stays broken
    // for the whole app, and a reload is not an obvious remedy to reach for.
    document.body.classList.remove('resizing')
    const onResize = () => {
      const next = clampPanelWidth(widthRef.current)
      if (next !== widthRef.current) { widthRef.current = next; setWidth(next) }
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      document.body.classList.remove('resizing')
    }
  }, [])

  // Keep a ref to the latest session prop so the seed reads current fields
  // without making the effect depend on the full object (which changes identity
  // every poll tick via SessionsContext).
  const sessionRef = useRef(session)
  sessionRef.current = session

  useEffect(() => {
    if (!session) return
    const s = sessionRef.current
    // Seed detail immediately from the card-level data we already have.
    // This gives the panel something to render (title, status, path, summary)
    // while the real fetch — which includes the full transcript — completes.
    // The real fetch overwrites this within ~200ms on a local connection.
    setDetail(prev => {
      if (prev?.id === s.id) return prev   // already have real data, keep it
      return {
        id: s.id,
        title: s.title,
        cwd: s.cwd,
        status: s.status,
        control: s.control,
        summary: s.summary,
        last_message: s.last_message,
        last_output: s.last_output,
        model: s.model,
        effort: s.effort,
        kiro_profile: s.kiro_profile,
        agent: s.agent,
        output: null,   // not yet loaded — transcript shows loading state
        _seeded: true,
      }
    })
    const prevOutputLen = { current: 0 }
    const lastDetailJson = { current: '' }
    const fetchDetail = () => {
      api.getSession(session.id)
        .then(d => {
          // Skip setState when payload hasn't actually changed — avoids a
          // full DetailPanel re-render (2600 lines) on every poll tick.
          const sig = `${d.status}|${d.control}|${(d.output||[]).length}|${d.last_output||''}`
          if (sig === lastDetailJson.current) return
          lastDetailJson.current = sig
          const newLen = (d.output || []).length
          setDetail(d)
          if (newLen > prevOutputLen.current) {
            prevOutputLen.current = newLen
            setTimeout(() => {
              if (termRef.current && followTranscriptRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
            }, 50)
          }
        })
    }
    fetchDetail()
    const interval = setInterval(fetchDetail, 2000)
    return () => clearInterval(interval)
  }, [session?.id])

  // Reset per-session UI state when switching sessions.
  useEffect(() => {
    const restored = localStorage.getItem(`draft:${session?.id}`) || ''
    setDraft(restored); if (draftRef.current) draftRef.current.value = restored
    setRenaming(false); setPane(''); setEcho(''); setPrompting(false)
    // Or the next session's first prompt would be suppressed by this one's answer.
    answeredPromptRef.current = 0
    followPaneRef.current = true; setAtBottom(true)
    followTranscriptRef.current = true
    setAtTranscriptBottom(true)
    prevMsgCountRef.current = 0
    setStack([])
    setAutoAdvance(false)
    setAutoApprove(!!localStorage.getItem(`auto-approve:${session?.id}`))
    if (!session?.id) return
    api.getStack(session.id).then(d => setStack(d.items || [])).catch(() => {})
    api.getSlashQueue(session.id).then(d => setSlashQueue(d.items || [])).catch(() => {})
    api.getDelivery(session.id).then(d => setDelivery(d)).catch(() => {})
    api.getAutoAdvance(session.id).then(d => setAutoAdvance(!!d.enabled)).catch(() => {})
    api.getSessionDuration(session.id).then(d => setDurationRecord(d?.record || null)).catch(() => {})
  }, [session?.id])

  // Poll for new screenshots every 3 seconds when the composer is visible.
  useEffect(() => {
    const poll = () => {
      fetch('/api/screenshots/recent-files?minutes=5').then(r => r.json())
        .then(d => {
          if (d.items?.length) setPendingScreenshots(prev => {
            const existing = new Set(prev.map(s => s.name))
            const fresh = d.items.filter(s => !existing.has(s.name) && !dismissedScreenshots.current.has(s.name))
            return fresh.length ? [...prev, ...fresh] : prev
          })
        })
        .catch(() => {})
    }
    poll()  // immediate on mount
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  // The transcript is the resting view now, so it loads whenever we land on it
  // rather than only on an explicit tab click.
  // Guard: only fire once per session id to avoid a retry storm when a fetch
  // fails (messages===null + loadingMessages===false re-triggers indefinitely).
  useEffect(() => {
    if (effectiveView !== 'transcript') return
    if (transcriptLoadedFor.current === session.id) return
    transcriptLoadedFor.current = session.id
    setLoadingMessages(true)
    setMessagesError(null)
    api.getMessages(session.id, -1, 2000)
      .then(d => {
        const msgs = d.messages || []
        setMessages(msgs)
        if (msgs.length) transcriptMaxSeq.current = msgs[msgs.length - 1].seq
        // Update cache — evict oldest entry if over 20 sessions
        transcriptCache.current[session.id] = msgs
        const keys = Object.keys(transcriptCache.current)
        if (keys.length > 20) delete transcriptCache.current[keys[0]]
        // Scroll to end after load — regardless of whether the session is active.
        setTimeout(() => {
          if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
        }, 50)
      })
      .catch(err => {
        setMessagesError(err?.message || 'Failed to load transcript')
        // Reset the guard so an explicit Retry can re-trigger the load.
        transcriptLoadedFor.current = null
      })
      .finally(() => setLoadingMessages(false))
  }, [effectiveView, session.id])


  // The jsonl only gains entries once a turn completes, so it lags badly while
  // the agent is mid-answer. The tmux pane shows output as it is produced, so
  // poll it continuously for managed sessions — fast while something is
  // happening, slower when idle.
  useEffect(() => {
    if (control !== 'managed') { setPane(''); return }
    let alive = true
    // Ask for a good deal more than fits. How tall the tmux window is and how
    // much of it is worth reading are different questions: the window has to
    // match the box so the TUI reflows correctly, but capturing only that many
    // lines throws away all the scrollback. A 480px panel is about 14 rows, so
    // asking for exactly the visible rows showed roughly two percent of a
    // session that had 771 lines of history behind it.
    const wanted = Math.max((paneRows || 0) * 6, PANE_SCROLLBACK)
    let lastPaneText = ''
    const load = () => api.getPane(session.id, wanted)
      .then(d => {
        if (!alive) return
        const newPane = d.pane || ''
        if (newPane !== lastPaneText) { lastPaneText = newPane; setPane(newPane) }
        // A capture taken before kiro-cli had finished redrawing still shows the
        // menu, so an answered prompt would flash back for one poll. Ignore
        // `awaiting_prompt` briefly after answering — but only briefly: a prompt
        // that is still there after that is a new one, and must be shown.
        if (d.awaiting_prompt && Date.now() - answeredPromptRef.current < 1500) return
        setPrompting(!!d.awaiting_prompt)
        // Auto-approve: if enabled for this session, trust the tool call immediately.
        if (d.awaiting_prompt && localStorage.getItem(`auto-approve:${session.id}`)) {
          answeredPromptRef.current = Date.now()
          setPrompting(false)
          api.respond(session.id, 'trust').catch(() => {})
        }
      })
      .catch(() => {})
    load()
    // Held so `respond` can capture the pane the moment it has acted, instead of
    // waiting out the interval. A ref rather than an effect dependency: nudging
    // through the deps would tear down and rebuild the interval on every
    // keystroke sent to the menu.
    loadPaneRef.current = load
    const busy = sending || status === 'thinking' || status === 'running'
    const interval = setInterval(load, busy ? pollBusyMs.current : pollIdleMs.current)
    return () => { alive = false; clearInterval(interval); loadPaneRef.current = null }
  }, [session.id, control, status, sending, paneRows]) // eslint-disable-line react-hooks/exhaustive-deps

  // Follow the newest output, the way a terminal does — but only until the user
  // scrolls up to read something, or every poll would yank them back down.
  // This has to run after the new text is in the DOM: scrolling from inside the
  // fetch callback measures the previous content's height and lands nowhere.
  useLayoutEffect(() => {
    const box = paneBoxRef.current
    if (box && followPaneRef.current) box.scrollTop = box.scrollHeight
  }, [pane])

  const onPaneScroll = () => {
    const box = paneBoxRef.current
    if (!box) return
    const isAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < PANE_FOLLOW_SLACK
    followPaneRef.current = isAtBottom
    setAtBottom(isAtBottom)
  }

  const scrollToBottom = () => {
    const box = paneBoxRef.current
    if (!box) return
    box.scrollTop = box.scrollHeight
    followPaneRef.current = true
    setAtBottom(true)
  }

  const TRANSCRIPT_FOLLOW_SLACK = 40
  const onTranscriptScroll = () => {
    const box = termRef.current
    if (!box) return
    const isAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < TRANSCRIPT_FOLLOW_SLACK
    followTranscriptRef.current = isAtBottom
    setAtTranscriptBottom(isAtBottom)
  }

  const scrollTranscriptToBottom = () => {
    const box = termRef.current
    if (!box) return
    box.scrollTop = box.scrollHeight
    followTranscriptRef.current = true
    setAtTranscriptBottom(true)
  }

  // Keep the tmux window the size of the box it is being displayed in, so the
  // TUI reflows with the window instead of rendering a fixed frame surrounded
  // by dead space. Driven by ResizeObserver, so maximising, dragging the panel
  // edge and resizing the OS window all go through the same path.
  useEffect(() => {
    if (control !== 'managed' || effectiveView !== 'live') return
    const box = paneBoxRef.current
    if (!box || typeof ResizeObserver === 'undefined') return

    let timer = null
    const measure = () => {
      const probe = metricRef.current
      if (!probe) return
      const cell = probe.getBoundingClientRect()
      const cellWidth = cell.width / CELL_SAMPLE
      const cellHeight = cell.height
      if (!(cellWidth > 0) || !(cellHeight > 0)) return
      // clientWidth already excludes the scrollbar; the padding does not come
      // out of it, so subtract that too.
      const style = getComputedStyle(box)
      const usableWidth = box.clientWidth
        - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      const usableHeight = box.clientHeight
        - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
      const cols = Math.floor(usableWidth / cellWidth)
      const rows = Math.floor(usableHeight / cellHeight)
      // A box measured mid-layout reports next to nothing. Clamping that to the
      // floor and sending it would shrink a real session to 6 rows, so skip the
      // measurement instead and wait for the observer to fire again.
      if (cols < PANE_MIN_COLS || rows < PANE_MIN_ROWS) return
      setPaneRows(rows)
      const sent = sentSizeRef.current
      if (sent.cols === cols && sent.rows === rows) return
      sentSizeRef.current = { cols, rows }
      api.resizeSession(session.id, cols, rows)
        .catch(() => { sentSizeRef.current = { cols: 0, rows: 0 } })
    }

    // Debounced: a drag emits a resize per frame, and each one would be a tmux
    // call plus a full TUI redraw.
    const observer = new ResizeObserver(() => {
      clearTimeout(timer)
      timer = setTimeout(measure, 120)
    })
    observer.observe(box)
    measure()
    return () => { clearTimeout(timer); observer.disconnect() }
  }, [session.id, control, effectiveView, expanded])

  // allow / trust / deny / dismiss end the menu. Up, Down and Enter move around
  // inside it, so they must not hide it — Enter's effect depends on what is
  // highlighted, and guessing wrong would hide a prompt that is still waiting.
  const ENDS_PROMPT = new Set(['allow', 'trust', 'deny', 'dismiss'])

  const respond = (choice) => {
    // The prompt block is driven by the pane poll, and while a prompt is up
    // nothing is thinking — so the interval is the slow 1200ms one. Waiting for
    // it means the button sits there looking unclicked for over a second after
    // the keys have already gone to tmux. So: hide it now, and capture the pane
    // as soon as kiro-cli has had time to redraw rather than on the next tick.
    if (ENDS_PROMPT.has(choice)) {
      answeredPromptRef.current = Date.now()
      setPrompting(false)
    }
    api.respond(session.id, choice)
      .then(d => {
        if (!d.error) return
        // It did not go through, so stop hiding the prompt: a menu that is still
        // waiting while the UI pretends it was answered is the worse failure.
        answeredPromptRef.current = 0
        setPrompting(true)
        notify(`Could not answer the prompt: ${d.error}`, 'error')
      })
      .then(() => onRefresh && setTimeout(onRefresh, 800))
    // Two nudges: the first catches the common case, the second covers a slower
    // redraw. Cheap — one capture-pane each.
    setTimeout(() => loadPaneRef.current && loadPaneRef.current(), 200)
    setTimeout(() => loadPaneRef.current && loadPaneRef.current(), 700)
  }

  const handoff = async (terminal) => {
    if (!terminal) return
    const label = (options.terminals || []).find(x => x.id === terminal)?.label || terminal
    const ok = await askConfirm(
      `Hand this session over to ${label}?`,
      'It will be asked to quit here, then reopened there with ' +
      '`cd <dir> && kiro-cli chat --resume-id <id>`. After that it runs outside ' +
      'the app and shows as foreign.',
      'Hand off',
    )
    if (!ok) return
    notify(`Handing off to ${label}…`)
    api.handoffSession(session.id, terminal)
      .then(d => {
        if (d.error) { notify(`Handoff failed: ${d.error}`, 'error'); return }
        if (!d.ran_command) {
          notify(
            'Warp cannot be told to run a command, so a tab opened in the directory' +
            (d.clipboard ? ' and the resume command is on your clipboard — paste it.' : '.'),
            'error',
          )
        } else {
          notify(`Reopened in ${label}` + (d.quit_mode === 'kill' ? ' (it did not quit cleanly)' : ''))
        }
        onClose()
      })
      .then(() => { if (onRefresh) setTimeout(onRefresh, 2000) })
  }

  const sendText = (text) => {
    if (!text.trim() && attachments.length === 0) return
    if (!canSend) return
    setSending(true)
    // Echo immediately: the pane needs a moment to redraw, and silence right
    // after pressing send reads as a dropped message.
    setEcho(text)
    const readyAttachments = attachments.filter(a => !a.uploading)
    api.sendInput(session.id, text, readyAttachments)
      .then(d => {
        if (d.error) { notify(`Send failed: ${d.error}`, 'error'); setEcho('') }
        else clearAttachments()
      })
      .finally(() => {
        setSending(false)
        if (onRefresh) setTimeout(onRefresh, 1000)
      })
  }

  // Toolbar / chip commands: queue when the session is working so they don't
  // interrupt the current turn. Send directly only when the session is idle.
  const queueOrSend = (text) => {
    if (!text.trim()) return
    if (isWorking) {
      api.pushSlashQueue(session.id, text)
        .then(d => {
          if (d.ok) {
            setSlashQueue(d.queue)
            notify(`Queued: ${text}`, 'info')
          }
        })
        .catch(() => {})
    } else {
      sendText(text)
    }
  }

  // Drop the echo once the pane actually shows it, so it is not duplicated.
  useEffect(() => {
    if (!echo || effectiveView !== 'pane') return
    const head = echo.trim().slice(0, 40)
    if (head && pane.includes(head)) setEcho('')
  }, [pane, echo, effectiveView])

  // Scroll transcript to bottom when a pending bubble appears
  useEffect(() => {
    if (!echo || !termRef.current) return
    termRef.current.scrollTop = termRef.current.scrollHeight
  }, [echo])
  // Clear the echo indicator when session status changes (agent picked it up)
  // or after a 8s fallback timeout.
  const echoTimerRef = useRef(null)
  useEffect(() => {
    if (!echo) { clearTimeout(echoTimerRef.current); return }
    if (echo.startsWith('/')) {
      clearTimeout(echoTimerRef.current)
      echoTimerRef.current = setTimeout(() => setEcho(''), 12000)
    }
    return () => clearTimeout(echoTimerRef.current)
  }, [echo])

  // Clear echo when transcript messages update with the new entry (bubble is now real)
  useEffect(() => {
    if (!echo || !messages?.length) return
    const head = echo.trim().slice(0, 40)
    const found = messages.some(m =>
      (m.role === 'user' || m.type === 'user') &&
      (m.text || m.content?.[0]?.text || '').startsWith(head)
    )
    if (found) setEcho('')
  }, [messages])

  // Load merged starter chips (project chips + optionally global) for this session's cwd
  useEffect(() => {
    if (!session?.cwd) return
    let cancelled = false

    const loadChips = () => {
      if (cancelled) return
      Promise.all([
        settingsApi.getSettings(),
        getProjectSettings(session.cwd),
      ]).then(([globalSettings, projectSettings]) => {
        if (cancelled) return
        const globalChips = Array.isArray(globalSettings['composer-chips']) && globalSettings['composer-chips'].length
          ? globalSettings['composer-chips'].filter(validChip)
          : DEFAULT_COMPOSER_CHIPS.filter(validChip)
        const projectChips = Array.isArray(projectSettings['composer-chips'])
          ? projectSettings['composer-chips'].filter(validChip)
          : []
        const includeGlobal = projectSettings['chips-include-global'] !== false
        // If project has chips: show them + optionally global
        // If no project chips: only show global if includeGlobal is explicitly true
        //   (default true when never set, false when user turned it off)
        const hasProjectSetting = 'chips-include-global' in projectSettings
        if (projectChips.length) {
          setStarterChips(includeGlobal ? [...projectChips, ...globalChips] : projectChips)
        } else if (!hasProjectSetting || includeGlobal) {
          // No project chips and global not explicitly excluded — show global
          setStarterChips(globalChips)
        } else {
          // User explicitly turned off global chips and has no project chips
          setStarterChips([])
        }
      }).catch(() => {})
    }

    loadChips()
    // Reload whenever the window regains focus — catches edits made in Settings tab
    window.addEventListener('focus', loadChips)
    document.addEventListener('visibilitychange', loadChips)
    return () => {
      cancelled = true
      window.removeEventListener('focus', loadChips)
      document.removeEventListener('visibilitychange', loadChips)
    }
  }, [session?.cwd, chipsModalOpen])

  const submitDraft = (e) => {
    if (e) e.preventDefault()
    // Read from DOM ref — draft state is not updated on every keystroke
    const text = draftRef.current ? draftRef.current.value : draft
    if (!text.trim() && attachments.length === 0) return
    // Route to CLI when in CLI send mode and a CLI is bound and idle
    if (cliSendMode && cliStatus?.bound) {
      sendToCli(text)
      return
    }
    sendText(text)
    // Newest first, deduped against the immediately preceding entry
    if (text.trim()) {
      const next = (history[0] === text ? history : [text, ...history]).slice(0, HISTORY_LIMIT)
      setHistory(next)
      writeHistory(session.id, next)  // write immediately — don't rely on effect timing
    }
    setHistoryIndex(-1)
    setDraft('')
    if (draftRef.current) draftRef.current.value = ''
  }

  // Persist draft per session — write to both localStorage and backend prefs
  // so it survives WKWebView's non-synchronous localStorage flush on close.
  useEffect(() => {
    if (!session?.id) return
    const key = `draft:${session.id}`
    // Read from DOM ref — draft state is only updated on empty/non-empty flip
    const text = draftRef.current ? draftRef.current.value : draft
    if (text) {
      localStorage.setItem(key, text)
      fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: text }) }).catch(() => {})
    } else {
      localStorage.removeItem(key)
      // Only clear from backend after initialization — don't overwrite a saved
      // draft before loadHistoryFromPrefs has had a chance to restore it.
      if (draftInitializedRef.current) {
        fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [key]: null }) }).catch(() => {})
      }
    }
  }, [draft, session?.id])
  // to follow the session explicitly or it would leak between them.
  // Load history from backend on session switch (survives WKWebView restarts)
  useEffect(() => {
    draftInitializedRef.current = false
    loadHistoryFromPrefs(session.id).then(fromBackend => {
      setHistory(fromBackend || readHistory(session.id))
      // After seeding localStorage from backend, reload draft too
      const savedDraft = localStorage.getItem(`draft:${session.id}`) || ''
      setDraft(savedDraft); if (draftRef.current) draftRef.current.value = savedDraft
      preDraftRef.current = ''
      draftInitializedRef.current = true
    })
    setHistoryIndex(-1)
  }, [session.id])

  // Shell-style recall. Only when the caret is on the first line, so arrow-up
  // still moves within a multi-line draft instead of replacing it.
  const recallHistory = (e, direction) => {
    if (!history.length) return
    const box = e.target
    // ArrowDown: only recall if caret is at end
    if (direction < 0 && box.selectionStart !== box.value.length) return
    // ArrowUp: only block when draft has multiple lines AND caret isn't on first line
    if (direction > 0 && box.value.includes('\n')) {
      const caretOnFirstLine = box.value.slice(0, box.selectionStart).indexOf('\n') === -1
      if (!caretOnFirstLine) return
    }
    const next = historyIndex + direction
    if (next < -1 || next >= history.length) return
    e.preventDefault()
    // Save the current draft before the first recall so ↓ back to -1 restores it
    if (historyIndex === -1 && direction > 0) preDraftRef.current = draftRef.current ? draftRef.current.value : draft
    setHistoryIndex(next)
    const recalled = next === -1 ? preDraftRef.current : history[next]
    setDraft(recalled)
    if (draftRef.current) draftRef.current.value = recalled
  }

  // Button-triggered history recall (no keyboard event available)
  const recallByButton = (direction) => {
    if (!history.length) return
    const next = historyIndex + direction
    if (next < -1 || next >= history.length) return
    if (historyIndex === -1 && direction > 0) preDraftRef.current = draft
    setHistoryIndex(next)
    setDraft(next === -1 ? preDraftRef.current : history[next])
    draftRef.current?.focus()
  }

  // Commands needing an argument are staged in the composer so the user can
  // finish the sentence; the rest are sent straight through.
  const runCommand = (cmd) => {
    if (cmd.needs_arg) {
      // For /goal, prepend --max N from the per-device setting if set
      let prefix = cmd.cmd
      if (cmd.cmd.startsWith('/goal ')) {
        const maxIter = localStorage.getItem('goal-max-iterations')
        if (maxIter && parseInt(maxIter, 10) > 0) {
          prefix = `/goal --max ${maxIter} `
        }
      }
      setDraft(prefix)
      if (draftRef.current) { draftRef.current.value = prefix; draftRef.current.focus() }
      return
    }
    queueOrSend(cmd.cmd)
  }

  const changeModel = (model) => { if (model) queueOrSend(`/model ${model}`) }
  const changeEffort = (level) => { if (level) queueOrSend(`/effort ${level}`) }

  const startRename = () => {
    setTitleDraft(detail?.title || session.title || '')
    setRenaming(true)
  }

  const saveRename = () => {
    const title = titleDraft.trim()
    setRenaming(false)
    if (!title || title === (detail?.title || session.title)) return
    api.renameSession(session.id, title)
      .then(d => { if (d.error) notify(`Rename failed: ${d.error}`, 'error') })
      .then(() => onRefresh && onRefresh())
  }

  if (!session) return null
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.done
  const title = detail?.title || session.title

  return (<>
    <div className={`detail-panel ${expanded ? 'detail-expanded' : ''} ${fromWall ? 'detail-from-wall' : ''}`}
         style={expanded ? undefined : { width }}>
      {!expanded && (
        <div className="detail-resize" onPointerDown={startResize} onDoubleClick={resetWidth}
             title="Drag to resize · double-click to reset" />
      )}
      <div className="detail-header">
        <span className="detail-status" style={{ color: cfg.color }}>{cfg.label}</span>
        <div className="detail-header-title">
          {renaming ? (
            <input className="detail-rename-inline" value={titleDraft} autoFocus
                   onChange={(e) => setTitleDraft(e.target.value)}
                   onBlur={saveRename}
                   onKeyDown={(e) => { if (e.key === 'Enter') saveRename(); if (e.key === 'Escape') setRenaming(false) }} />
          ) : (
            <span className="detail-title-inline" title="Click to rename" onClick={startRename}>
              {title}
            </span>
          )}
          {session.parent_id && (
            <span className="detail-lineage" title={`Forked from session ${session.parent_id}`}>
              ⎇ {session.parent_id.slice(0, 8)}{session.branch_point != null ? ` @ turn ${session.branch_point}` : ''}
            </span>
          )}
          {detail?.attach && (
            <button className="detail-attach-copy"
                    title={detail.attach}
                    onClick={() => {
                      navigator.clipboard?.writeText(detail.attach).catch(() => {})
                      notify('Attach command copied', 'info')
                    }}>
              ⎘ attach
            </button>
          )}
        </div>
        <div className="detail-actions">
          {control === 'foreign' && (
            <button className="detail-switch" onClick={() => onTakeover(session)}>⇩ Take over</button>
          )}
          {control === 'archived' && (
            <button className="detail-switch" onClick={() => onResume(session)}>▶ Resume</button>
          )}
          {control === 'acp' && (
            <span className="detail-acp-badge" title="Input routed via ACP — no tmux pane needed">◉ ACP</span>
          )}
          {/* Favourite — always present */}
          {onToggleFavourite && (() => {
            const isFav = (favourites || []).some(f => f.id === session.id)
            return (
              <button className={`detail-icon detail-icon-fav${isFav ? ' active' : ''}`}
                      title={isFav ? 'Remove from favourites' : 'Add to favourites'}
                      onClick={() => onToggleFavourite(session)}>
                {isFav ? '★' : '☆'}
              </button>
            )
          })()}
          {/* Expand/fullscreen — always present */}
          <button className="detail-icon detail-icon-expand" onClick={onToggleExpand}
                  title={expanded ? 'Exit fullscreen (F)' : 'Fullscreen (F)'}>
            {expanded ? '⤡' : '⤢'}
          </button>
          {/* Items below hidden for foreign/archived sessions — no input possible */}
          {control !== 'foreign' && control !== 'archived' && (<>
          {/* Correction — icon only */}
          <button className="detail-correct detail-icon" title="Log a correction — agent did something wrong"
                  onClick={logCorrection}>⚑</button>
          {/* Restart here — archive this session, start fresh with same name + queue */}
          {control === 'managed' && onRestartHere && (
            <button className="detail-icon" title="Restart here — archive this session and start a fresh one with the same name and queue"
                    onClick={() => onRestartHere(session.id)}>↺</button>
          )}
          {/* Side chat — keep label, it's a mode */}
          <SideChat
            ref={sideChatRef}
            sessionId={session.id}
            notify={notify}
            respond={respond}
            runCommand={runCommand}
            options={options}
          />
          {/* CLI binding chip — only for foreign sessions (manually started kiro-cli).
              Managed sessions already accept input directly via the composer. */}
          {control === 'foreign' && (() => {
            const st = cliStatus
            if (!st) return null
            const isBound = st.bound
            const chipStatus = st.status
            const isNatural = st.tmux_session === `kiro-${session.id}`
            const dot = chipStatus === 'idle' ? '🟢' : chipStatus === 'thinking' ? '🟡' : chipStatus === 'awaiting-approval' ? '🔴' : isBound ? '⚪' : null
            const label = chipStatus === 'idle' ? 'CLI idle' : chipStatus === 'thinking' ? 'CLI busy' : chipStatus === 'awaiting-approval' ? 'CLI waiting' : chipStatus === 'dead' ? 'CLI dead' : null
            if (!isBound) {
              return (
                <button className="detail-cli-chip detail-cli-unbound" onClick={openCliBinder}
                        title="Connect to this CLI's tmux pane — send commands and track idle/busy status">
                  + CLI
                </button>
              )
            }
            return (
              <button className={`detail-cli-chip detail-cli-${chipStatus}`}
                      onClick={openCliBinder}
                      title={isNatural
                        ? `CLI connected — click to switch to a different pane`
                        : `Bound to ${st.tmux_session} — click to change`}>
                {dot} {label || chipStatus}
              </button>
            )
          })()}
          {/* CLI bind picker */}
          {cliBindOpen && (
            <div className="detail-cli-picker" onClick={() => setCliBindOpen(false)}>
              <div className="detail-cli-picker-inner" onClick={e => e.stopPropagation()}>
                <div className="detail-cli-picker-title">Attach a CLI pane</div>
                {cliInstances.length === 0 && (
                  <div className="detail-cli-picker-empty">No kiro-cli panes found in tmux.</div>
                )}
                {cliInstances.map(inst => (
                  <div key={inst.tmux_session} className="detail-cli-picker-row"
                       onClick={() => bindCli(inst.tmux_session)}>
                    <span className={`detail-cli-status-dot detail-cli-dot-${inst.status}`}>
                      {inst.status === 'idle' ? '🟢' : inst.status === 'thinking' ? '🟡' : '⚪'}
                    </span>
                    <span className="detail-cli-picker-name">{inst.tmux_session}</span>
                    <span className="detail-cli-picker-cwd">{inst.cwd_short || inst.cwd}</span>
                    <span className={`detail-cli-picker-status detail-cli-picker-status-${inst.status}`}>{inst.status}</span>
                  </div>
                ))}
                {cliStatus?.bound && (
                  <button className="detail-cli-picker-unbind" onClick={() => { setCliBindOpen(false); unbindCli() }}>
                    Detach current
                  </button>
                )}
                <button className="detail-cli-picker-cancel" onClick={() => setCliBindOpen(false)}>Cancel</button>
              </div>
            </div>
          )}
          {/* Overflow: Hand off, new session, wall toggle */}
          <div className="detail-overflow-wrap">
            <button className="detail-icon detail-overflow-btn"
                    title="More actions"
                    onClick={() => setOverflowOpen(v => !v)}>···</button>
            {overflowOpen && (
              <div className="detail-overflow-menu" onClick={() => setOverflowOpen(false)}>
                {canSend && (
                  <button className="detail-overflow-item" onClick={() => handoff(handoffTerminal)}
                          title={`Reopen in ${(options.terminals || []).find(x => x.id === handoffTerminal)?.label || handoffTerminal}`}>
                    ⇱ Hand off
                  </button>
                )}
                {onNewSession && (
                  <button className="detail-overflow-item" onClick={() => onNewSession(session.cwd)}>
                    ＋ New session
                  </button>
                )}
                {onToggleFocus && !expanded && (
                  <button className="detail-overflow-item" onClick={onToggleFocus}>
                    {focusMode ? '◧ Exit focus' : '▣ Focus mode'}
                  </button>
                )}
                {session.cwd && (
                  <button className="detail-overflow-item" onClick={() => setSecretsModalOpen(true)}>
                    🔑 Secrets
                  </button>
                )}
                {session.cwd && (
                  <button className="detail-overflow-item" onClick={() => setChipsModalOpen(true)}>
                    🧩 Chips
                  </button>
                )}
              </div>
            )}
          </div>
          </>)}
          <button className="detail-close" onClick={onClose}>✕</button>
        </div>
      </div>
      {showCorrections && (
        <div className="detail-corrections">
          <div className="corrections-header">
            <span>
              {(() => {
                const claims = corrections.filter(c => c.kind === 'unverified_claim')
                const corrs  = corrections.filter(c => c.kind !== 'unverified_claim')
                if (claims.length > 0 && corrs.length > 0)
                  return `${corrs.length} correction${corrs.length !== 1 ? 's' : ''} · ${claims.length} unverified claim${claims.length !== 1 ? 's' : ''}`
                if (claims.length > 0)
                  return `${claims.length} unverified claim${claims.length !== 1 ? 's' : ''}`
                return `Corrections (${corrs.length})`
              })()}
            </span>
            <button className="corrections-close" onClick={() => setShowCorrections(false)}>×</button>
          </div>
          {corrections.length === 0 && (
            <div className="corrections-empty">No corrections yet.</div>
          )}
          {corrections.map(c => {
            const isClaim = c.kind === 'unverified_claim'
            return (
              <div key={c.id} className={`correction-row correction-${c.status}${isClaim ? ' correction-claim' : ''}`}>
                <div className="correction-meta">
                  <span className="correction-time">{new Date(c.ts * 1000).toLocaleTimeString()}</span>
                  {isClaim
                    ? <span className="correction-kind-badge" title="Machine-detected: claim keyword found with no observation tool in the turn">⚠ auto-detected</span>
                    : <span className="correction-status">{c.status}</span>
                  }
                  {c.rules_in_context?.length > 0 && (
                    <span className="correction-rules" title={c.rules_in_context.join(', ')}>
                      {c.rules_in_context.length} rule{c.rules_in_context.length !== 1 ? 's' : ''} in context
                    </span>
                  )}
                  {c.last_message_seq != null && (
                    <span className="correction-seq">seq {c.last_message_seq}</span>
                  )}
                </div>
                {isClaim && c.claim_text && (
                  <div className="correction-message correction-claim-text" title="Sentence that triggered claim detection">
                    "{c.claim_text.slice(0, 300)}{c.claim_text.length > 300 ? '…' : ''}"
                  </div>
                )}
                {!isClaim && c.assistant_message && (
                  <div className="correction-message" title="What the agent said at correction time">
                    {c.assistant_message.slice(0, 300)}{c.assistant_message.length > 300 ? '…' : ''}
                  </div>
                )}
                {!isClaim && c.status === 'open' && (
                  <div className="correction-note-row">
                    <input
                      className="correction-note-input"
                      placeholder="What went wrong? (optional)"
                      defaultValue={c.note || ''}
                      id={`correction-note-${c.id}`}
                    />
                  </div>
                )}
                {c.note && c.status !== 'open' && (
                  <div className="correction-note-display">📝 {c.note}</div>
                )}
                <div className="correction-actions">
                  {!isClaim && c.status !== 'confirmed' && (
                    <button className="correction-btn correction-confirm"
                            onClick={() => {
                              const input = document.getElementById(`correction-note-${c.id}`)
                              setStatus(c.id, 'confirmed', input?.value || c.note || '')
                            }}>Confirm</button>
                  )}
                  {c.status !== 'withdrawn' && (
                    <button className="correction-btn correction-withdraw"
                            onClick={() => setStatus(c.id, 'withdrawn', c.note || '')}>
                      {isClaim ? 'False positive' : 'Withdraw'}
                    </button>
                  )}
                  {c.status === 'withdrawn' && isClaim && (
                    <span className="correction-dismissed">dismissed</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {detail?.dead_pane && (
        <div className="detail-warning">Process exited — the tmux pane is kept so its error stays readable.</div>
      )}
      {/* Only shown when the pane really is displaying the permission menu —
          an ordinary question from the agent is not an approval request. */}
      {prompting && (
        <div className="detail-prompt">
          <div className="detail-prompt-label">Permission required</div>
          <pre className="prompt-pane">{pane.trimEnd().split('\n').slice(-14).join('\n')}</pre>
          <div className="detail-prompt-actions">
            <button className="prompt-allow" onClick={() => respond('allow')}>Allow once</button>
            <button onClick={() => respond('trust')}>Trust for session</button>
            <button onClick={() => respond('deny')}>Deny</button>
            <button
              className={`prompt-auto-approve ${autoApprove ? 'active' : ''}`}
              onClick={toggleAutoApprove}
              title={autoApprove ? 'Auto-approve on — click to disable' : 'Enable auto-approve for this session'}
            >
              {autoApprove ? '⚡ Auto on' : '⚡ Auto'}
            </button>
            <span className="launcher-spacer" />
            <button onClick={() => respond('Up')} title="Move up">↑</button>
            <button onClick={() => respond('Down')} title="Move down">↓</button>
            <button onClick={() => respond('Enter')} title="Select">↵</button>
            <button onClick={() => respond('dismiss')} title="Close the menu">esc</button>
          </div>
        </div>
      )}
      {/* Session navigation strip — only for managed sessions, shows siblings
          so you can hop between sessions without closing the panel. */}
      {sessions && sessions.length > 0 && onSelect && (() => {
        const managed = sessions.filter(s => s.control === 'managed' || s.control === 'starting')
        if (!managed.length) return null
        const idx = managed.findIndex(s => s.id === session.id)
        const prev = idx > 0 ? managed[idx - 1] : null
        const next = idx >= 0 && idx < managed.length - 1 ? managed[idx + 1] : null
        return (
          <div className="detail-compact-bar">
            <button className="dsn-arrow" disabled={!prev}
                    title={prev ? prev.name : undefined}
                    onClick={() => prev && onSelect(prev)}>‹</button>
            <div className="dsn-sessions">
              {managed.map(s => {
                const chipCfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.done
                const needsYouChip = s.status === 'awaiting-approval' || s.status === 'error'
                return (
                  <button key={s.id}
                          className={`dsn-chip ${s.id === session.id ? 'active' : ''} ${needsYouChip ? 'dsn-chip-attention' : ''}`}
                          title={`${s.title || s.name} — ${chipCfg.label}`}
                          onClick={() => onSelect(s)}>
                    <span className="dsn-chip-dot" style={{ color: chipCfg.color }}>●</span>
                    {s.name || s.folder || '…'}
                  </button>
                )
              })}
            </div>
            <button className="dsn-arrow" disabled={!next}
                    title={next ? next.name : undefined}
                    onClick={() => next && onSelect(next)}>›</button>
            <span className="detail-bar-sep" />
            <span className="detail-view-label">
              {effectiveView === 'live' ? 'Live' : 'Transcript'}
              {effectiveView === 'live' && isWorking ? ' ●' : ''}
              {effectiveView === 'transcript' && messages ? ` · ${messages.length}` : ''}
            </span>
            {canLive && (
              <div className="detail-view-pin">
                <button className={`detail-pin-btn ${!viewOverride ? 'active' : ''}`} onClick={() => setViewOverride(null)} title="Follow session state">auto</button>
                <button className={`detail-pin-btn ${viewOverride === 'live' ? 'active' : ''}`} onClick={() => setViewOverride('live')} title="Always show live pane">live</button>
                <button className={`detail-pin-btn ${viewOverride === 'transcript' ? 'active' : ''}`} onClick={() => setViewOverride('transcript')} title="Always show transcript">transcript</button>
              </div>
            )}
            {!isMobileBrowser && (
              <button className={`detail-pin-btn-shell ${viewOverride === 'shell' ? 'active' : ''}`}
                      onClick={() => {
                        if (viewOverride === 'shell') { setViewOverride(null); return }
                        setViewOverride('shell')
                        if (!activeShellId) openShellForCwd(session?.cwd)
                      }}
                      title="Open terminal shell for this project">⌨ shell</button>
            )}
            {session.context_pct != null && session.context_pct !== '' && (
              <ContextPct pct={session.context_pct} onCompact={() => queueOrSend('/compact')} />
            )}
          </div>
        )
      })()}
      {!(sessions && sessions.length > 0 && onSelect) && (
        <div className="detail-compact-bar detail-compact-bar-solo">
          <span className="detail-view-label">
            {effectiveView === 'live' ? 'Live' : 'Transcript'}
            {effectiveView === 'live' && isWorking ? ' ●' : ''}
            {effectiveView === 'transcript' && messages ? ` · ${messages.length}` : ''}
          </span>
          {session.context_pct != null && session.context_pct !== '' && (
            <ContextPct pct={session.context_pct} onCompact={() => queueOrSend('/compact')} />
          )}
          {canLive && (
            <div className="detail-view-pin">
              <button className={`detail-pin-btn ${!viewOverride ? 'active' : ''}`} onClick={() => setViewOverride(null)}>auto</button>
              <button className={`detail-pin-btn ${viewOverride === 'live' ? 'active' : ''}`} onClick={() => setViewOverride('live')}>live</button>
              <button className={`detail-pin-btn ${viewOverride === 'transcript' ? 'active' : ''}`} onClick={() => setViewOverride('transcript')}>transcript</button>
            </div>
          )}
          <button className={`detail-pin-btn-shell ${viewOverride === 'shell' ? 'active' : ''}`}
                  onClick={() => {
                    if (viewOverride === 'shell') { setViewOverride(null); return }
                    setViewOverride('shell')
                    if (!activeShellId) openShellForCwd(session?.cwd)
                  }}
                  title="Terminal shell">⌨ shell</button>
          {session.context_pct != null && session.context_pct !== '' && (
            <ContextPct pct={session.context_pct} onCompact={() => queueOrSend('/compact')} />
          )}
        </div>
      )}
      {effectiveView === 'live' && canLive && (
        <div className={`terminal terminal-live pane-${paneTheme}`} ref={liveRef}>
          {/* Off-screen cell probe: the tmux geometry is derived from what a
              character actually measures in this font at this zoom, not from a
              guessed constant. */}
          <pre className="live-metric" ref={metricRef} aria-hidden="true">{'M'.repeat(CELL_SAMPLE)}</pre>
          <pre className="live-pane" ref={paneBoxRef} onScroll={onPaneScroll}>{pane.replace(/\s+$/, '')}</pre>
          {echo && <pre className="live-echo">❯ {echo}</pre>}
          {!atBottom && (
            <button className="live-scroll-btn" onClick={scrollToBottom} title="Scroll to latest">↓</button>
          )}
        </div>
      )}
      {effectiveView === 'transcript' && (
        <div className={`chat-transcript pane-${paneTheme}`} ref={termRef} onScroll={onTranscriptScroll}>
          <TranscriptErrorBoundary onRetry={() => { setMessages(null); setLoadingMessages(false); setMessagesError(null); transcriptLoadedFor.current = null }}>
          {messagesError && !loadingMessages && (
            <div className="transcript-load-error">
              <span>Failed to load transcript: {messagesError}</span>
              <button onClick={() => { setMessagesError(null); transcriptLoadedFor.current = null }}>Retry</button>
            </div>
          )}
          {(loadingMessages || messages === null) && !messagesError && (() => {
            // Use session prop fields — always available without waiting for detail fetch.
            // last_message is the last assistant text; title is the first user prompt.
            // Show the last user entry from detail.output if available, otherwise title.
            const lastUser = detail?.output
              ? [...detail.output].reverse().find(e => e.type === 'user')
              : null
            const lastUserText = lastUser?.text || session.title || null
            return (
              <>
                {lastUserText && (
                  <div className="chat-row chat-row-user">
                    <div className="chat-bubble-user">
                      <div className="chat-bubble-text">{lastUserText.slice(0, 500)}</div>
                    </div>
                  </div>
                )}
                {session.last_message && (
                  <div className="chat-row chat-row-assistant">
                    <div className="chat-assistant-text chat-assistant-placeholder">
                      {session.last_message.slice(0, 300)}
                    </div>
                  </div>
                )}
                <div className="chat-empty chat-loading-hint">Loading full transcript…</div>
              </>
            )
          })()}
          {messages && messages.length === 0 && !isWorking && <div className="chat-empty">No messages yet.</div>}
          {messages && (() => {
            // Pre-group messages into renderable blocks:
            // - user turns → bubble
            // - consecutive assistant+tool pairs (agentic loop) → one collapsed tool cluster
            // - final assistant text in a turn (no tools) → plain response
            const blocks = []
            let i = 0
            while (i < messages.length) {
              const msg = messages[i]
              if (msg.role === 'user') {
                blocks.push({ type: 'user', msg, key: msg.seq })
                i++
                continue
              }
              if (msg.role === 'assistant') {
                // Collect the agentic loop: all assistant(tools)+tool pairs
                const toolCalls = []  // { assistantMsg, toolResult }
                let j = i
                while (j < messages.length) {
                  const a = messages[j]
                  if (a.role !== 'assistant') break
                  const hasTools = a.tools?.length > 0
                  // Look ahead for a tool result
                  const t = messages[j + 1]
                  const hasResult = t && t.role === 'tool'
                  if (hasTools || hasResult) {
                    toolCalls.push({ assistantMsg: a, toolResult: hasResult ? t : null })
                    j += hasResult ? 2 : 1
                  } else {
                    break
                  }
                }
                // What's left at j: final assistant text (no tools), or nothing
                const finalMsg = j < messages.length && messages[j].role === 'assistant' && !messages[j].tools?.length
                  ? messages[j]
                  : null
                if (finalMsg) j++

                if (toolCalls.length > 0) {
                  blocks.push({ type: 'tool-cluster', toolCalls, finalMsg, key: msg.seq })
                } else if (finalMsg) {
                  blocks.push({ type: 'assistant', msg: finalMsg, key: finalMsg.seq })
                } else if (msg.role === 'assistant') {
                  // lone assistant with no tools and no following text (shouldn't happen, but be safe)
                  blocks.push({ type: 'assistant', msg, key: msg.seq })
                  j = i + 1
                }
                i = j
                continue
              }
              // tool entries not consumed above — skip
              i++
            }

            return blocks.map(block => {
              if (!block || !block.type) return null
              if (block.type === 'user') {
                const msg = block.msg
                if (!msg) return null
                const ts = msg.timestamp
                  ? new Date(msg.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                  : null
                const forkFn = msg.is_turn ? () => {
                  const branchSeq = msg.seq > 0 ? msg.seq - 1 : 0
                  api.branchAt(session.id, branchSeq)
                    .then(d => {
                      if (d.ok) {
                        notify(`Forked → ${d.new_id?.slice(0, 8)}`, 'info')
                        onRefresh()
                        if (onSelect && d.new_id) {
                          setTimeout(() => onSelect({
                            id: d.new_id,
                            title: `(fork @${branchSeq}) ${session.title || ''}`,
                            cwd: session.cwd, status: 'thinking', control: 'managed'
                          }), 600)
                        }
                      } else notify(d.error || 'Fork failed', 'error')
                    })
                    .catch(() => notify('Could not fork', 'error'))
                } : null
                return (
                  <div key={block.key} className="chat-row chat-row-user">
                    {msg.text?.startsWith('[LIVE STEERING') ? (
                      <details className="chat-steering">
                        <summary className="chat-steering-summary">📡 Live steering</summary>
                        <div className="chat-steering-body">{msg.text?.slice(0, 2000)}</div>
                      </details>
                    ) : (
                      <>
                        <div className="chat-bubble-user">
                          {parseUserMessage(msg.text).map((seg, si) =>
                            seg.type === 'doc'
                              ? <DocCard key={si} segment={seg} />
                              : <div key={si} className="chat-bubble-text">{seg.content?.slice(0, 2000)}</div>
                          )}
                        </div>
                        <div className="chat-meta-user">
                          {ts && <span className="chat-ts">{ts}</span>}
                          <CopyButton text={msg.text} />
                          {forkFn && <button className="chat-fork" title="Fork from this point" onClick={forkFn}>⑂</button>}
                          {msg.is_turn && (
                            <button
                              className="chat-fork"
                              title="Save as template from this point"
                              onClick={() => setSatModal({ afterSeq: msg.seq > 0 ? msg.seq - 1 : 0 })}
                            >📋</button>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                )
              }

              if (block.type === 'tool-cluster') {
                const { toolCalls, finalMsg } = block
                // Flatten all tool names across all calls in the cluster
                const allTools = toolCalls.flatMap(tc => tc.assistantMsg.tools?.map(t => t.name || t) || [])
                const unique = [...new Set(allTools)]
                const totalResults = toolCalls.reduce((n, tc) => n + (tc.toolResult?.results || 0), 0)
                const label = `${allTools.length} tool call${allTools.length !== 1 ? 's' : ''}: ${unique.join(', ')} · ${totalResults} result${totalResults !== 1 ? 's' : ''}`
                // Collect prose that precedes tools within each assistantMsg entry.
                // This is the reasoning/narration the model emits before calling a tool —
                // it lives in assistantMsg.text and was never rendered before this fix.
                const leadingProse = toolCalls
                  .map(tc => tc.assistantMsg.text)
                  .filter(Boolean)
                return (
                  <div key={block.key} className="chat-row chat-row-assistant">
                    {leadingProse.map((prose, idx) => (
                      <div key={idx} className="chat-assistant-text">
                        <Markdown text={prose.slice(0, 16000)} />
                        {toolCalls[idx]?.assistantMsg?.truncated && (
                          <span className="chat-truncated-marker">…truncated</span>
                        )}
                      </div>
                    ))}
                    <details className="chat-tools">
                      <summary className="chat-tools-summary">▶ {label}</summary>
                      <div className="chat-tools-list">
                        {toolCalls.map((tc, idx) => (
                          <span key={idx} className="chat-tool-chip">
                            ⚙ {tc.assistantMsg.tools?.map(t => t.name || t).join(', ') || '?'}
                          </span>
                        ))}
                      </div>
                    </details>
                    {finalMsg?.text && (
                      <div className="chat-assistant-text">
                        <Markdown text={finalMsg.text.slice(0, 16000)} />
                        {finalMsg.truncated && (
                          <span className="chat-truncated-marker">…truncated</span>
                        )}
                        <CopyButton text={finalMsg.text} />
                      </div>
                    )}
                  </div>
                )
              }

              if (block.type === 'assistant') {
                const msg = block.msg
                return (
                  <div key={block.key} className="chat-row chat-row-assistant">
                    {msg.text && (
                      <div className="chat-assistant-text">
                        <Markdown text={msg.text.slice(0, 16000)} />
                        {msg.truncated && (
                          <span className="chat-truncated-marker">…truncated</span>
                        )}
                        <CopyButton text={msg.text} />
                      </div>
                    )}
                  </div>
                )
              }
              return null
            })
          })()}
          {isWorking && (
            <div className="chat-row chat-row-assistant">
              <div className="chat-assistant-thinking">
                <span className="chat-spinner">●</span> working…
              </div>
            </div>
          )}
          {/* Pending bubble: show sent text immediately before server confirms it */}
          {echo && (() => {
            const head = echo.trim().slice(0, 40)
            const alreadyShown = messages?.some(m =>
              (m.role === 'user' || m.type === 'user') &&
              (m.text || m.content?.[0]?.text || '').startsWith(head)
            )
            if (alreadyShown) return null
            return (
              <div className="chat-row chat-row-user chat-row-pending">
                <div className="chat-bubble-user">
                  <div className="chat-bubble-text">{echo}</div>
                </div>
              </div>
            )
          })()}
          {!atTranscriptBottom && (
            <button className="live-scroll-btn transcript-scroll-btn" onClick={scrollTranscriptToBottom} title="Scroll to latest">↓</button>
          )}
          </TranscriptErrorBoundary>
        </div>
      )}
      {/* Composer lives with the output it belongs to, so reading and replying
          happen in one place. */}
      {/* ── Shell view — full height, replaces transcript/live when active ── */}
      {effectiveView === 'shell' && (
        <div className="detail-shell-full">
          {/* Shell tabs strip — only shells in the same folder */}
          {(() => {
            const sessionCwd = session?.cwd || ''
            const sameFolder = shells.filter(sh =>
              !sessionCwd || sh.cwd === sessionCwd ||
              sh.cwd.startsWith(sessionCwd + '/') ||
              sessionCwd.startsWith(sh.cwd + '/')
            )
            const folderName = sessionCwd.replace(/.*\//, '') || sessionCwd
            return (
              <div className="detail-shell-strip">
                {sameFolder.map(sh => {
                  const label = sh.cwd.replace(/.*\//, '') || sh.cwd
                  return (
                    <button
                      key={sh.shell_id}
                      className={`detail-shell-tab${activeShellId === sh.shell_id ? ' active' : ''}${!sh.alive ? ' dead' : ''}`}
                      onClick={() => setActiveShellId(activeShellId === sh.shell_id ? null : sh.shell_id)}
                      title={sh.cwd}>
                      <span className="detail-shell-tab-dot">{sh.alive ? '🟢' : '⚪'}</span>
                      <span className="detail-shell-tab-label">{label}</span>
                      <span className="detail-shell-tab-close"
                            onClick={e => { e.stopPropagation(); closeShellTab(sh.shell_id) }}
                            title="Close this shell">×</span>
                    </button>
                  )
                })}
                <button className="detail-shell-add-btn"
                        disabled={shellBusy}
                        onClick={() => openShellForCwd(sessionCwd)}
                        title={`New shell in ${folderName || '~'}`}>
                  + New
                </button>
              </div>
            )
          })()}
          {/* Shell pane content */}
          {!activeShellId ? (
            <div className="detail-shell-empty">
              <button className="detail-shell-open-btn"
                      disabled={shellBusy}
                      onClick={() => openShellForCwd(session?.cwd)}>
                Open shell{session?.cwd ? ` in ${session.cwd.replace(/.*\//, '')}` : ''}
              </button>
            </div>
          ) : (
            <div className={`detail-shell-view terminal terminal-live pane-${paneTheme}`} style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
              {!shellSt?.alive && !shellSt?.exists && (
                <div className="detail-shell-open-row">
                  <span className="detail-shell-hint">Shell not found.</span>
                  <button className="detail-shell-open-btn" disabled={shellBusy}
                          onClick={() => openShellForCwd(session?.cwd)}>Restart</button>
                </div>
              )}
              {!shellSt?.alive && shellSt?.exists && (
                <div className="detail-shell-open-row">
                  <span className="detail-shell-hint">⚠ Shell exited.</span>
                  <button className="detail-shell-open-btn" disabled={shellBusy}
                          onClick={() => openShellForCwd(session?.cwd)}>Restart</button>
                </div>
              )}
              {isMobileBrowser ? (
                <div className="shell-mobile-unavailable">
                  Terminal shell is not available in mobile browsers.
                </div>
              ) : (
                <XtermPane
                  shellId={activeShellId}
                  cwd={session?.cwd || '~'}
                  active={effectiveView === 'shell'}
                  localToken={window._qdLocalToken || ''}
                  onReady={() => { setShellSt(st => st ? { ...st, alive: true } : st) }}
                  onDead={() => { setShellSt(st => st ? { ...st, alive: false } : st) }}
                  cmdToSend={shellCmd || null}
                  onCmdSent={() => setShellCmd('')}
                />
              )}
              <ShellInputBar
                disabled={!shellSt?.alive}
                onSend={(text) => setShellCmd(text)}
                onKey={(seq) => setShellCmd(seq)}
              />
            </div>
          )}
        </div>
      )}
      <div className="detail-footer">
      <div className="composer">
        {canSend && effectiveView !== 'shell' ? (
          <>
            {chipsOpen && (
              <div className="composer-commands-wrap">
                <button className="composer-commands-arrow composer-commands-arrow-left"
                        onClick={() => { const el = document.querySelector('.composer-commands'); if (el) el.scrollBy({ left: -120, behavior: 'smooth' }) }}
                        tabIndex={-1} aria-hidden="true">‹</button>
                <div className="composer-commands">
                <button className="composer-chip composer-key" title="Send Escape — cancels the current turn or closes a menu"
                        onClick={() => respond('Escape')}>esc</button>
                <button className="composer-chip composer-key" title="Send Ctrl+C — interrupt"
                        onClick={() => respond('C-c')}>ctrl-c</button>
                <button className="composer-chip composer-key" title="Send Ctrl+X — kiro-cli shortcut"
                        onClick={() => respond('C-x')}>ctrl-x</button>
                <button className="composer-chip composer-key" title="Send Delete key"
                        onClick={() => respond('DC')}>del</button>
                <button className="composer-chip composer-key" title="Previous message (history ↑)"
                        onClick={() => recallByButton(1)}>↑</button>
                <button className="composer-chip composer-key" title="Next message (history ↓)"
                        onClick={() => recallByButton(-1)}>↓</button>
                <button className="composer-chip composer-key" title="Send Enter on its own"
                        onClick={() => respond('Enter')}>↵</button>
                {(options.commands || []).map(c => (
                  <button key={c.cmd} className="composer-chip" title={c.hint} onClick={() => runCommand(c)}>
                    {c.label}{c.needs_arg ? '…' : ''}
                  </button>
                ))}
                <select
                  className="composer-select"
                  value={session?.model || ""}
                  onChange={(e) => changeModel(e.target.value)}
                  title={session?.model ? `Active model: ${session.model} — switch for this session` : "Switch model for this session"}
                >
                  {!session?.model && <option value="">model…</option>}
                  {(options.models || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
                <select
                  className="composer-select"
                  value={session?.effort || ""}
                  onChange={(e) => changeEffort(e.target.value)}
                  title={session?.effort ? `Active effort: ${session.effort} — switch for this session` : "Switch thinking effort"}
                >
                  {!session?.effort && <option value="">effort…</option>}
                  {(options.efforts || []).map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              </div>
                <button className="composer-commands-arrow composer-commands-arrow-right"
                        onClick={() => { const el = document.querySelector('.composer-commands'); if (el) el.scrollBy({ left: 120, behavior: 'smooth' }) }}
                        tabIndex={-1} aria-hidden="true">›</button>
              </div>
            )}
            {pendingScreenshots.length > 0 && (
              <div className="screenshot-pending-bar">
                {pendingScreenshots.slice(-5).map((s) => (
                  <button key={s.name} className="screenshot-pending-chip"
                          type="button"
                          title={s.path}
                          onMouseEnter={e => setChipPreview({ url: `/api/screenshots/file?path=${encodeURIComponent(s.path)}`, x: e.clientX, y: e.clientY })}
                          onMouseMove={e => setChipPreview(p => p ? { ...p, x: e.clientX, y: e.clientY } : p)}
                          onMouseLeave={() => setChipPreview(null)}
                          onClick={() => {
                            const newVal = (draftRef.current?.value || draft) ? `${draftRef.current?.value || draft}\n${s.path}` : s.path
                            setDraft(newVal)
                            if (draftRef.current) draftRef.current.value = newVal
                            dismissedScreenshots.current.add(s.name)
                            setPendingScreenshots(prev => prev.filter(x => x.name !== s.name))
                            setChipPreview(null)
                            draftRef.current?.focus()
                          }}>
                    📎 {s.name}
                  </button>
                ))}
                <button className="screenshot-pending-dismiss"
                        type="button"
                        onClick={() => {
                          pendingScreenshots.forEach(s => dismissedScreenshots.current.add(s.name))
                          setPendingScreenshots([])
                        }}>✕</button>
              </div>
            )}
            {chipPreview && createPortal(
              <div className="wall-tile-preview" style={{
                position: 'fixed',
                left: Math.min(chipPreview.x + 12, window.innerWidth - 280),
                top: Math.max(chipPreview.y - 200, 8),
                pointerEvents: 'none',
                zIndex: 9999,
              }}>
                {chipPreviewBlob
                  ? <img src={chipPreviewBlob} alt="screenshot preview" />
                  : chipPreviewError
                    ? <div className="chip-preview-loading">⚠ {chipPreviewError}</div>
                    : <div className="chip-preview-loading">Loading…</div>}
              </div>,
              document.body
            )}
            {starterChips.length > 0 && (
              <div className="starter-chips">
                {starterChips.map((c, i) => (
                  <button key={i} type="button" className="starter-chip"
                    title={c.prompt}
                    onClick={() => {
                      if (c.mode === 'send') {
                        if (draftRef.current) draftRef.current.value = c.prompt
                        setDraft(c.prompt)
                        setTimeout(() => submitDraft(null), 0)
                      } else {
                        const cur = draftRef.current?.value || draft
                        const next = cur ? `${cur}\n${c.prompt}` : c.prompt
                        if (draftRef.current) draftRef.current.value = next
                        setDraft(next)
                        draftRef.current?.focus()
                      }
                    }}>
                    {c.label}
                  </button>
                ))}
              </div>
            )}
            <form className="composer-row" onSubmit={submitDraft}>
              {attachments.length > 0 && (
                <PasteAttachments attachments={attachments} onRemove={removeAttachment} />
              )}
              <button type="button" className="composer-chips-toggle"
                      title={chipsOpen ? 'Hide controls' : 'Show controls (keys, model, effort)'}
                      onClick={() => setChipsOpen(v => { const next = !v; localStorage.setItem('detail-chips-open', next ? '1' : '0'); settingsApi.saveSettings({ 'detail-chips-open': next }).catch(() => {}); return next })}>
                {chipsOpen ? '⌄' : '⌃'}
              </button>
              <textarea
                ref={draftRef}
                className="composer-input"
                rows={expanded ? 3 : 2}
                defaultValue={draft}
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="off"
                onInput={(e) => {
                  // Only sync state when empty/non-empty flips — avoids re-rendering
                  // DetailPanel on every keystroke (the controlled-input lag).
                  const empty = !e.target.value.trim()
                  const wasEmpty = !draft.trim()
                  if (empty !== wasEmpty) setDraft(e.target.value)
                }}
                onPaste={onPasteAttachment}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) submitDraft(e)
                  if (e.key === 'ArrowUp') recallHistory(e, 1)
                  if (e.key === 'ArrowDown') recallHistory(e, -1)
                  if (e.key === 'Escape') {
                    e.preventDefault()
                    if (draftRef.current?.value) {
                      draftRef.current.value = ''
                      setDraft('')
                    } else respond('Escape')
                  }
                  if (e.key === 'x' && e.ctrlKey) {
                    e.preventDefault()
                    respond('C-x')
                  }
                }}
                placeholder="Reply to this session…  (Enter to send, Shift+Enter for a new line)"
              />
              <button className="dispatch-btn" type="submit" disabled={(!draft.trim() && attachments.filter(a => !a.uploading).length === 0) || sending}>
                {sending ? '…' : cliSendMode && cliStatus?.bound ? '↗ CLI' : '↗ Send'}
              </button>
              {/* CLI send mode toggle — only for foreign sessions with a bound CLI */}
              {control === 'foreign' && cliStatus?.bound && cliStatus.status !== 'unbound' && (
                <button
                  type="button"
                  className={`detail-cli-mode-btn${cliSendMode ? ' active' : ''}`}
                  title={cliSendMode
                    ? `Sending to CLI (${cliStatus.tmux_session}) — click to send to session instead`
                    : `Click to send to CLI (${cliStatus.tmux_session}) instead of this session`}
                  onClick={() => setCLISendMode(v => !v)}>
                  {cliStatus.status === 'idle' ? '🟢' : cliStatus.status === 'thinking' ? '🟡' : '⚪'} CLI
                </button>
              )}

              <button className="queue-btn" type="button"
                      title="Add to task queue instead of sending now"
                      disabled={!draft.trim() && attachments.filter(a => !a.uploading).length === 0}
                      onClick={() => {
                        const text = draftRef.current ? draftRef.current.value : draft
                        const readyAtts = attachments.filter(a => !a.uploading)
                        if (!text.trim() && readyAtts.length === 0) return
                        api.addStackItem(session.id, text, readyAtts).then(d => {
                          if (d.ok) {
                            setStack(d.items)
                            setDraft('')
                            if (draftRef.current) draftRef.current.value = ''
                            clearAttachments()
                          }
                        }).catch(() => {})
                      }}>
                + Queue
              </button>
            </form>
            <TaskStack sessionId={session.id} stack={stack} setStack={setStack} canSend={canSend} />
            {/* Slash command queue — items display only, input removed */}
            <div className="slash-queue">
              {slashQueue.length > 0 && (
                <ul className="slash-queue-list">
                  {slashQueue.map((item, i) => (
                    <li key={item.id} className="slash-queue-item">
                      <span className="slash-queue-index">{i + 1}</span>
                      <span className="slash-queue-text">{item.text}</span>
                      <button className="slash-queue-cancel"
                        title="Remove from queue"
                        onClick={() => {
                          api.deleteSlashQueueItem(session.id, item.id)
                            .then(d => setSlashQueue(d.queue || []))
                            .catch(() => {})
                        }}>×</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        ) : (
          <div className="composer-locked">
            {control === 'foreign'
              ? 'Started outside the app — take it over to send input.'
              : 'Not running — resume it to send input.'}
          </div>
        )}
        {/* Always show queues if items exist, even in read-only / transcript view */}
        {!canSend && stack.length > 0 && (
          <TaskStack sessionId={session.id} stack={stack} setStack={setStack} canSend={false} />
        )}
        {!canSend && slashQueue.length > 0 && (
          <ul className="slash-queue-list slash-queue-readonly">
            {slashQueue.map((item, i) => (
              <li key={item.id} className="slash-queue-item">
                <span className="slash-queue-index">{i + 1}</span>
                <span className="slash-queue-text">{item.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="detail-meta" onClick={() => {
        // The raw path, never the display form — the endpoint opens what it is given.
        const cwd = detail?.cwd || session.cwd
        if (!cwd) return
        settingsApi.openFolder(cwd)
          .then(d => { const e = errorOf(d); if (e) notify(`Could not open folder: ${e}`, 'error') })
          .catch(() => notify('Could not open folder', 'error'))
      }} title="Open in Finder">{showPath(detail) || showPath(session)}</div>
      {/* Steering delivery: which rules are configured for this session */}
      {delivery && delivery.expected_count > 0 && (
        <details className="detail-delivery">
          <summary className="detail-delivery-summary">
            <span className="detail-delivery-label">
              ◈ {delivery.expected_count} rule{delivery.expected_count !== 1 ? 's' : ''} configured
            </span>
            {delivery.probes_run > 0 && (
              <span className="detail-delivery-probes" title={`${delivery.probes_run} probe echo tests recorded`}>
                {delivery.probe_results.filter(p => p.delivered).length}/{delivery.probes_run} confirmed
              </span>
            )}
          </summary>
          <ul className="detail-delivery-files">
            {delivery.expected_files.map(f => {
              const probe = delivery.probe_results.find(p => p.token && f.includes(p.token.split('_')[0]))
              return (
                <li key={f} className={`detail-delivery-file ${probe ? (probe.delivered ? 'delivered' : 'not-delivered') : ''}`}>
                  {probe?.delivered ? '✓' : probe ? '✗' : '·'} {f}
                </li>
              )
            })}
          </ul>
          {delivery.notes.map((n, i) => (
            <div key={i} className="detail-delivery-note">{n}</div>
          ))}
        </details>
      )}
      {delivery && delivery.expected_count === 0 && (
        <div className="detail-delivery-empty" title="No always-mode steering files found for this session">
          ⚠ no steering rules configured
        </div>
      )}
      {/* Which agent this session runs as. kiro-cli does not record it, so this
          is only known for sessions Quarterdeck started. */}
      {(detail?.agent || session.agent) && (
        <div className="detail-meta detail-agent"
             title="Agent this session was started with">
          ⌥ {detail?.agent || session.agent}
        </div>
      )}
      {/* Profile · duration · delete — one compact line on both desktop and mobile */}
      <div className="detail-meta-strip">
        {(detail?.kiro_profile || session.kiro_profile) && (
          <span className="detail-meta-strip-profile" title="Kiro profile used for this session">
            ◉ {detail?.kiro_profile || session.kiro_profile}
          </span>
        )}
        {durationRecord?.outcome?.wall_clock_min != null && (
          <span className="detail-meta-strip-duration"
                title={`Type: ${durationRecord.features?.type_tag || 'unknown'} · Project: ${durationRecord.features?.project || ''}`}>
            ⏱ {Math.round(durationRecord.outcome.wall_clock_min)} min
            {durationRecord.outcome.tool_calls_total > 0 && (
              <> · {durationRecord.outcome.tool_calls_total} calls</>
            )}
          </span>
        )}
        {(status === 'done' || control === 'archived' || status === 'idle' || status === 'error') && (
          <button className="detail-delete-btn" onClick={async () => {
            const title = detail?.title || session.title || 'this session'
            const isRunning = status === 'idle' && control !== 'archived'
            const ok = await askConfirm(
              `Delete "${title}"?`,
              isRunning
                ? 'This session is idle but may still have a running process. It will be removed anyway.'
                : 'The conversation history and all session files will be permanently removed.',
              'Delete'
            )
            if (!ok) return
            api.deleteSession(session.id)
              .then(d => {
                if (d && d.error) { notify(d.error, 'error'); return }
                onClose()
                if (onRefresh) onRefresh()
              })
              .catch(() => notify('Could not delete session', 'error'))
          }}>
            Delete session
          </button>
        )}
      </div>
      </div>{/* detail-footer */}
    </div>
    {secretsModalOpen && createPortal(
      <SecretsPanel cwd={session.cwd} onClose={() => setSecretsModalOpen(false)} modal />,
      document.body
    )}
    {chipsModalOpen && session.cwd && createPortal(
      <ChipsPanel cwd={session.cwd} onClose={() => setChipsModalOpen(false)} />,
      document.body
    )}
    {satModal && (
      <SaveAsTemplateModal
        session={session}
        afterSeq={satModal.afterSeq}
        onClose={() => setSatModal(null)}
        notify={notify}
      />
    )}
    </>
  )
}


export default DetailPanel
