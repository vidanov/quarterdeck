/**
 * usePasteAttachments — intercepts large clipboard pastes and turns them into
 * file-backed attachment tiles instead of flooding the textarea.
 *
 * Usage:
 *   const { attachments, onPaste, removeAttachment, clearAttachments } =
 *     usePasteAttachments({ sessionId })
 *
 *   <textarea onPaste={onPaste} … />
 *   <PasteAttachments attachments={attachments} onRemove={removeAttachment} />
 */
import { useState, useCallback } from 'react'
import { createPaste, deletePaste } from '../api/sessions'

// Mirror the backend thresholds (also used by DocCard heuristic)
export const PASTE_MIN_CHARS = 1200
export const PASTE_MIN_LINES = 20

function shouldCollapse(text) {
  return text.length >= PASTE_MIN_CHARS || (text.match(/\n/g) || []).length + 1 >= PASTE_MIN_LINES
}

export function usePasteAttachments({ sessionId = null } = {}) {
  const [attachments, setAttachments] = useState([])

  const onPaste = useCallback(async (e) => {
    const text = e.clipboardData?.getData('text/plain') ?? ''
    if (!shouldCollapse(text)) return  // let the browser paste normally

    e.preventDefault()
    // Optimistic placeholder while uploading
    const tmpId = `tmp-${Date.now()}`
    const lines = text.split('\n').length
    const preview = text.split('\n').slice(0, 4).join('\n')
    setAttachments(prev => [...prev, {
      id: tmpId,
      name: null,
      session_id: sessionId || '_unassigned',
      lines,
      bytes: new TextEncoder().encode(text).length,
      size_display: formatBytes(new TextEncoder().encode(text).length),
      preview,
      uploading: true,
    }])

    try {
      const meta = await createPaste(text, sessionId)
      setAttachments(prev => prev.map(a =>
        a.id === tmpId ? { ...meta, id: meta.name, uploading: false } : a
      ))
    } catch {
      // Upload failed — remove the placeholder
      setAttachments(prev => prev.filter(a => a.id !== tmpId))
    }
  }, [sessionId])

  const removeAttachment = useCallback((att) => {
    setAttachments(prev => prev.filter(a => a.id !== att.id))
    if (att.name && att.session_id) {
      deletePaste(att.session_id, att.name).catch(() => {})
    }
  }, [])

  const clearAttachments = useCallback(() => setAttachments([]), [])

  return { attachments, setAttachments, onPaste, removeAttachment, clearAttachments }
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
