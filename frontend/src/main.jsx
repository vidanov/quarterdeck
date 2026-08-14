import React from 'react'
import ReactDOM from 'react-dom/client'
import App, { AppErrorBoundary } from './App.jsx'
import { ToastProvider } from './state/ToastContext.jsx'
import { ConfirmProvider } from './state/ConfirmContext.jsx'
import { SessionsProvider } from './state/SessionsContext.jsx'
import { ApprovalsProvider } from './state/ApprovalsContext.jsx'
import './App.css'

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
