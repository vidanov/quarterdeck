import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import * as api from '../api/sessions'
import * as settingsApi from '../api/settings'

// The session list, polled in one place. Two views mounted at once must not
// mean two polls disagreeing about what is running, which is the whole reason
// this is a provider rather than a hook each view calls for itself.
const SessionsContext = createContext(null)

export const useSessions = () => useContext(SessionsContext)

// Default poll intervals (ms). Overridden by settings if configured.
const DEFAULT_POLL_MS = 2000

// How long a card stays flagged after its status changes to one that wants you.
const NOTIFY_MS = 5000

export function SessionsProvider({ children }) {
  const [sessions, setSessions] = useState([])
  const [error, setError] = useState(null)
  const [loaded, setLoaded] = useState(false)  // true after first successful fetch
  const [notifiedIds, setNotifiedIds] = useState(new Set())
  const [ackedIds, setAckedIds] = useState(new Set())
  // Sessions asked to end. Held here so the card can go at once rather than
  // sitting there through a clean quit, which read as closing being broken.
  const [killing, setKilling] = useState(new Set())
  // Show hidden sessions (captain/bosun) — off by default
  const [showHidden, setShowHidden] = useState(false)
  const showHiddenRef = useRef(false)
  const prevStatuses = useRef({})
  const pollMs = useRef(DEFAULT_POLL_MS)

  // Load configured poll interval once
  useEffect(() => {
    settingsApi.getSettings().then(s => {
      const ms = s['poll-sessions-ms']
      if (ms && ms > 0) pollMs.current = ms
    }).catch(() => {})
  }, [])

  // Resolves with the list it just read, so a caller that needs the sessions
  // themselves — taking a snapshot — does not have to fetch them again.
  const refresh = useCallback(() => api.listSessions(showHiddenRef.current)
    .then(data => {
      const newSessions = data.sessions || []
      setSessions(prev => {
        // Preserve ghost names: if a real session has "Untitled" as its name
        // but there is a ghost (optimistic or resolving) with the same cwd
        // dispatched recently, keep the ghost's task text until kiro-cli
        // writes a real title.  Ghosts stay alive until the real session has
        // a non-Untitled name, or until 90 s have elapsed, so the name does
        // not flash "Untitled" in the gap between ghost removal and the agent
        // writing a title.
        const ghosts = prev.filter(s => s._optimistic || s._resolving)
        const merged = newSessions.map(s => {
          if (s.title && s.title !== 'Untitled' && s.name && s.name !== 'Untitled') return s
          // Find a ghost for the same cwd dispatched in the last 90 seconds
          const ghost = ghosts.find(g =>
            g.cwd && g.cwd === s.cwd &&
            Date.now() - new Date(g.updated_at).getTime() < 90000
          )
          if (ghost) return {
            ...s,
            name: (ghost.name && ghost.name !== 'Untitled') ? ghost.name : s.name,
            title: (s.title && s.title !== 'Untitled') ? s.title : ghost.title,
          }
          return s
        })
        // Drop ghosts that have been superseded by a real session with a title,
        // or that have aged past 90 s.
        const realIds = new Set(newSessions.map(s => s.id))
        const activeGhosts = ghosts.filter(g => {
          if (realIds.has(g.id)) return false          // nonce never becomes a real id
          const age = Date.now() - new Date(g.updated_at).getTime()
          if (age > 90000) return false
          // Keep resolving ghosts until the real session has a proper title
          if (g._resolving) {
            const real = newSessions.find(s => s.cwd === g.cwd && s.title && s.title !== 'Untitled')
            if (real) return false
          }
          return true
        })
        // Pure ghosts that are still _optimistic (server not yet replied) stay
        // at the front; resolved ghosts are invisible placeholders only.
        const visibleGhosts = activeGhosts.filter(g => g._optimistic)
        const next = [...visibleGhosts, ...merged]
        // Bail out: if the session list hasn't materially changed, keep the
        // same array reference to prevent downstream re-renders (DetailPanel
        // depends on session identity via props).
        if (next.length === prev.length && next.every((s, i) => {
          const p = prev[i]
          return p && s.id === p.id && s.status === p.status && s.control === p.control
            && s.title === p.title && s.updated_at === p.updated_at
        })) return prev
        return next
      })
      setError(null)
      setLoaded(true)
      const newNotified = new Set()
      for (const s of newSessions) {
        const prev = prevStatuses.current[s.id]
        if (prev && prev !== s.status && (s.status === 'idle' || s.status === 'awaiting-approval')) {
          newNotified.add(s.id)
        }
      }
      if (newNotified.size > 0) {
        setNotifiedIds(prev => new Set([...prev, ...newNotified]))
        setTimeout(() => setNotifiedIds(prev => {
          const next = new Set(prev)
          newNotified.forEach(id => next.delete(id))
          return next
        }), NOTIFY_MS)
      }

      // Track statuses for next comparison
      const statusMap = {}
      for (const s of newSessions) statusMap[s.id] = s.status
      // Clear acked IDs for sessions no longer awaiting
      setAckedIds(prev => {
        const next = new Set(prev)
        for (const id of prev) {
          const s = newSessions.find(x => x.id === id)
          if (!s || s.status !== 'awaiting-approval') next.delete(id)
        }
        return next
      })
      prevStatuses.current = statusMap

      // Clear stale killing entries — if a session was killed but later resumed
      // (same ID), it re-appears in newSessions and must become visible again.
      const activeIds = new Set(newSessions.map(s => s.id))
      setKilling(prev => {
        if (prev.size === 0) return prev
        const next = new Set([...prev].filter(id => !activeIds.has(id)))
        return next.size === prev.size ? prev : next
      })

      return newSessions
    })
    .catch(() => { setError('Backend not reachable'); return [] }), [])

  // Spawning, resuming and taking over all finish asynchronously somewhere the
  // poll cannot see — tmux correlating an id, kiro-cli redrawing after input.
  // So an action nudges the list on a schedule of its own choosing rather than
  // waiting out the interval. The delays are per-caller and deliberately not
  // averaged into one: id-correlation measures about 2.7s, a redraw after input
  // is under a second.
  const refreshBurst = useCallback((delays) => {
    delays.forEach(ms => setTimeout(refresh, ms))
  }, [refresh])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, pollMs.current)
    return () => clearInterval(interval)
  }, [refresh])

  const markKilling = useCallback((id) => setKilling(prev => new Set([...prev, id])), [])
  const unmarkKilling = useCallback((id) => setKilling(prev => {
    const next = new Set(prev)
    next.delete(id)
    return next
  }), [])

  // UI-only: dismiss the notification and suppress the awaiting animation.
  const ack = useCallback((id) => {
    setNotifiedIds(prev => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    setAckedIds(prev => new Set([...prev, id]))
  }, [])

  // Optimistic dispatch: insert a ghost card immediately so the user sees
  // feedback in <50ms. The ghost uses the nonce as its id and is replaced once
  // the server returns a real session id via the normal poll. If the server
  // errors, the caller removes it via rejectOptimistic.
  const addOptimistic = useCallback((ghost) => {
    setSessions(prev => [ghost, ...prev.filter(s => s.id !== ghost.id)])
  }, [])

  const resolveOptimistic = useCallback((nonce) => {
    // Transition the ghost from _optimistic (visible card) to _resolving
    // (invisible placeholder kept only so the merge loop can inherit the task
    // name onto the real session while it still says "Untitled").  The merge
    // loop removes _resolving entries once the real session has a proper title
    // or after 90 s.
    setSessions(prev => prev.map(s =>
      s.id === nonce ? { ...s, _optimistic: false, _resolving: true } : s
    ))
  }, [])

  const rejectOptimistic = useCallback((nonce) => {
    setSessions(prev => prev.filter(s => s.id !== nonce))
  }, [])

  // Keep ref in sync so the poll callback (which can't depend on showHidden
  // without recreating the interval) reads the latest value.
  const toggleShowHidden = useCallback((val) => {
    const next = typeof val === 'boolean' ? val : !showHiddenRef.current
    showHiddenRef.current = next
    setShowHidden(next)
  }, [])

  const value = {
    sessions, error, loaded, refresh, refreshBurst,
    killing, markKilling, unmarkKilling,
    notifiedIds, ackedIds, ack,
    addOptimistic, resolveOptimistic, rejectOptimistic,
    showHidden, toggleShowHidden,
  }

  return <SessionsContext.Provider value={value}>{children}</SessionsContext.Provider>
}
