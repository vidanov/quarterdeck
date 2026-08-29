import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

import * as approvalsApi from '../api/approvals'
import { useToast } from './ToastContext'

// Tool calls held by the preToolUse hook. Above the app because answering one
// has to work identically from the grid, from the card, from the detail panel
// and from any view added later — reimplementing this per view is exactly how
// two of them end up disagreeing about whether a call was already answered.
const ApprovalsContext = createContext(null)

export const useApprovals = () => useContext(ApprovalsContext)

// A gated session's tool calls are held until this poll surfaces them and
// someone answers, so the interval is the floor on how long every held call
// waits. Gating is opt-in per session, so on a machine with nothing gated this
// is a cheap request against an empty directory.
const POLL_MS = 2000

// How long an answered request stays suppressed if the server never stops
// reporting it. A backstop only — normally the server dropping it is the
// acknowledgement. Without this a request the server never drops would stay
// invisible for the rest of the session.
const SUPPRESS_MAX_MS = 30000

export function ApprovalsProvider({ children }) {
  const notify = useToast()
  const [pendingApprovals, setPendingApprovals] = useState([])
  // Requests answered here that the server may still be reporting. Optimistic
  // removal is not enough on its own: the answer has to reach the hook, and
  // until it does /api/approvals still lists the call, so the next poll put the
  // row back a second after it was clicked. Answering read as not having worked.
  const answered = useRef(new Map())

  useEffect(() => {
    // Back off when nothing is pending: poll every 8s instead of 2s.
    // Most users have gating disabled, so this saves ~3 req/s of overhead.
    let emptyCount = 0
    const BACKOFF_THRESHOLD = 3  // after 3 empty polls, slow down
    const poll = () => approvalsApi.listApprovals()
      .then(d => {
        const incoming = d.approvals || []
        const live = new Set(incoming.map(a => a.request_id))
        // Forget a suppression once the server stops reporting it — that is the
        // acknowledgement.
        for (const [id, at] of answered.current) {
          if (!live.has(id) || Date.now() - at > SUPPRESS_MAX_MS) answered.current.delete(id)
        }
        const visible = incoming.filter(a => !answered.current.has(a.request_id))
        setPendingApprovals(visible)
        emptyCount = visible.length > 0 ? 0 : emptyCount + 1
      })
      .catch(() => {})
    poll()
    const interval = setInterval(() => {
      // Fast while approvals are pending or just appeared; slow otherwise.
      if (emptyCount < BACKOFF_THRESHOLD) poll()
      else if (emptyCount % 4 === 0) poll()  // ~8s cadence (4 × 2s)
      emptyCount++
    }, POLL_MS)
    return () => clearInterval(interval)
  }, [])

  const respondApproval = useCallback((sessionId, requestId, allow) => {
    // Optimistic: remove from the UI immediately, don't wait for the server
    // round-trip, and suppress it until the server agrees it is gone.
    answered.current.set(requestId, Date.now())
    setPendingApprovals(prev => prev.filter(a => a.request_id !== requestId))
    approvalsApi.answerApproval(requestId, sessionId, allow)
      .then(d => {
        if (d.ok) return
        // It did not take, so stop hiding it — leaving the row hidden after a
        // failure is the one outcome worse than the flicker this replaced.
        answered.current.delete(requestId)
        notify(d.error || 'Could not respond', 'error')
      })
      .catch(() => {
        answered.current.delete(requestId)
        notify('Backend unreachable', 'error')
      })
  }, [notify])

  const dismissAll = useCallback(() => {
    // Optimistic clear, plus the same suppression a single answer gets —
    // otherwise the whole banner returns on the next poll.
    setPendingApprovals(prev => {
      prev.forEach(a => answered.current.set(a.request_id, Date.now()))
      return []
    })
    approvalsApi.dismissAllApprovals().catch(() => {})
  }, [])

  // The oldest held call per session: with several waiting, that is the one
  // that has blocked the agent longest, so it is the one to put an Allow next
  // to. `pendingApprovals` is sorted newest first, so the last match wins.
  const heldBySession = useMemo(() => {
    const m = new Map()
    pendingApprovals.forEach(a => m.set(a.session_id, a))
    return m
  }, [pendingApprovals])

  const value = { pendingApprovals, heldBySession, respondApproval, dismissAll }

  return <ApprovalsContext.Provider value={value}>{children}</ApprovalsContext.Provider>
}
