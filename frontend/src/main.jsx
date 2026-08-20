import React from 'react'
import ReactDOM from 'react-dom/client'
import App, { AppErrorBoundary } from './App.jsx'
import { ToastProvider } from './state/ToastContext.jsx'
import { ConfirmProvider } from './state/ConfirmContext.jsx'
import { SessionsProvider } from './state/SessionsContext.jsx'
import { ApprovalsProvider } from './state/ApprovalsContext.jsx'
import './App.css'

// Dev mode token bootstrap.
// In the packaged app, app.py patches window.fetch to inject X-Local-Token.
// In Vite dev mode (localhost:5173 → backend :19419), there's no pywebview,
// so we fetch the token from the dev-only endpoint and patch fetch ourselves.
// This is a no-op in the installed app (the endpoint returns 404 when not DEV).
async function bootstrapDevToken() {
  if (window._qdLocalToken) return // already injected (packaged app)
  try {
    const backendBase = import.meta.env.DEV ? 'http://127.0.0.1:19419' : ''
    const r = await fetch(`${backendBase}/api/dev/token`)
    if (!r.ok) return
    const { token } = await r.json()
    if (!token) return
    window._qdLocalToken = token
    // Patch fetch to inject the token on every request
    const _orig = window.fetch.bind(window)
    window.fetch = function(input, init) {
      init = init ? Object.assign({}, init) : {}
      const headers = new Headers(init.headers || {})
      if (!headers.has('X-Local-Token')) headers.set('X-Local-Token', token)
      init.headers = headers
      return _orig(input, init)
    }
  } catch { /* backend not up yet or not in dev mode — silent */ }
}

bootstrapDevToken().then(() => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <AppErrorBoundary>
      <ToastProvider>
        <ConfirmProvider>
          <SessionsProvider>
            <ApprovalsProvider>
              <App />
            </ApprovalsProvider>
          </SessionsProvider>
        </ConfirmProvider>
      </ToastProvider>
    </AppErrorBoundary>
  )
})
