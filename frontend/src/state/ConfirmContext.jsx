import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

// A promise-based replacement for window.confirm, which the pywebview shell
// stubs out. Every destructive action in the app goes through it, so it lives
// at the root rather than being passed down from whichever view happens to own
// the button.
const ConfirmContext = createContext(() => Promise.resolve(false))
const ConfirmPendingContext = createContext(false)

export const useConfirm = () => useContext(ConfirmContext)

// Whether a dialog is currently up. Bare-letter keyboard shortcuts have to
// stand down while one is, or F would maximise the panel behind it.
export const useConfirmPending = () => useContext(ConfirmPendingContext)

function ConfirmDialog({ request, onResolve }) {
  // React flushes the state update synchronously during the click that opens
  // this, so the backdrop mounts while that same click is still bubbling and
  // would instantly dismiss itself. Ignore backdrop clicks until it settles.
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!request) { setArmed(false); return }
    const timer = setTimeout(() => setArmed(true), 250)
    return () => clearTimeout(timer)
  }, [request])

  useEffect(() => {
    if (!request) return
    // Escape cancels; there is deliberately no Enter-to-confirm, because these
    // dialogs guard destructive actions and a stray keypress must not approve one.
    const onKey = (e) => { if (e.key === 'Escape') onResolve(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [request, onResolve])

  if (!request) return null
  return (
    <div className="modal-backdrop" onClick={() => { if (armed) onResolve(false) }}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">{request.title}</h3>
        {request.body && <p className="modal-body">{request.body}</p>}
        <div className="modal-actions">
          <button className="modal-cancel" onClick={() => onResolve(false)}>Cancel</button>
          <button className="modal-ok" onClick={() => onResolve(true)}>
            {request.confirmLabel || 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null)
  const resolveRef = useRef(null)

  const askConfirm = useCallback((title, body, confirmLabel) => new Promise(resolve => {
    resolveRef.current = resolve
    setRequest({ title, body, confirmLabel })
  }), [])

  const resolve = useCallback((answer) => {
    setRequest(null)
    const fn = resolveRef.current
    resolveRef.current = null
    if (fn) fn(answer)
  }, [])

  return (
    <ConfirmContext.Provider value={askConfirm}>
      <ConfirmPendingContext.Provider value={!!request}>
        {children}
        <ConfirmDialog request={request} onResolve={resolve} />
      </ConfirmPendingContext.Provider>
    </ConfirmContext.Provider>
  )
}
