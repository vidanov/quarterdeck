/**
 * DocCard — a collapsible <details> card for pasted document segments in the
 * transcript. Follows the chat-steering pattern from DetailPanel.jsx.
 *
 * For 'ref' segments: fetches full text from /api/pastes/… on first expand.
 * For 'heuristic' segments: uses inline content directly.
 */
import { useState } from 'react'
import { getPasteText } from '../api/sessions'

export function DocCard({ segment }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(null)
  const [loading, setLoading] = useState(false)
  const [gone, setGone] = useState(false)

  const { type, source } = segment

  const handleToggle = async (e) => {
    const isOpen = e.target.open
    setOpen(isOpen)
    if (isOpen && text === null && source === 'ref') {
      setLoading(true)
      try {
        const d = await getPasteText(segment.session_id, segment.name)
        setText(d.text ?? '')
      } catch {
        setGone(true)
        setText(null)
      }
      setLoading(false)
    }
  }

  const lines = segment.lines ?? (segment.content ?? '').split('\n').length
  const size = segment.size ?? ''
  const summary = `📄 Pasted document · ${lines} lines${size ? ' · ' + size : ''}`

  const body = source === 'ref'
    ? (gone ? '(file has been swept — content no longer available)' : (loading ? 'Loading…' : (text ?? '')))
    : (segment.content ?? '')

  return (
    <details className="chat-doc-card" onToggle={handleToggle}>
      <summary className="chat-doc-summary">{summary}</summary>
      <pre className="chat-doc-body">{body}</pre>
    </details>
  )
}
