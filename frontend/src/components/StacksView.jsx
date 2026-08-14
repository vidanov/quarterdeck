import React, { useState, useEffect } from 'react'

function fmtCwd(cwd) {
  if (!cwd) return ''
  return cwd.replace(/^\/Users\/[^/]+/, '~')
}

function StatusDot({ status }) {
  const color = {
    thinking: '#f59e0b',
    running: '#f59e0b',
    'awaiting-approval': '#f97316',
    idle: '#94a3b8',
    done: '#94a3b8',
    error: '#ef4444',
    unknown: '#94a3b8',
  }[status] || '#94a3b8'
  return <span style={{ color, marginRight: 5, fontSize: 10 }}>●</span>
}

export default function StacksView({ onSelectSession }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () => {
    setLoading(true)
    fetch('/api/stacks')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }

  useEffect(() => { load() }, [])

  if (loading) return <div className="stacks-empty">Loading…</div>
  if (error) return <div className="stacks-empty stacks-error">Error: {error}</div>

  const sessions = data?.sessions || []

  if (!sessions.length) {
    return (
      <div className="stacks-empty">
        <div className="stacks-empty-icon">📭</div>
        <div>No pending tasks in any session queue.</div>
        <div className="stacks-empty-hint">Add tasks to a session's stack from the detail panel → Queue tab.</div>
      </div>
    )
  }

  return (
    <div className="stacks-view">
      <div className="stacks-header">
        <span className="stacks-summary">
          {data.total_items} task{data.total_items !== 1 ? 's' : ''} across {sessions.length} session{sessions.length !== 1 ? 's' : ''}
        </span>
        <button className="stacks-refresh" onClick={load}>↺ Refresh</button>
      </div>

      {sessions.map(s => (
        <div key={s.session_id} className="stacks-session">
          <div className="stacks-session-header"
               onClick={() => onSelectSession && onSelectSession({ id: s.session_id, title: s.title, cwd: s.cwd, status: s.status })}
               title="Open session">
            <StatusDot status={s.status} />
            <span className="stacks-session-title">{s.title || s.session_id.slice(0, 8)}</span>
            {s.cwd && <span className="stacks-session-cwd">{fmtCwd(s.cwd)}</span>}
            <span className="stacks-session-count">{s.count} task{s.count !== 1 ? 's' : ''}</span>
          </div>
          <ol className="stacks-items">
            {s.items.map((item, idx) => (
              <li key={item.id || idx} className="stacks-item">
                <span className="stacks-item-num">{idx + 1}</span>
                <span className="stacks-item-text">{item.text}</span>
                {item.sent_at && <span className="stacks-item-sent" title="Sent">✓</span>}
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}
