import React, { useState, useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { usePasteAttachments } from '../hooks/usePasteAttachments'
import { PasteAttachments } from './PasteAttachments'

/**
 * SideChat — ask questions about the session without polluting its context.
 *
 * Props:
 *   sessionId  — kiro session uuid
 *   notify     — (msg, level) toast function from parent
 *   respond    — (choice) send a key/command to the main session
 *   runCommand — (cmd) run a slash command on the main session
 *   options    — session options (commands list etc.)
 *
 * Ref methods (exposed via forwardRef):
 *   open()     — open (or toggle closed) the panel
 *   isOpen     — boolean (read via ref.current.isOpen)
 *   isOpening  — boolean
 */
const SideChat = forwardRef(function SideChat({ sessionId, notify, respond, runCommand, options }, ref) {
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
    setThinking(true)
    fetch(`/api/sessions/${sessionId}/side-chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).catch(() => {})
  }

  // The toggle button — rendered inline where the caller wants it
  const toggleButton = (
    <button
      className={`detail-switch detail-side-btn${open ? ' active' : ''}`}
      title={open ? 'Close side chat' : 'Open side chat — ask questions about this session without polluting its context'}
      onClick={handleToggle}
      disabled={opening}
    >
      {opening ? '…' : '◎ Side'}
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
          {chipsOpen && (
            <div className="side-chat-chips">
              <button type="button" className="composer-chip composer-key"
                title="Send Escape"
                onClick={() => { respond('Escape'); setChipsOpen(false) }}>esc</button>
              <button type="button" className="composer-chip composer-key"
                title="Send Ctrl+C"
                onClick={() => { respond('C-c'); setChipsOpen(false) }}>ctrl-c</button>
              <button type="button" className="composer-chip composer-key"
                title="Send Enter"
                onClick={() => { respond('Enter'); setChipsOpen(false) }}>↵</button>
              {(options?.commands || []).map(c => (
                <button key={c.cmd} type="button" className="composer-chip"
                  title={c.hint}
                  onClick={() => { runCommand(c); setChipsOpen(false) }}>
                  {c.label}{c.needs_arg ? '…' : ''}
                </button>
              ))}
            </div>
          )}
          <form className="side-chat-compose" onSubmit={handleSend}>
            {attachments.length > 0 && (
              <PasteAttachments attachments={attachments} onRemove={removeAttachment} />
            )}
            <button type="button"
              className={`side-chat-chips-toggle${chipsOpen ? ' active' : ''}`}
              title={chipsOpen ? 'Hide controls' : 'Show controls & commands'}
              onClick={() => setChipsOpen(v => !v)}>⌃</button>
            <input
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
        </div>
      </div>
    </>
  )
})

export default SideChat
