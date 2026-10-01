import React, { useState, useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { usePasteAttachments } from '../hooks/usePasteAttachments'
import { PasteAttachments } from './PasteAttachments'

/**
 * SideChat — ask questions about the session without polluting its context.
 *
 * Props:
 *   sessionId  — kiro session uuid
 *   notify     — (msg, level) toast function from parent
 *   options    — session options (commands, models, efforts)
 *   chips      — helper chips (same list as the main composer)
 *
 * Ref methods (exposed via forwardRef):
 *   open()     — open (or toggle closed) the panel
 *   isOpen     — boolean (read via ref.current.isOpen)
 *   isOpening  — boolean
 */
const SideChat = forwardRef(function SideChat({ sessionId, notify, options, chips = [] }, ref) {
  const [open, setOpen] = useState(false)
  const [opening, setOpening] = useState(false)
  const [lines, setLines] = useState([])
  const [thinking, setThinking] = useState(false)
  const [draft, setDraft] = useState('')
  const [chipsOpen, setChipsOpen] = useState(false)
  const [history, setHistory] = useState(() => {
    try {
      const r = JSON.parse(localStorage.getItem(`side-chat-history:${sessionId}`) || '[]')
      return Array.isArray(r) ? r : []
    } catch { return [] }
  })
  const [historyIdx, setHistoryIdx] = useState(-1)

  const pollRef = useRef(null)
  const inputRef = useRef(null)
  const bottomRef = useRef(null)

  const {
    attachments,
    onPaste,
    removeAttachment,
    clearAttachments,
  } = usePasteAttachments({ sessionId })

  // Expose open/isOpen/isOpening/close to parent via ref
  useImperativeHandle(ref, () => ({
    get isOpen() { return open },
    get isOpening() { return opening },
    toggle() { handleToggle() },
    close() { if (open) handleClose() },
  }))

  // Poll loop
  useEffect(() => {
    if (!open) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    const poll = () => {
      fetch(`/api/sessions/${sessionId}/side-chat/poll`)
        .then(r => r.json())
        .then(d => {
          if (!d.alive) return
          setThinking(!!d.thinking)
          if (d.lines && d.lines.length) setLines(d.lines)
        })
        .catch(() => {})
    }
    poll()
    pollRef.current = setInterval(poll, 1500)
    return () => { clearInterval(pollRef.current); pollRef.current = null }
  }, [open, sessionId])

  // Auto-scroll to bottom
  useEffect(() => {
    if (bottomRef.current) bottomRef.current.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  const handleToggle = () => {
    if (open) { handleClose(); return }
    setOpening(true)
    fetch(`/api/sessions/${sessionId}/side-chat/open`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.error) { notify(`Side chat: ${d.error}`, 'error'); return }
        setOpen(true)
        setLines([])
      })
      .catch(() => notify('Side chat unavailable', 'error'))
      .finally(() => setOpening(false))
  }

  const handleClose = () => {
    setOpen(false)
    fetch(`/api/sessions/${sessionId}/side-chat/close`, { method: 'POST' }).catch(() => {})
  }

  // Everything the toolbar does goes to the side chat's own kiro-cli, never to
  // the main session — the point of a side chat is to leave that one alone.
  const post = (path, body) => fetch(`/api/sessions/${sessionId}/side-chat/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()).then(d => { if (d.error) notify(`Side chat: ${d.error}`, 'error') })
    .catch(() => notify('Side chat unavailable', 'error'))
  const sendKey = (key) => post('key', { key })
  const sendLine = (text) => { setThinking(true); post('send', { text }) }
  const putDraft = (text) => { setDraft(text); setTimeout(() => inputRef.current?.focus(), 0) }
  const runSideCommand = (c) => c.needs_arg ? putDraft(c.cmd) : sendLine(c.cmd)
  const applyChip = (c) => {
    if (c.mode === 'send') sendLine(c.prompt)
    else putDraft(draft ? `${draft} ${c.prompt}` : c.prompt)
  }

  const handleSend = (e) => {
    e.preventDefault()
    const typedText = draft.trim()
    const readyAtts = attachments.filter(a => !a.uploading)
    if (!typedText && readyAtts.length === 0) return
    setDraft('')
    clearAttachments()
    setHistoryIdx(-1)
    const parts = readyAtts.map(a => a.preview ?? '').filter(Boolean)
    if (typedText) parts.push(typedText)
    const text = parts.join('\n\n')
    if (!text.trim()) return
    const newHistory = [text, ...history.filter(h => h !== text)].slice(0, 50)
    setHistory(newHistory)
    try { localStorage.setItem(`side-chat-history:${sessionId}`, JSON.stringify(newHistory)) } catch {}
    sendLine(text)
  }

  // The toggle button — rendered inline where the caller wants it
  const toggleButton = (
    <button
      className={`detail-icon detail-side-btn${open ? ' active' : ''}`}
      title={open ? 'Close side chat' : 'Open side chat — ask questions about this session without polluting its context'}
      onClick={handleToggle}
      disabled={opening}
    >
      {opening ? '…' : '◎'}
    </button>
  )

  if (!open) return toggleButton

  return (
    <>
      {toggleButton}
      <div className="side-chat-panel">
        <div className="side-chat-header">
          <span className="side-chat-title">
            ◎ Side Chat{' '}
            <span className="side-chat-subtitle">— ask questions without touching the session</span>
          </span>
          <div className="side-chat-header-actions">
            <button
              className="side-chat-fork"
              title="Fork to standalone session — dispatches a new session with this conversation as context"
              onClick={() => {
                fetch(`/api/sessions/${sessionId}/side-chat/fork`, { method: 'POST' })
                  .then(r => r.json())
                  .then(d => {
                    if (d.error) { notify(`Fork failed: ${d.error}`, 'error'); return }
                    setOpen(false)
                    notify('Forked to new session')
                  })
                  .catch(() => notify('Fork failed', 'error'))
              }}
            >
              ⑂ Fork
            </button>
            <button className="side-chat-close" onClick={handleClose} title="Close side chat">✕</button>
          </div>
        </div>

        <div className="side-chat-body">
          {lines.length === 0 && !thinking && (
            <div className="side-chat-empty">Starting up — context is being injected…</div>
          )}
          {lines.map((line, i) => (
            <div key={i} className="side-chat-line">{line}</div>
          ))}
          {thinking && <div className="side-chat-thinking">◔ thinking…</div>}
          <div ref={bottomRef} />
        </div>

        <div className="side-chat-compose-wrap">
          <form className="side-chat-compose" onSubmit={handleSend}>
            {attachments.length > 0 && (
              <PasteAttachments attachments={attachments} onRemove={removeAttachment} />
            )}
            <button type="button"
              className={`side-chat-chips-toggle${chipsOpen ? ' active' : ''}`}
              title={chipsOpen ? 'Hide controls' : 'Show controls & commands'}
              onClick={() => setChipsOpen(v => !v)}>{chipsOpen ? '⌃' : '⌄'}</button>
            <input
              ref={inputRef}
              className="side-chat-input"
              value={draft}
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
              onChange={e => { setDraft(e.target.value); setHistoryIdx(-1) }}
              onPaste={onPaste}
              onKeyDown={e => {
                if (e.key === 'ArrowUp') {
                  if (!history.length) return
                  const next = Math.min(historyIdx + 1, history.length - 1)
                  e.preventDefault()
                  setHistoryIdx(next)
                  setDraft(history[next])
                } else if (e.key === 'ArrowDown') {
                  if (historyIdx <= 0) { setHistoryIdx(-1); setDraft(''); return }
                  const next = historyIdx - 1
                  e.preventDefault()
                  setHistoryIdx(next)
                  setDraft(next === -1 ? '' : history[next])
                }
              }}
              placeholder="Ask a question about this session…"
              autoFocus
              disabled={thinking}
            />
            <button className="side-chat-send" type="submit"
              disabled={!draft.trim() || thinking}>↗</button>
          </form>
          {chipsOpen && (
            <div className="composer-tools side-chat-tools">
              <div className="composer-commands">
                {/* Keys stay usable while it is thinking: esc is how you stop it. */}
                {[['Escape', 'esc', 'Send Escape — cancel the turn or close a menu'],
                  ['C-c', 'ctrl-c', 'Send Ctrl+C — interrupt'],
                  ['C-x', 'ctrl-x', 'Send Ctrl+X'],
                  ['DC', 'del', 'Send Delete'],
                  ['Up', '↑', 'Send Up'],
                  ['Down', '↓', 'Send Down'],
                  ['Enter', '↵', 'Send Enter on its own']].map(([key, label, title]) => (
                  <button key={key} type="button" className="composer-chip composer-key"
                          title={title} onClick={() => sendKey(key)}>{label}</button>
                ))}
                {(options?.commands || []).map(c => (
                  <button key={c.cmd} type="button" className="composer-chip"
                          title={c.hint} disabled={thinking && !c.needs_arg}
                          onClick={() => runSideCommand(c)}>
                    {c.label}{c.needs_arg ? '…' : ''}
                  </button>
                ))}
                <select className="composer-select" value="" disabled={thinking}
                        onChange={e => e.target.value && sendLine(`/model ${e.target.value}`)}
                        title="Switch the side chat's model">
                  <option value="">model…</option>
                  {(options?.models || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
                <select className="composer-select" value="" disabled={thinking}
                        onChange={e => e.target.value && sendLine(`/effort ${e.target.value}`)}
                        title="Switch the side chat's thinking effort">
                  <option value="">effort…</option>
                  {(options?.efforts || []).map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              </div>
              {chips.length > 0 && (
                <div className="starter-chips">
                  {chips.map((c, i) => (
                    <button key={i} type="button" className="starter-chip" title={c.prompt}
                            disabled={thinking && c.mode === 'send'}
                            onClick={() => applyChip(c)}>{c.label}</button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
})

export default SideChat
