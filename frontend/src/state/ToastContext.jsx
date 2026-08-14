import React, { createContext, useCallback, useContext, useRef, useState } from 'react'

// Transient messages. Lifted out of App so a view that has not been written yet
// can report a failure without a notify prop threaded down to reach it.
const ToastContext = createContext(() => {})

export const useToast = () => useContext(ToastContext)

function Toasts({ items, onDismiss }) {
  if (!items.length) return null
  return (
    <div className="toasts">
      {items.map(x => (
        <div key={x.id} className={`toast toast-${x.kind}`} onClick={() => onDismiss(x.id)}>
          {x.text}
        </div>
      ))}
    </div>
  )
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const seq = useRef(0)

  // Errors stay twice as long: they are the ones worth reading twice, and the
  // ones most likely to arrive while you are looking somewhere else.
  const notify = useCallback((text, kind = 'info') => {
    const id = ++seq.current
    setToasts(prev => [...prev, { id, text, kind }])
    setTimeout(() => setToasts(prev => prev.filter(x => x.id !== id)), kind === 'error' ? 9000 : 4500)
  }, [])

  const dismiss = useCallback((id) => setToasts(prev => prev.filter(x => x.id !== id)), [])

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <Toasts items={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}
