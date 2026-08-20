/**
 * ShellInputBar — isolated command input for the shell view.
 *
 * Keeping this in its own component means keystrokes only re-render this
 * tiny component, NOT the 2570-line DetailPanel that contains xterm.js.
 *
 * Props:
 *   onSend(text)   — called with trimmed command text when user submits
 *   onKey(seq)     — called with a PTY escape sequence when a chip is clicked
 *   disabled       — disable all controls
 */
import React, { useState, useCallback } from 'react'

const CHIPS = [
  ['Tab',  '\t'],
  ['↑',    '\x1b[A'],
  ['↓',    '\x1b[B'],
  ['^C',   '\x03'],
  ['^D',   '\x04'],
  ['^L',   '\x0c'],
]

export default React.memo(function ShellInputBar({ onSend, onKey, disabled }) {
  const [cmd, setCmd] = useState('')

  const handleSubmit = useCallback((e) => {
    e.preventDefault()
    const text = cmd.trim()
    if (!text) return
    setCmd('')
    if (onSend) onSend(text)
  }, [cmd, onSend])

  return (
    <div className="detail-shell-input-row">
      <div className="detail-shell-keys">
        {CHIPS.map(([label, seq]) => (
          <button
            key={label}
            className="composer-chip composer-key"
            disabled={disabled}
            onClick={() => onKey && onKey(seq)}
          >
            {label}
          </button>
        ))}
      </div>
      <form className="detail-shell-form" onSubmit={handleSubmit}>
        <input
          className="detail-shell-cmd"
          value={cmd}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          placeholder="Type a command…"
          onChange={e => setCmd(e.target.value)}
        />
        <button
          className="dispatch-btn"
          type="submit"
          disabled={disabled || !cmd.trim()}
        >↩</button>
      </form>
    </div>
  )
})
