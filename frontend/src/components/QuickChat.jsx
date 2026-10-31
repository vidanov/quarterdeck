import React, { useEffect, useRef, useState } from 'react'
import * as quickApi from '../api/quickChat'

// Marks the end of the hidden opening prompt in the pane; everything up to it
// (and the model's "ready") is plumbing, not conversation.
const INTRO_END = 'Reply with just: ready'
const PROMPT = /ask a question or describe a task/i

// kiro-cli's TUI chrome: rules, the model/context status line, the prompt
// placeholder and key hints. None of it is conversation.
const CHROME = [
  /^[\s─━═_\-–—]+$/,
  PROMPT,
  /^\/copy\b/i,
  /^~\s*$/,
  / · .*◔\s*\d+%/,          // "cmux · claude-haiku-4.5 · ◔ 24%      ~"
  /^\s*[•●]?\s*ready\.?\s*$/i,
]

// Turn the pane into turns: an indented line is what you typed, a bulleted
// one starts an answer, anything else continues the line before it.
function toTurns(lines) {
  const turns = []
  for (const raw of lines) {
    if (CHROME.some(re => re.test(raw.trim()))) continue
    const answer = raw.match(/^\s*[•●]\s?(.*)$/)
    const asked = !answer && /^\s{2,}\S/.test(raw) && (!turns.length || turns[turns.length - 1].who === 'ai')
    if (answer) turns.push({ who: 'ai', text: answer[1] })
    else if (asked) turns.push({ who: 'you', text: raw.trim() })
    else if (turns.length) turns[turns.length - 1].text += '\n' + raw
    else turns.push({ who: 'ai', text: raw })
  }
  return turns
}

/**
 * QuickChat — a docked assistant for short questions, toggled with ⌘J.
 *
 * Always on: hiding the dock keeps the kiro-cli process and its history, so
 * the next ⌘J picks up where it left off. "New" starts a fresh conversation.
 */
export default function QuickChat({ open, onClose, notify }) {
  const [lines, setLines] = useState([])
  const [thinking, setThinking] = useState(false)
  const [starting, setStarting] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)
  const bottomRef = useRef(null)
  const started = useRef(false)

  const start = () => {
    setStarting(true)
    return quickApi.open()
      .then(d => {
        if (d.error) { notify(`Quick chat: ${d.error}`, 'error'); return }
        started.current = true
      })
      .catch(() => notify('Quick chat unavailable', 'error'))
      .finally(() => setStarting(false))
  }

  useEffect(() => {
    if (!open) return
    setTimeout(() => inputRef.current?.focus(), 30)
    if (!started.current) start()
    const tick = () => quickApi.poll().then(d => {
      if (!d.alive) { started.current = false; return }
      const all = d.lines || []
      // Ready when the prompt is back near the bottom; the TUI prints a few
      // hint lines under it, so the backend's 5-line window misses it.
      setThinking(!all.slice(-10).some(l => PROMPT.test(l)))
      const cut = all.findLastIndex(l => l.includes(INTRO_END))
      setLines(toTurns(cut >= 0 ? all.slice(cut + 1) : all))
    }).catch(() => {})
    tick()
    const t = setInterval(tick, 1200)
    return () => clearInterval(t)
  }, [open])

  useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }) }, [lines, open])

  const submit = (e) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text) return
    setDraft('')
    setThinking(true)
    quickApi.send(text)
      .then(d => { if (d.error) { notify(`Quick chat: ${d.error}`, 'error'); setThinking(false) } else started.current = true })
      .catch(() => { notify('Quick chat unavailable', 'error'); setThinking(false) })
  }

  const fresh = () => {
    setLines([])
    quickApi.close().catch(() => {}).finally(() => { started.current = false; start() })
    inputRef.current?.focus()
  }

  if (!open) return null
  return (
    <div className="quick-chat" role="dialog" aria-label="Quick chat">
      <div className="quick-chat-header">
        <span className="quick-chat-title">Quick chat</span>
        <span className="quick-chat-hint">⌘J</span>
        <span className="quick-chat-spacer" />
        <button className="wc" onClick={fresh} title="New conversation" aria-label="New conversation">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path d="M8 3.5v9M3.5 8h9" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
        </button>
        <button className="wc" onClick={onClose} title="Hide (⌘J) — keeps the conversation" aria-label="Hide quick chat">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path d="M3.5 12h9" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="quick-chat-body">
        {lines.length === 0 && (
          <div className="quick-chat-empty">
            {starting ? 'Starting…' : 'Ask anything — short answers, kept between ⌘J.'}
          </div>
        )}
        {lines.map((t, i) => (
          <div key={i} className={`quick-chat-turn quick-chat-${t.who}`}>{t.text}</div>
        ))}
        {thinking && lines.length > 0 && <div className="quick-chat-thinking">◔ thinking…</div>}
        <div ref={bottomRef} />
      </div>
      <form className="quick-chat-compose" onSubmit={submit}>
        <input ref={inputRef} className="quick-chat-input" value={draft}
               placeholder={thinking ? 'Thinking… esc to stop' : 'Ask a quick question…'}
               spellCheck={false}
               onChange={e => setDraft(e.target.value)}
               onKeyDown={e => {
                 if (e.key !== 'Escape') return
                 e.preventDefault()
                 e.stopPropagation()
                 if (draft) setDraft('')
                 else if (thinking) quickApi.key('Escape').catch(() => {})
                 else onClose()
               }} />
        <button type="submit" className="quick-chat-send" disabled={!draft.trim()}>↗</button>
      </form>
    </div>
  )
}
