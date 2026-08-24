import { useState, useEffect } from 'react'
import * as cliApi from '../api/cli'

/**
 * useCLI — manages CLI pane binding state for a session.
 *
 * The hook owns: status polling, bind/unbind, picker open/close.
 * sendToCli is not here — it touches the composer's draft state, so
 * it lives in DetailPanel and just calls cliApi.sendToCLI directly.
 *
 * Returns:
 *   cliStatus    — null | {bound, tmux_session, cwd, status}
 *   cliSendMode  — boolean (send composer input to CLI instead of session)
 *   setCLISendMode
 *   openCliBinder — open the picker (or auto-bind to natural pane)
 *   cliBindOpen
 *   cliInstances  — list from /api/cli/list
 *   bindCli(tmuxSession)
 *   unbindCli()
 *   setCliBindOpen
 */
export function useCLI(sessionId, notify) {
  const [cliStatus, setCliStatus] = useState(null)
  const [cliBindOpen, setCliBindOpen] = useState(false)
  const [cliInstances, setCliInstances] = useState([])
  const [cliSendMode, setCLISendMode] = useState(false)

  // Poll CLI status every 5 s while the session is open
  useEffect(() => {
    if (!sessionId) return
    let active = true
    const poll = () => {
      cliApi.getCLIStatus(sessionId)
        .then(d => { if (active) setCliStatus(d) })
        .catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 5000)
    return () => { active = false; clearInterval(iv) }
  }, [sessionId])

  const openCliBinder = () => {
    // Try to auto-bind to the session's own natural tmux pane first
    const naturalTmux = `kiro-${sessionId}`
    cliApi.bindCLI(sessionId, naturalTmux)
      .then(d => {
        if (d.ok) {
          cliApi.getCLIStatus(sessionId).then(setCliStatus)
        } else {
          cliApi.listCLI()
            .then(r => { setCliInstances(r.instances || []); setCliBindOpen(true) })
            .catch(() => {})
        }
      })
      .catch(() => {
        cliApi.listCLI()
          .then(r => { setCliInstances(r.instances || []); setCliBindOpen(true) })
          .catch(() => {})
      })
  }

  const bindCli = (tmuxSession) => {
    cliApi.bindCLI(sessionId, tmuxSession)
      .then(d => {
        if (d.ok) { setCliBindOpen(false); cliApi.getCLIStatus(sessionId).then(setCliStatus) }
        else notify(d.error || 'Bind failed', 'error')
      })
  }

  const unbindCli = () => {
    cliApi.unbindCLI(sessionId)
      .then(() => { setCliStatus({ bound: false, status: 'unbound' }); setCLISendMode(false) })
  }

  return {
    cliStatus,
    cliSendMode,
    setCLISendMode,
    openCliBinder,
    cliBindOpen,
    setCliBindOpen,
    cliInstances,
    bindCli,
    unbindCli,
  }
}
