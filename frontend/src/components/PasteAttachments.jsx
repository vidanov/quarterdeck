/**
 * PasteAttachments — renders the attachment tile row above the composer input.
 *
 * Each tile shows: clipped 4-line preview, PASTED badge, line/size counts,
 * × to remove, and click-to-expand in a modal.
 */
import { useState } from 'react'
import { getPasteText } from '../api/sessions'

export function PasteAttachments({ attachments, onRemove }) {
  if (!attachments || attachments.length === 0) return null
  return (
    <div className="paste-attachments">
      {attachments.map(att => (
        <PasteTile key={att.id} att={att} onRemove={onRemove} />
      ))}
    </div>
  )
}

function PasteTile({ att, onRemove, compact = false }) {
  const [expanded, setExpanded] = useState(false)
  const [fullText, setFullText] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleExpand = async () => {
    if (expanded) { setExpanded(false); return }
    if (!fullText && att.name && att.session_id && !att.uploading) {
      setLoading(true)
      try {
        const d = await getPasteText(att.session_id, att.name)
        setFullText(d.text ?? att.preview ?? '')
      } catch {
        setFullText(att.preview ?? '(file unavailable)')
      }
      setLoading(false)
    }
    setExpanded(true)
  }

  const previewLines = (att.preview ?? '').split('\n').slice(0, 4).join('\n')

  return (
    <>
      <div className={`paste-tile${compact ? ' paste-tile-compact' : ''}${att.uploading ? ' paste-tile-uploading' : ''}`}>
        <div className="paste-tile-preview" onClick={handleExpand} title="Click to expand">
          <pre className="paste-tile-text">{previewLines}</pre>
        </div>
        <div className="paste-tile-footer">
          <span className="paste-tile-badge">PASTED</span>
          <span className="paste-tile-meta">
            {att.uploading ? 'uploading…' : `${att.lines} lines · ${att.size_display}`}
          </span>
          <button className="paste-tile-expand" onClick={handleExpand} title="Expand">
            {expanded ? '⌃' : '⌄'}
          </button>
          <button className="paste-tile-remove" onClick={() => onRemove(att)} title="Remove attachment">×</button>
        </div>
      </div>
      {expanded && (
        <div className="paste-modal-overlay" onClick={() => setExpanded(false)}>
          <div className="paste-modal" onClick={e => e.stopPropagation()}>
            <div className="paste-modal-header">
              <span>📄 {att.name || 'Pasted document'}</span>
              <span className="paste-modal-meta">{att.lines} lines · {att.size_display}</span>
              <button className="paste-modal-close" onClick={() => setExpanded(false)}>✕</button>
            </div>
            <pre className="paste-modal-body">
              {loading ? 'Loading…' : (fullText ?? att.preview ?? '')}
            </pre>
          </div>
        </div>
      )}
    </>
  )
}

// Compact variant for card/wall contexts
export function PasteTileCompact({ att, onRemove }) {
  return <PasteTile att={att} onRemove={onRemove} compact />
}
