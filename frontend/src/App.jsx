import React, { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'

// Top-level error boundary — catches any render crash in the whole app and
// shows a recovery button instead of a permanent white screen.
export class AppErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(e) { return { error: e } }
  componentDidCatch(e, info) { console.error('[Quarterdeck] render crash:', e, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'100vh',gap:16,padding:32,fontFamily:'system-ui,sans-serif'}}>
          <div style={{fontSize:18,fontWeight:600,color:'#1e293b'}}>⚠ Something went wrong</div>
          <div style={{fontSize:12,color:'#94a3b8',fontFamily:'monospace',maxWidth:400,wordBreak:'break-all',textAlign:'center'}}>
            {String(this.state.error?.message || this.state.error)}
          </div>
          <button style={{padding:'8px 20px',border:'1px solid #d1d5db',borderRadius:6,background:'#fff',cursor:'pointer',fontSize:13}}
                  onClick={() => this.setState({ error: null })}>
            ↺ Reload view
          </button>
          <button style={{padding:'6px 14px',border:'none',background:'none',cursor:'pointer',fontSize:12,color:'#94a3b8'}}
                  onClick={() => window.location.reload()}>
            Full page reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

import { errorOf } from './api/client'
import * as api from './api/sessions'
import * as settingsApi from './api/settings'
import * as collectionsApi from './api/collections'
import * as profilesApi from './api/profiles'
import { useToast } from './state/ToastContext'
import { useConfirm, useConfirmPending } from './state/ConfirmContext'
import { useSessions } from './state/SessionsContext'
import { useApprovals } from './state/ApprovalsContext'
import { partitionByAttention } from './state/attention'
import SettingsPanel from './components/SettingsPanel'
import DetailPanel from './components/DetailPanel'
import CollectionsPanel from './components/CollectionsPanel'
import { CardReply, SessionCard, AttentionBar, ListView } from './components/SessionGrid'
import StacksView from './components/StacksView'
import { QuickCreate, CommandBar, NewSessionLauncher } from './components/Launcher'
import Markdown from './components/Markdown'
import { timeAgo, showPath, STATUS_CONFIG } from './utils'

function ProfilePill({ visibleSessionIds, onProfileSwitch, onCurrentProfile }) {
  const notify = useToast()
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [busy, setBusy] = useState('')
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginUrl, setLoginUrl] = useState('')
  const [loginRegion, setLoginRegion] = useState('')
  const ref = useRef(null)

  const load = () => {
    profilesApi.currentProfile().then(d => {
      setCurrent(d)
      if (onCurrentProfile) onCurrentProfile(d?.active_profile || '')
    }).catch(() => {})
    profilesApi.listProfiles().then(d => setProfiles(d.profiles || [])).catch(() => {})
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handleSwitch = async (name) => {
    setBusy('switch-' + name)
    const d = await profilesApi.switchProfile(name).catch(() => ({ error: 'Network error' }))
    setBusy('')
    if (d.error) { notify(d.error, 'error'); return }
    notify(`Switched to "${name}" (${d.email})`, 'info')
    load()
    setOpen(false)
    if (onProfileSwitch) onProfileSwitch()
  }

  const handleLogout = async () => {
    setBusy('logout')
    const d = await profilesApi.kiroLogout().catch(() => ({ error: 'Network error' }))
    setBusy('')
    if (d.error) { notify(d.error, 'error'); return }
    notify('Logged out', 'info')
    load()
    setOpen(false)
  }

  const handleLogin = async () => {
    setBusy('login')
    const opts = loginUrl ? { license: 'pro', identity_provider: loginUrl, region: loginRegion || 'eu-central-1' } : { license: 'free' }
    const d = await profilesApi.kiroLogin(opts).catch(() => ({ error: 'Network error' }))
    setBusy('')
    if (d.error) { notify(d.error, 'error'); return }
    notify('Login window opened in Terminal', 'info')
    setLoginOpen(false)
    setOpen(false)
  }

  const handleRestartVisible = async () => {
    if (!visibleSessionIds?.length) { notify('No visible sessions to restart', 'info'); return }
    setBusy('restart')
    const d = await profilesApi.restartVisibleSessions(visibleSessionIds).catch(() => ({ error: 'Network error' }))
    setBusy('')
    if (d.error) { notify(d.error, 'error'); return }
    notify(`Restarted ${d.restarted} session${d.restarted === 1 ? '' : 's'}`, 'info')
    setOpen(false)
  }

  const label = current?.active_profile || (current?.email ? current.email.split('@')[0] : '···')
  const email = current?.email || ''

  return (
    <div className="profile-pill-wrap" ref={ref}>
      <button className={`profile-pill ${open ? 'active' : ''}`} onClick={() => setOpen(v => !v)} title={email}>
        <span className="profile-pill-icon">◉</span>
        <span className="profile-pill-label">{label}</span>
      </button>

      {open && (
        <div className="profile-dropdown">
          <div className="profile-dropdown-header">
            <div className="profile-dropdown-email">{email || 'Not logged in'}</div>
            {current?.active_profile && (
              <div className="profile-dropdown-active">profile: {current.active_profile}</div>
            )}
          </div>

          {profiles.length > 0 && (
            <div className="profile-dropdown-section">
              <div className="profile-dropdown-label">Switch to</div>
              {profiles.map(p => (
                <button key={p.name}
                        className={`profile-dropdown-item ${current?.active_profile === p.name ? 'active' : ''}`}
                        disabled={!!busy}
                        title={p.profile_arn || p.email}
                        onClick={() => handleSwitch(p.name)}>
                  <span className="profile-dropdown-name">{p.name}</span>
                  <span className="profile-dropdown-meta">{p.email}{p.profile_arn ? ` · ${p.profile_arn.split('/').pop()}` : ''}</span>
                  {current?.active_profile === p.name && <span className="profile-dropdown-check">✓</span>}
                  {busy === 'switch-' + p.name && <span className="profile-dropdown-check">⟳</span>}
                </button>
              ))}
            </div>
          )}

          <div className="profile-dropdown-section">
            <div className="profile-dropdown-label">Actions</div>
            <button className="profile-dropdown-item" disabled={!!busy}
                    onClick={() => { setLoginOpen(v => !v) }}>
              🔑 Login…
            </button>
            <button className="profile-dropdown-item" disabled={!!busy}
                    onClick={handleLogout}>
              {busy === 'logout' ? '⟳ Logging out…' : '⎋ Logout'}
            </button>
            <button className="profile-dropdown-item" disabled={!!busy || !visibleSessionIds?.length}
                    title={`Restart ${visibleSessionIds?.length || 0} visible session(s)`}
                    onClick={handleRestartVisible}>
              {busy === 'restart' ? '⟳ Restarting…' : `↺ Restart visible (${visibleSessionIds?.length || 0})`}
            </button>
          </div>

          {loginOpen && (
            <div className="profile-dropdown-login">
              <div className="profile-dropdown-label">Identity Center (leave blank for Builder ID)</div>
              <input className="profile-login-input" placeholder="Start URL (https://…awsapps.com/start)"
                     value={loginUrl} onChange={e => setLoginUrl(e.target.value)} />
              <input className="profile-login-input" placeholder="Region (e.g. eu-central-1)"
                     value={loginRegion} onChange={e => setLoginRegion(e.target.value)} />
              <button className="dispatch-btn" disabled={!!busy} onClick={handleLogin}>
                {busy === 'login' ? '⟳ Opening…' : 'Open login in Terminal'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function WallTile({ s, cfg, isLive, isWaiting, focused, onFocus, onOpenFull, onRestart, onKill, held, onRespondApproval }) {
  const [renaming, setRenaming] = React.useState(false)
  const [nameDraft, setNameDraft] = React.useState('')
  const [confirmKill, setConfirmKill] = React.useState(false)
  const notify = useToast()

  const startRename = (e) => {
    e.stopPropagation()
    setNameDraft(s.title || s.name || '')
    setRenaming(true)
  }

  const saveRename = (e) => {
    if (e) e.stopPropagation()
    const title = nameDraft.trim()
    setRenaming(false)
    if (!title || title === (s.title || s.name)) return
    api.renameSession(s.id, title)
      .then(d => { if (d.error) notify(`Rename failed: ${d.error}`, 'error') })
  }

  return (
    <div className={`wall-tile wall-tile-${s.status} ${isLive ? 'wall-tile-live' : ''} ${isWaiting ? 'wall-tile-waiting' : ''} ${s.stalled ? 'wall-tile-stalled' : ''} ${focused ? 'wall-tile-selected' : ''}`}
         onClick={onFocus}
         role="button" tabIndex={0}
         onKeyDown={e => e.key === 'Enter' && onFocus()}>
      <div className="wall-tile-status" style={{ color: cfg.color }}>{cfg.label}{s.stalled ? ' ⚠' : ''}</div>
      {renaming ? (
        <input
          className="wall-tile-rename-input"
          value={nameDraft}
          autoFocus
          onClick={e => e.stopPropagation()}
          onChange={e => setNameDraft(e.target.value)}
          onBlur={saveRename}
          onKeyDown={e => {
            e.stopPropagation()
            if (e.key === 'Enter') saveRename(e)
            if (e.key === 'Escape') setRenaming(false)
          }}
        />
      ) : (
        <div className="wall-tile-name" title="Click to rename"
             onClick={e => { e.stopPropagation(); startRename(e) }}>{s.name || s.folder || 'Session'}</div>
      )}
      <div className="wall-tile-folder">{showPath(s)}</div>
      {s.kiro_profile && (
        <div className="wall-tile-profile" title={`Kiro profile: ${s.kiro_profile}`}>◉ {s.kiro_profile}</div>
      )}
      {(s.summary || s.last_message || s.last_output) && (
        <div className={`wall-tile-last${s.summary ? ' wall-tile-summary' : ''}`}>
          {s.summary || s.last_message || s.last_output?.replace(/\s+/g, ' ').trim().slice(0, 120)}
        </div>
      )}
      {held && onRespondApproval && (
        <div className="wall-tile-approval" onClick={e => e.stopPropagation()}>
          <button className="wall-tile-allow"
                  onClick={() => onRespondApproval(s.id, held.request_id, true)}>Allow</button>
          <button className="wall-tile-deny"
                  onClick={() => onRespondApproval(s.id, held.request_id, false)}>Deny</button>
          <span className="wall-tile-tool" title={JSON.stringify(held.tool_input)}>
            {held.tool_name || 'tool'}
          </span>
        </div>
      )}
      {confirmKill ? (
        <div className="wall-tile-kill-confirm" onClick={e => e.stopPropagation()}>
          <span>Kill session?</span>
          <button className="wall-tile-kill-yes" onClick={e => { e.stopPropagation(); setConfirmKill(false); onKill(e) }}>Kill</button>
          <button className="wall-tile-kill-no" onClick={e => { e.stopPropagation(); setConfirmKill(false) }}>Cancel</button>
        </div>
      ) : (
        <button className="wall-tile-kill" title="Kill session" onClick={e => { e.stopPropagation(); setConfirmKill(true) }}>✕</button>
      )}
      <button className="wall-tile-open" title="Open full session" onClick={e => { e.stopPropagation(); onOpenFull && onOpenFull(s) }}>↗</button>
    </div>
  )
}

export default function App() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const confirmPending = useConfirmPending()
  const {
    sessions, error, refresh: fetchSessions, refreshBurst,
    killing, markKilling, unmarkKilling,
    notifiedIds, ackedIds, ack: handleAckSession,
    addOptimistic, resolveOptimistic, rejectOptimistic,
  } = useSessions()
  const [selected, setSelected] = useState(null)

  // Wrap setSelected to also persist the session id to backend settings
  const selectSession = (session) => {
    setSelected(session)
    if (session?.id) {
      localStorage.setItem('last-session-id', session.id)
      settingsApi.saveSettings({ 'last-session-id': session.id }).catch(() => {})
    } else {
      localStorage.removeItem('last-session-id')
      settingsApi.saveSettings({ 'last-session-id': '' }).catch(() => {})
    }
  }
  // Lifted out of DetailPanel so a card's double-click can open straight into
  // the maximised view, and so F works wherever focus happens to be.
  const [expanded, setExpanded] = useState(false)
  const [returnView, setReturnView] = useState(null) // view to return to when closing expanded detail
  // Focus mode: grid collapses to a thin attention strip, panel takes full width.
  // Different from expanded (which overlays): the grid stays visible.
  const [focusMode, setFocusMode] = useState(false)
  const toggleFocus = () => setFocusMode(v => !v)
  const [launcherOpen, setLauncherOpen] = useState(false)
  const [wallFocused, setWallFocused] = useState(null)
  const [wallInput, setWallInput] = useState('')
  const [wallOutput, setWallOutput] = useState('')
  const [wallGrouped, setWallGrouped] = useState(() => localStorage.getItem('wall-grouped') === '1')
  const [wallCollections, setWallCollections] = useState([])
  const [cmdBarOpen, setCmdBarOpen] = useState(false)
  const [controlFilter, setControlFilter] = useState(() => localStorage.getItem('control-filter') || 'managed')
  const [statusFilter, setStatusFilter] = useState(() => localStorage.getItem('status-filter') || null)
  const [sessionViewMode, setSessionViewMode] = useState(() => {
    const v = localStorage.getItem('session-view-mode') || 'wall'
    return ['cards', 'list', 'wall'].includes(v) ? v : 'wall'
  })
  const [cwdSuggestion, setCwdSuggestion] = useState(null)
  const { pendingApprovals, heldBySession, respondApproval, dismissAll } = useApprovals()
  const [paneTheme, setPaneThemeApp] = useState(() => localStorage.getItem('pane-theme') || 'light')
  const togglePaneTheme = () => {
    const next = paneTheme === 'dark' ? 'light' : 'dark'
    setPaneThemeApp(next)
    localStorage.setItem('pane-theme', next)
  }

  // Working sessions are collapsed by default: the point of the view is what
  // needs you, and a list of agents getting on with it competes with that.
  const [showWorking, setShowWorking] = useState(
    () => localStorage.getItem('show-working') === '1')
  const changeShowWorking = (v) => {
    setShowWorking(v)
    if (v) localStorage.setItem('show-working', '1')
    else localStorage.removeItem('show-working')
  }
  const changeControlFilter = (v) => { setControlFilter(v); localStorage.setItem('control-filter', v) }
  const changeStatusFilter = (v) => { setStatusFilter(v); if (v) localStorage.setItem('status-filter', v); else localStorage.removeItem('status-filter') }
  const changeSessionViewMode = (v) => { setSessionViewMode(v); localStorage.setItem('session-view-mode', v) }

  const [showHidden, setShowHidden] = useState(() => localStorage.getItem('show-hidden') === '1')
  const changeShowHidden = (v) => { setShowHidden(v); if (v) localStorage.setItem('show-hidden', '1'); else localStorage.removeItem('show-hidden') }
  const [activeProfileName, setActiveProfileName] = useState('')
  const openFull = useCallback((session) => {
    selectSession(session)
    setExpanded(true)
  }, [])

  // Closing the panel drops the maximised state with it, so the next session
  // does not open full screen unless it was asked to.
  useEffect(() => { if (!selected) { setExpanded(false); setReturnView(null) } }, [selected])

  // F maximises the open session. Deliberately unmodified — the composer and
  // every other text field is excluded below, so a bare letter is free.
  useEffect(() => {
    if (!selected) return
    const onKey = (e) => {
      if (e.key !== 'f' && e.key !== 'F') return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (confirmPending) return
      const el = e.target
      if (el?.isContentEditable) return
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el?.tagName)) return
      e.preventDefault()
      setExpanded(v => !v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, confirmPending])

  // ⌘K opens the command bar (concierge assistant).
  // ⌘Enter opens the launcher — also fired by the native Session menu via custom event.
  // Guard: ⌘Enter is also "send" in textareas, so skip if an input has focus.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'k' && e.metaKey && !e.shiftKey && !e.altKey) {
        e.preventDefault()
        setCmdBarOpen(v => !v)
      }
      if (e.key === 'Enter' && e.metaKey && !e.shiftKey && !e.altKey) {
        const el = e.target
        if (el?.tagName === 'TEXTAREA' || el?.tagName === 'INPUT' || el?.isContentEditable) return
        e.preventDefault()
        setLauncherOpen(v => !v)
        setView(v => v !== 'active' ? 'active' : v)
        localStorage.setItem('active-view', 'active')
      }
      if ((e.key === 'l' || e.key === 'L') && e.metaKey && e.shiftKey && !e.altKey) {
        e.preventDefault()
        setLauncherOpen(v => !v)
        setView(v => v !== 'active' ? 'active' : v)
        localStorage.setItem('active-view', 'active')
      }
    }
    const onNative = () => {
      setFocusMode(false)
      setLauncherOpen(true)
      setView(v => v !== 'active' ? 'active' : v)
      localStorage.setItem('active-view', 'active')
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('quarterdeck:open-launcher', onNative)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('quarterdeck:open-launcher', onNative)
    }
  }, [])

  const [options, setOptions] = useState({ models: [], efforts: [], commands: [] })
  const [showRecent, setShowRecent] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [view, setView] = useState(() => {
    const v = localStorage.getItem('active-view') || 'active'
    // migrate old view names to new ones
    if (['projects','snapshots','archive'].includes(v)) return 'collections'
    return v
  }) // 'active' | 'collections' | 'stats' | 'settings' | 'stacks'
  const changeView = (v) => {
    setView(v)
    localStorage.setItem('active-view', v)
    settingsApi.saveSettings({ 'last-view': v }).catch(() => {})
    // Exit focus mode and close the detail panel when leaving the active grid
    if (v !== 'active') {
      setFocusMode(false)
      setSelected(null)
    }
  }
  const [collectionSource, setCollectionSource] = useState(() => localStorage.getItem('collection-source') || 'archive')
  const changeCollectionSource = (s) => { setCollectionSource(s); localStorage.setItem('collection-source', s) }
  const [restoring, setRestoring] = useState(false)
  const [snapshots, setSnapshots] = useState([])
  const snapshotsLoaded = useRef(false)
  const wallInputRef = useRef(null)
  // Archive state
  const [archiveQuery, setArchiveQuery] = useState('')
  const [archiveResults, setArchiveResults] = useState([])
  const [archiveTotal, setArchiveTotal] = useState(0)
  const [favourites, setFavourites] = useState([])
  const [stats, setStats] = useState(null)
  const [statsPeriod, setStatsPeriod] = useState('all')
  const [statsFrom, setStatsFrom] = useState('')
  const [statsTo, setStatsTo] = useState('')
  const [statsShowMessages, setStatsShowMessages] = useState(false)
  const [archiveSelected, setArchiveSelected] = useState(new Set())
  // Cleanup state
  const [cleanupPreview, setCleanupPreview] = useState(null)
  const [cleanupLoading, setCleanupLoading] = useState(false)
  const [allCorrections, setAllCorrections] = useState(null)
  const loadCleanupPreview = () => {
    setCleanupLoading(true)
    collectionsApi.getCleanupPreview()
      .then(d => { setCleanupPreview(d); setCleanupLoading(false) })
      .catch(() => setCleanupLoading(false))
  }
  const applyCleanup = async (sessionIds) => {
    if (!(await askConfirm('Delete zombie sessions?', `${sessionIds.length} sessions will be deleted.`, 'Delete'))) return
    collectionsApi.applyCleanup(sessionIds)
      .then(d => {
        loadCleanupPreview() // Refresh
        loadStats() // Refresh stats
      })
  }
  // Projects state
  const [projectsData, setProjectsData] = useState(null)
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [projectsError, setProjectsError] = useState(null)
  const [expandedProjects, setExpandedProjects] = useState(new Set())
  const loadProjects = (refresh = false) => {
    setProjectsLoading(true)
    setProjectsError(null)
    collectionsApi.getProjects(refresh)
      .then(d => { setProjectsData(d); setProjectsLoading(false) })
      .catch(e => { setProjectsError(e.message); setProjectsLoading(false) })
  }
  const deleteProject = async (cwd, name, sessionCount, e) => {
    e.stopPropagation()

    // Ask the server what it would delete. Matching is by path containment, so a
    // nested directory listed as its own project is included too \u2014 the number in
    // the project card is not always the number that would go.
    let preview = null
    try {
      preview = await collectionsApi.previewProjectDelete(cwd)
    } catch { /* fall back to the listed count below */ }

    const previewErr = errorOf(preview)
    if (previewErr) { notify(previewErr, 'error'); return }

    const count = preview?.session_count ?? sessionCount
    const running = preview?.active_sessions?.length || 0
    let detail = `${count} session${count === 1 ? '' : 's'} for "${name}" will be deleted.`
    if (preview && count > sessionCount) {
      detail += ` That is more than the ${sessionCount} listed, because sessions in subdirectories count as part of this project.`
    }
    if (running) {
      detail += ` ${running} of them ${running === 1 ? 'is' : 'are'} still running, so the delete will be refused until ${running === 1 ? 'it is' : 'they are'} ended.`
    }
    if (count === 0) { notify(`No sessions to delete for ${name}`); return }
    if (!(await askConfirm('Delete this project\u2019s sessions?', detail, 'Delete'))) return

    collectionsApi.deleteProjectSessions(cwd)
      .then(d => {
        const err = errorOf(d)
        if (err) {
          // The endpoint is all-or-nothing: one running session refuses the
          // whole project, and it names them, so say which.
          const active = d.active_sessions?.length
          notify(active ? `${err} (${active} still running)` : err, 'error')
          return
        }
        notify(`Deleted ${d.deleted_sessions} session${d.deleted_sessions === 1 ? '' : 's'} from ${name}`)
      })
      .catch(() => notify('Delete failed', 'error'))
      .then(() => loadProjects())
  }
  const toggleProject = (name) => {
    setExpandedProjects(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }
  const loadStats = (period, from_, to_) => {
    const p = period || statsPeriod
    collectionsApi.getStats({ period: p, from: from_ || statsFrom, to: to_ || statsTo })
      .then(d => setStats(d)).catch(() => {})
  }
  // Model / effort / command lists come from the backend so the UI never
  // offers a value kiro-cli would reject.
  useEffect(() => {
    settingsApi.getOptions().then(setOptions).catch(() => {})
    settingsApi.getCwdSuggestion().then(setCwdSuggestion).catch(() => {})
  }, [])
  // Build freshness — poll every 30s so a STALE BUILD banner appears when
  // source has changed without a rebuild. Prevents false "done" claims.
  const [buildHealth, setBuildHealth] = useState(null)
  useEffect(() => {
    const check = () => fetch('/api/health/build')
      .then(r => r.json()).then(setBuildHealth).catch(() => {})
    check()
    const t = setInterval(check, 30000)
    return () => clearInterval(t)
  }, [])
  // Load favourites on mount
  useEffect(() => {
    collectionsApi.getFavourites().then(d => setFavourites(d.favourites || [])).catch(() => {})
  }, [])
  // Load snapshots from backend on mount
  useEffect(() => {
    collectionsApi.getSnapshots().then(d => {
      setSnapshots(d.snapshots || [])
      snapshotsLoaded.current = true
    }).catch(() => { snapshotsLoaded.current = true })
  }, [])
  // Save snapshots to backend on change (only after initial load)
  useEffect(() => {
    if (!snapshotsLoaded.current) return
    collectionsApi.saveSnapshots(snapshots).catch(() => {})
  }, [snapshots])
  // Load settings from backend on mount
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const lastSessionIdRef = useRef(localStorage.getItem('last-session-id') || '')
  useEffect(() => {
    settingsApi.getSettings().then(d => {
      if (d['detail-tab']) setDetailTab(d['detail-tab'])
      // Restore last view — backend survives app restart even when WKWebView clears localStorage
      if (d['last-view']) {
        const v = d['last-view']
        const valid = ['active', 'collections', 'stats', 'stacks', 'settings']
        if (valid.includes(v)) {
          setView(v)
          localStorage.setItem('active-view', v)
        }
      }
      // Store last session id for restoration once sessions list loads
      if (d['last-session-id']) {
        lastSessionIdRef.current = d['last-session-id']
        localStorage.setItem('last-session-id', d['last-session-id'])
      }
    }).catch(() => {}).finally(() => setSettingsLoaded(true))
  }, [])

  // Once sessions load and settings are ready, restore the last open session
  const sessionRestoredRef = useRef(false)
  useEffect(() => {
    if (sessionRestoredRef.current) return
    if (!settingsLoaded) return
    if (!sessions.length) return
    const id = lastSessionIdRef.current
    if (!id) return
    const match = sessions.find(s => s.id === id)
    if (match) {
      setSelected(match)
      sessionRestoredRef.current = true
    }
  }, [settingsLoaded, sessions])

  // When the wall-focused session finishes (→ idle/done), refresh the output
  const wallFocusedPrevStatus = useRef(null)
  useEffect(() => {
    if (!wallFocused) { wallFocusedPrevStatus.current = null; return }
    const live = sessions.find(s => s.id === wallFocused.id)
    if (!live) return
    const prev = wallFocusedPrevStatus.current
    const isNowDone = live.status === 'idle' || live.status === 'done'
    const wasActive = prev === 'thinking' || prev === 'running'
    if (isNowDone && wasActive) {
      api.getSession(live.id).then(d => { if (d.last_output) setWallOutput(d.last_output) })
    }
    wallFocusedPrevStatus.current = live.status
  }, [sessions, wallFocused])

  // Fetch collections for board view on mount if it was already active
  useEffect(() => {
    if (!wallGrouped) return
    fetch('/api/collections').then(r => r.json())
      .then(d => setWallCollections((d.collections || []).filter(c => c.source !== 'snapshot')))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-trigger summary for managed sessions that just went idle and have no summary yet
  // Auto-trigger summary for managed sessions that just went idle and have no summary yet
  const prevSessionStatuses = useRef({})
  useEffect(() => {
    const prev = prevSessionStatuses.current
    sessions.forEach(s => {
      if (s.control !== 'managed') return
      if (s.summary) return
      const wasActive = prev[s.id] === 'thinking' || prev[s.id] === 'running'
      const isNowIdle = s.status === 'idle' || s.status === 'done'
      if (wasActive && isNowIdle) {
        api.summarize(s.id).catch(() => {})
      }
    })
    sessions.forEach(s => { prev[s.id] = s.status })
  }, [sessions])

  // A snapshot is of the list as it is right now, so it reads through the same
  // refresh the grid uses rather than fetching a second copy of it.
  const takeSnapshot = () => {
    setScanning(true)
    fetchSessions()
      .then(async list => {
        setScanning(false)
        const now = new Date()
        const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        const dateStr = now.toLocaleDateString([], { month: 'short', day: 'numeric' })
        const activeSessions = list.filter(s => {
          if (!['thinking', 'running', 'awaiting-approval', 'idle'].includes(s.status)) return false
          if (s.status === 'idle' && s.updated_at) {
            const age = Date.now() - new Date(s.updated_at).getTime()
            if (age > 2 * 24 * 60 * 60 * 1000) return false
          }
          return true
        })
        if (!activeSessions.length) return
        // Create a collection instead of a local snapshot
        const name = `Snapshot ${dateStr} ${timeStr}`
        try {
          const col = await collectionsApi.createCollection(name, { source: 'snapshot' })
          if (col.error) { notify(col.error, 'error'); return }
          await Promise.all(activeSessions.map(s =>
            collectionsApi.addMember(col.collection.id, { session_id: s.id, cwd: s.cwd })
          ))
          notify(`Collection "${name}" created with ${activeSessions.length} sessions`, 'info')
        } catch {
          notify('Could not create snapshot collection', 'error')
        }
      })
      .catch(() => setScanning(false))
  }

  // Answering and replying from the grid, without opening anything. The detail
  // panel is where a session gets driven; it is the wrong place to be forced
  // into for one line of text or one yes. See CardReply for the reasoning.
  const sendToSession = (sessionId, text) =>
    api.sendInput(sessionId, text)
      .then(d => {
        if (d.error) { notify(`Send failed: ${d.error}`, 'error'); return false }
        // The card's status is derived from the pane, so nudge the list rather
        // than leaving it to say "your turn" at something now thinking.
        refreshBurst([400, 1200])
        return true
      })
      .catch(() => { notify('Send failed: backend unreachable', 'error'); return false })

  const respondPrompt = (sessionId, choice) =>
    api.respond(sessionId, choice)
      .then(d => {
        if (d.error) { notify(`Could not answer: ${d.error}`, 'error'); return false }
        refreshBurst([400, 1200])
        return true
      })
      .catch(() => { notify('Could not answer: backend unreachable', 'error'); return false })

  const handleDispatch = (request) => {
    setLauncherOpen(false)
    // Optimistic: insert a ghost card immediately so the user sees feedback in
    // under 50ms. The ghost has a unique nonce as its id. Once the server
    // returns (or errors), the ghost is removed and replaced by the real session
    // that will appear on the next poll.
    const nonce = `opt-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const folderName = request.cwd ? request.cwd.split('/').pop() : ''
    const ghost = {
      id: nonce,
      nonce,
      title: request.task || '…',
      name: request.task ? request.task.slice(0, 40) : '…',
      folder: folderName,
      cwd: request.cwd || '',
      status: 'starting',
      control: 'starting',
      updated_at: new Date().toISOString(),
      kiro_profile: activeProfileName || undefined,
      _optimistic: true,
    }
    addOptimistic(ghost)
    api.dispatch(request)
      .then(d => {
        resolveOptimistic(nonce)
        if (d.error) { notify(`Dispatch failed: ${d.error}`, 'error'); return }
        fetchSessions()
        refreshBurst([800, 2000, 4000, 8000])
      })
      .catch(() => {
        rejectOptimistic(nonce)
        notify('Dispatch failed: backend unreachable', 'error')
      })
  }

  const handleKillSession = async (sessionId, e) => {
    if (e) e.stopPropagation()
    const s = sessions.find(x => x.id === sessionId)
    // Crew sessions can't be killed — dismiss (archive) them instead
    if (s?.control === 'crew') {
      markKilling(sessionId)
      if (selected?.id === sessionId) setSelected(null)
      api.dismissSession(sessionId)
        .then(d => {
          if (d.error) { notify(`Could not dismiss: ${d.error}`, 'error'); unmarkKilling(sessionId) }
          else fetchSessions()
        })
        .catch(() => unmarkKilling(sessionId))
      return
    }
    // Archived and foreign sessions have no process — delete files directly
    if (s?.control === 'archived' || s?.control === 'foreign') {
      const label = s ? (s.name || s.id) : sessionId
      const ok = await askConfirm(
        'Delete this session?',
        `"${(label || '').slice(0, 80)}" — the conversation files will be permanently deleted.`,
        'Delete',
      )
      if (!ok) return
      markKilling(sessionId)
      if (selected?.id === sessionId) selectSession(null)
      api.deleteSession(sessionId)
        .then(d => {
          if (d.error) { notify(`Could not delete: ${d.error}`, 'error'); unmarkKilling(sessionId) }
          else fetchSessions()
        })
        .catch(() => { notify('Could not delete: backend unreachable', 'error'); unmarkKilling(sessionId) })
      return
    }
    const label = s ? (s.name || s.id) : sessionId
    const ok = await askConfirm(
      'End this session?',
      `"${(label || '').slice(0, 80)}" — ` + (s?.control === 'managed'
        ? 'it will be asked to quit cleanly, so the conversation is kept and can be resumed later.'
        : 'its process will be signalled to stop. The conversation is kept and can be resumed later.'),
      'End session',
    )
    if (!ok) return
    // Hide it straight away: the clean quit happens in the background, and
    // leaving the card sitting there made closing feel broken.
    markKilling(sessionId)
    if (selected?.id === sessionId) setSelected(null)
    api.killSession(sessionId)
      .then(d => {
        if (d.error) {
          notify(`Could not end session: ${d.error}`, 'error')
          unmarkKilling(sessionId)
          return
        }
        notify('Session ending…')
      })
      .catch(() => {
        notify('Could not end session: backend unreachable', 'error')
        unmarkKilling(sessionId)
      })
      .finally(() => refreshBurst([500, 1500, 4000, 9000]))
  }

  // Skip askConfirm — used by wall tile which has its own inline confirm
  const killSessionDirect = (sessionId) => {
    markKilling(sessionId)
    if (selected?.id === sessionId) setSelected(null)
    api.killSession(sessionId)
      .then(d => {
        if (d.error) { notify(`Could not end session: ${d.error}`, 'error'); unmarkKilling(sessionId); return }
        notify('Session ending…')
      })
      .catch(() => { notify('Could not end session: backend unreachable', 'error'); unmarkKilling(sessionId) })
      .finally(() => refreshBurst([500, 1500, 4000, 9000]))
  }

  const handleRestartSession = async (sessionId, e) => {
    if (e) e.stopPropagation()
    // Preserve the current title through the restart window so the card
    // doesn't flash "Untitled" while kiro-cli is starting and hasn't written
    // its title yet. Inject a resolving ghost keyed by the session's cwd;
    // the ghost merge loop will overlay the saved title onto the real session
    // until kiro-cli writes its own.
    const existing = sessions.find(s => s.id === sessionId)
    if (existing?.title && existing.title !== 'Untitled') {
      addOptimistic({
        id: `restart-${sessionId}`,
        nonce: `restart-${sessionId}`,
        title: existing.title,
        name: existing.name || existing.title.slice(0, 40),
        cwd: existing.cwd || '',
        status: 'starting',
        control: 'starting',
        updated_at: new Date().toISOString(),
        _optimistic: false,
        _resolving: true,
      })
    }
    markKilling(sessionId)
    profilesApi.restartVisibleSessions([sessionId])
      .then(d => {
        unmarkKilling(sessionId)
        if (d.error) { notify(d.error, 'error'); return }
        const result = d.results?.[sessionId]
        if (result && result !== 'ok') { notify(`Restart failed: ${result}`, 'error'); return }
        notify('Session restarted', 'info')
        refreshBurst([800, 2000, 5000])
      })
      .catch(() => { unmarkKilling(sessionId); notify('Could not restart session', 'error') })
  }

  const handleCorrectSession = (sessionId) => {
    api.addCorrection(sessionId)
      .then(rec => {
        if (rec.ok) notify('Correction logged — open the session to confirm or withdraw', 'info')
        else notify(rec.error || 'Could not log correction', 'error')
      })
      .catch(() => notify('Backend unreachable', 'error'))
  }

  const handleCancelPending = async (nonce) => {
    const ok = await askConfirm(
      'Give up on this spawn?',
      'It never reported a session id. Its tmux session is killed if one is still ' +
      'running; anything it wrote to disk is kept and will show up as an archived session.',
      'Give up',
    )
    if (!ok) return
    api.cancelPending(nonce)
      .then(d => notify(d.error ? `Could not cancel: ${d.error}` : 'Spawn abandoned',
                        d.error ? 'error' : 'info'))
      .catch(() => notify('Could not cancel: backend unreachable', 'error'))
      .finally(() => fetchSessions())
  }

  const handleTakeover = async (session) => {
    const ok = await askConfirm(
      'Take over this session?',
      `"${(session.title || session.name || '').slice(0, 80)}" — this stops the running ` +
      'kiro-cli process and restarts it under tmux, continuing the same conversation. ' +
      'Unsaved terminal state is lost.',
      'Take over',
    )
    if (!ok) return
    notify('Taking over…')
    api.takeoverSession(session.id)
      .then(d => {
        if (d.error) notify(`Takeover failed: ${d.error}`, 'error')
        else notify('Session is now managed — you can send input')
      })
      .catch(() => notify('Takeover failed: backend unreachable', 'error'))
      .finally(() => refreshBurst([800, 2500, 5000]))
  }

  const handleResumeSession = (session) => {
    api.resumeSession(session.id)
      .then(d => {
        if (d.error) notify(`Resume failed: ${d.error}`, 'error')
        else notify('Session resumed under tmux')
      })
      .catch(() => notify('Resume failed: backend unreachable', 'error'))
      .finally(() => refreshBurst([800, 2500, 5000]))
  }

  const handleBranchSession = (sessionId) => {
    api.branchSession(sessionId)
      .then(() => refreshBurst([3000]))
  }

  const handleCmdBarAction = (action) => {
    if (action.action === 'resume' && action.session_id) {
      api.resumeSession(action.session_id)
        .then(d => {
          if (d.error) notify(`Resume failed: ${d.error}`, 'error')
          else notify('Session resumed')
        })
        .finally(() => refreshBurst([800, 2500]))
    } else if (action.action === 'dispatch') {
      api.dispatch({ task: action.task || '', cwd: action.cwd || '' })
        .then(d => {
          if (d.error) notify(`Dispatch failed: ${d.error}`, 'error')
          else notify(`Launched: ${(action.task || '').slice(0, 50)}`)
        })
        .finally(() => refreshBurst([800, 2500]))
    } else if (action.action === 'filter_project') {
      changeView('projects')
      if (!projectsData) loadProjects()
    } else if (action.action === 'search') {
      changeView('archive')
      setArchiveQuery(action.query || '')
    }
  }

  const handleRestore = (sessionId) => {
    api.resumeSession(sessionId)
      .then(() => refreshBurst([3000]))
  }

  const handleRestoreAll = (snap) => {
    if (!snap || snap.sessions.length === 0) return
    setRestoring(true)
    // Sequential with a gap: each resume spawns a process and waits for its
    // session id, so firing them all at once would race for correlation.
    snap.sessions.reduce((chain, s, i) => chain.then(() => new Promise(resolve => {
      setTimeout(() => {
        api.resumeSession(s.id).then(resolve).catch(resolve)
      }, i * 2000)
    })), Promise.resolve()).then(() => {
      setRestoring(false)
      refreshBurst([2000])
    })
  }

  const handleDeleteSnapshot = (snapId) => {
    collectionsApi.deleteCollection(snapId)
      .then(() => collectionsApi.getSnapshots().then(d => setSnapshots(d.snapshots || [])))
      .catch(() => setSnapshots(prev => prev.filter(s => s.id !== snapId)))
  }

  const searchArchive = (query) => {
    setArchiveQuery(query)
    collectionsApi.searchArchive(query)
      .then(d => { setArchiveResults(d.sessions || []); setArchiveTotal(d.total || 0) })
      .catch(() => {})
  }
  const handleArchiveSearch = searchArchive

  const handleToggleArchiveSelect = (id) => {
    setArchiveSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const handleToggleProject = (name) => {
    setExpandedProjects(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }

  const handleDeleteProject = async (cwd, name, count, e) => {
    if (e) e.stopPropagation()
    const ok = await askConfirm(
      `Delete all sessions in "${name}"?`,
      `${count} session${count === 1 ? '' : 's'} at or below ${cwd} will be permanently deleted.`,
      'Delete')
    if (!ok) return
    collectionsApi.deleteProjectSessions(cwd)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        notify(`Deleted ${d.deleted?.length || 0} sessions`, 'info')
        loadProjects(true)
        fetchSessions()
      })
      .catch(() => notify('Could not delete project sessions', 'error'))
  }

  const handleToggleFavourite = (session) => {
    const isFav = favourites.some(f => f.id === session.id)
    if (isFav) {
      collectionsApi.removeFavourite(session.id)
        .then(() => setFavourites(prev => prev.filter(f => f.id !== session.id)))
    } else {
      collectionsApi.addFavourite(session.id)
        .then(() => setFavourites(prev => [...prev, { id: session.id, title: session.title, cwd: session.cwd, cwd_display: session.cwd_display, name: session.name || '' }]))
    }
  }

  const handleLaunchFavourite = (fav) => {
    api.resumeSession(fav.id)
      .then(d => {
        if (d.error && d.error.includes('not found')) {
          // Session no longer exists — start a fresh session in the same cwd
          return api.dispatch({ cwd: fav.cwd })
            .then(r => {
              if (r.error) notify(`Launch failed: ${r.error}`, 'error')
              else refreshBurst([1000, 3000])
            })
        }
        if (d.error) notify(`Resume failed: ${d.error}`, 'error')
        else refreshBurst([3000])
      })
  }

  // The backend refuses to delete a live session, and says so in a 200 body.
  // Resolves to the id when the session really is gone, null when it is not, so
  // callers only drop rows the server actually deleted.
  const deleteOneSession = (sessionId) =>
    api.deleteSession(sessionId)
      .then(d => {
        const err = errorOf(d)
        if (err) { notify(err, 'error'); return null }
        return sessionId
      })
      .catch(() => { notify('Delete failed', 'error'); return null })

  const handleDeleteArchive = (sessionId) => {
    deleteOneSession(sessionId).then(gone => {
      if (gone) setArchiveResults(prev => prev.filter(s => s.id !== gone))
    })
  }

  const handleBatchDelete = async () => {
    if (archiveSelected.size === 0) return
    if (!(await askConfirm('Delete selected sessions?', `${archiveSelected.size} sessions will be deleted.`, 'Delete'))) return
    Promise.all([...archiveSelected].map(deleteOneSession))
      .then(results => {
        const gone = new Set(results.filter(Boolean))
        if (gone.size) setArchiveResults(prev => prev.filter(s => !gone.has(s.id)))
        // Anything the server refused stays selected, so a retry after ending
        // the session does not need the selection rebuilt by hand.
        setArchiveSelected(prev => new Set([...prev].filter(id => !gone.has(id))))
      })
  }

  const toggleArchiveSelect = (id) => {
    setArchiveSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRenameArchive = (session) => {
    const newName = window.prompt('Rename session:', session.title)
    if (newName !== null && newName.trim() && newName !== session.title) {
      api.renameSession(session.id, newName.trim())
        .then(() => setArchiveResults(prev => prev.map(s => s.id === session.id ? { ...s, title: newName.trim() } : s)))
    }
  }

  const active = sessions.filter(s => {
    if (!['thinking', 'running', 'awaiting-approval', 'idle', 'starting'].includes(s.status)) return false
    // Hide machine-owned workers unless showHidden is on. Defaults: if
    // visible is undefined (old server), treat as true for safety.
    if (s.visible === false && !showHidden) return false
    return true
  })

  // Group sessions sharing a group_id into synthetic group cards.
  // Workers (visible:false) that passed the filter above are children.
  const { groupedActive, groupMap } = (() => {
    const groups = {}  // group_id -> [session, ...]
    const ungrouped = []
    for (const s of active) {
      if (s.group_id) {
        if (!groups[s.group_id]) groups[s.group_id] = []
        groups[s.group_id].push(s)
      } else {
        ungrouped.push(s)
      }
    }
    const groupCards = Object.entries(groups).map(([gid, members]) => {
      // Aggregate state: worst status wins
      const statusPriority = ['awaiting-approval', 'thinking', 'running', 'idle', 'starting']
      const status = statusPriority.find(p => members.some(m => m.status === p)) || members[0].status
      const primary = members.find(m => m.role === 'primary') || members[0]
      return {
        ...primary,
        id: gid,
        _isGroupCard: true,
        _members: members,
        group_id: gid,
        title: `[Group] ${primary.title || gid}`,
        status,
        workerCount: members.length,
        handoverable: false,
      }
    })
    const result = [...ungrouped, ...groupCards]
    return { groupedActive: result, groupMap: groups }
  })()

  const shownActive = groupedActive.filter(s =>
    !killing.has(s.id) &&
    (!statusFilter || (statusFilter === 'thinking'
      ? (s.status === 'thinking' || s.status === 'running')
      : s.status === statusFilter)) &&
    (controlFilter === 'all' || s.control === controlFilter ||
      // A session still starting will become managed, so keep it in that view.
      (controlFilter === 'managed' && s.control === 'starting') ||
      // Group cards have no single control value — show in all views.
      s._isGroupCard))

  // A working agent needs nothing from you, so it does not deserve the same
  // real estate as one that is stopped waiting. Cards for what needs you; one
  // line each for what does not.
  const { needsYou, working } = partitionByAttention(shownActive, heldBySession)
  const visibleSessionIds = shownActive.filter(s => s.id && !s.nonce).map(s => s.id)

  // Dock badge — update when needsYou count changes
  const prevBadgeRef = useRef(-1)
  useEffect(() => {
    const count = needsYou.length
    if (count === prevBadgeRef.current) return
    prevBadgeRef.current = count
    fetch('/api/badge', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count }) }).catch(() => {})
  }, [needsYou.length])

  return (
    <div className="app">
      <header className="header">
        <h1><img src="/app/icon-32.png" alt="" className="header-logo" /><span className="header-wordmark">Quarterdeck</span></h1>
        <div className="header-stats">
          {[
            ['thinking', 'thinking', 'stat-running', s => s.status === 'thinking' || s.status === 'running'],
            ['awaiting-approval', 'waiting', 'stat-waiting', s => s.status === 'awaiting-approval'],
            ['idle', 'idle', 'stat-idle', s => s.status === 'idle'],
          ].map(([key, label, cls, pred]) => (
            <button key={key}
                    className={`stat ${cls} stat-btn ${statusFilter === key ? 'stat-on' : ''}`}
                    title={statusFilter === key ? 'Show all statuses' : `Show only ${label}`}
                    onClick={() => {
                      changeView('active')
                      changeStatusFilter(statusFilter === key ? null : key)
                    }}>
              {active.filter(pred).length} {label}
            </button>
          ))}
        </div>
        {/* Outside the badge strip, not pinned inside it. On a phone the badges
            scroll sideways, and these used to ride along as sticky elements
            offset by a hardcoded 100px — which is not the Snapshot button's
            width (76px), and changes again to "⟳ Saving…" mid-click. So `+` sat
            on top of the idle badge. A sibling that never scrolls cannot drift. */}
        <div className="header-actions">
          <button className="new-btn" onClick={() => setLauncherOpen(v => !v)} title="Launch a new session (⌘↩)">
            {launcherOpen ? '×' : '+'}
          </button>
          <button className="scan-btn" onClick={takeSnapshot} disabled={scanning}>
            {scanning ? '⟳ Saving…' : '⊙ Snapshot'}
          </button>
          <ProfilePill visibleSessionIds={visibleSessionIds}
                       onProfileSwitch={() => settingsApi.getOptions().then(setOptions).catch(() => {})}
                       onCurrentProfile={setActiveProfileName} />
          <button className={`gear-btn ${view === 'settings' ? 'active' : ''}`}
                  onClick={() => changeView(view === 'settings' ? 'active' : 'settings')}
                  title="Settings">⚙</button>
        </div>
      </header>

      <div className="view-tabs">
        <button className={`view-tab ${view === 'active' ? 'active' : ''}`} onClick={() => changeView('active')}>
          Active ({active.length})
        </button>
        <button className={`view-tab ${view === 'collections' ? 'active' : ''}`} onClick={() => { changeView('collections'); if (!projectsData) loadProjects() }}>
          Collections
        </button>
        <button className={`view-tab ${view === 'stats' ? 'active' : ''}`} onClick={() => { changeView('stats'); if (!stats) loadStats(); if (!allCorrections) fetch('/api/corrections').then(r=>r.json()).then(d=>setAllCorrections(d.corrections||[])).catch(()=>{}) }}>
          Stats
        </button>
        <button className={`view-tab ${view === 'stacks' ? 'active' : ''}`} onClick={() => changeView('stacks')}>
          Stacks
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {buildHealth?.stale && (
        <div className="stale-build-banner">
          <span className="stale-build-icon">⚠</span>
          <span className="stale-build-msg">
            Running build is behind source
            {buildHealth.changed_files?.length > 0 && (
              <> — {buildHealth.changed_files.length} file{buildHealth.changed_files.length > 1 ? 's' : ''} changed
                <span className="stale-build-files"> ({buildHealth.changed_files.join(', ')})</span>
              </>
            )}
          </span>
          <button
            className="stale-build-action"
            onClick={() => changeView('settings')}
          >
            Rebuild →
          </button>
        </div>
      )}

      {pendingApprovals.length > 0 && (
        <div className="approval-banner">
          <div className="approval-banner-header">
            <span className="approval-banner-title">
              🔧 {pendingApprovals.length} tool call{pendingApprovals.length > 1 ? 's' : ''} waiting for approval
            </span>
            <button
              className="approval-dismiss-all"
              onClick={dismissAll}
              title="Deny all and clear banner"
            >
              Dismiss all
            </button>
          </div>
          {pendingApprovals.map(a => {
            const session = sessions.find(s => s.id === a.session_id)
            const sessionTitle = session?.title || a.session_id.slice(0, 8)
            const ageSec = Math.round(a.age || 0)
            // Extract a short preview from tool_input
            let inputPreview = ''
            if (a.tool_input && typeof a.tool_input === 'object') {
              const keys = Object.keys(a.tool_input)
              if (keys.length > 0) {
                const val = a.tool_input[keys[0]]
                const str = typeof val === 'string' ? val : JSON.stringify(val)
                inputPreview = str.length > 80 ? str.slice(0, 80) + '…' : str
              }
            }
            return (
              <div key={a.request_id} className="approval-item">
                <div className="approval-item-row">
                  <span className="approval-tool">
                    🔧 <strong>{a.tool_name && a.tool_name !== 'unknown' ? a.tool_name : 'tool call'}</strong>
                  </span>
                  <span className="approval-session">{sessionTitle}</span>
                  {ageSec > 0 && <span className="approval-age">{ageSec}s</span>}
                  <button className="approval-allow" onClick={() => respondApproval(a.session_id, a.request_id, true)}>Allow</button>
                  <button className="approval-deny" onClick={() => respondApproval(a.session_id, a.request_id, false)}>Deny</button>
                </div>
                {inputPreview && <div className="approval-input" title={JSON.stringify(a.tool_input)}>{inputPreview}</div>}
              </div>
            )
          })}
        </div>
      )}

      <div className={`main-layout${focusMode ? ' focus-mode' : ''}`}>
        <div className="grid">
          {view === 'active' && (
            <>
              {/* Filter + view toolbar — one bar that holds control filter,
                  status filter chips, and the three view-mode buttons. */}
              <div className="active-toolbar">
                <div className="active-toolbar-filters">
                  {/* Control filter */}
                  {[
                    ['all', 'All'],
                    ['managed', 'Managed'],
                    ['foreign', 'Foreign'],
                    ['crew', 'Crew'],
                  ].map(([key, label]) => {
                    const n = key === 'all' ? active.length : active.filter(s => s.control === key).length
                    return (
                      <button key={key}
                              className={`control-filter-btn ${controlFilter === key ? 'active' : ''}`}
                              onClick={() => changeControlFilter(key)}>
                        {label} <span className="control-filter-count">{n}</span>
                      </button>
                    )
                  })}
                  {/* Status chips */}
                  <span className="toolbar-divider" />
                  {[
                    ['thinking', '⟳ Thinking', active.filter(s => s.status === 'thinking' || s.status === 'running').length],
                    ['awaiting-approval', '⏸ Waiting', active.filter(s => s.status === 'awaiting-approval').length],
                    ['idle', '· Idle', active.filter(s => s.status === 'idle').length],
                  ].map(([key, label, n]) => n > 0 && (
                    <button key={key}
                            className={`status-chip ${statusFilter === key ? 'active' : ''}`}
                            onClick={() => changeStatusFilter(statusFilter === key ? null : key)}>
                      {label} <span className="control-filter-count">{n}</span>
                    </button>
                  ))}
                </div>
                {/* View mode switcher */}
                <div className="view-mode-btns">
                  <button className={`view-mode-btn ${sessionViewMode === 'cards' ? 'active' : ''}`}
                          title="Card view" onClick={() => changeSessionViewMode('cards')}>⊞</button>
                  <button className={`view-mode-btn ${sessionViewMode === 'list' ? 'active' : ''}`}
                          title="List view" onClick={() => changeSessionViewMode('list')}>☰</button>
                  <button className={`view-mode-btn ${sessionViewMode === 'wall' ? 'active' : ''}`}
                          title="Wall / ambient view — read-only, second monitor" onClick={() => changeSessionViewMode('wall')}>⬚</button>
                </div>
              </div>
              {/* One prompt box at a time. The launcher is the quick line with
                  its options unfolded — showing both left two "what should the
                  agent do" fields on screen, only one of which was listening. */}
              {launcherOpen ? (
                <NewSessionLauncher options={options} onDispatch={handleDispatch} onCancel={() => setLauncherOpen(false)} />
              ) : (
                <QuickCreate onDispatch={handleDispatch} suggestion={cwdSuggestion} sessions={sessions} />
              )}
              {/* Focus mode: launcher is hidden but work still needs to start.
                  Clicking + exits focus mode and opens the launcher normally. */}
              <button
                className="focus-launch-btn"
                onClick={() => { setFocusMode(false); setLauncherOpen(true) }}
                title="New session (⌘↩)"
              >+</button>

              {/* List view — flat compact rows */}
              {sessionViewMode === 'list' && shownActive.length > 0 && (
                <ListView
                  needsYou={needsYou}
                  working={working}
                  selected={selected}
                  onSelect={selectSession}
                  onOpenFull={openFull}
                  onKill={handleKillSession}
                  onCancelPending={handleCancelPending}
                  onTakeover={handleTakeover}
                  killing={killing}
                  heldBySession={heldBySession}
                  onRespondApproval={respondApproval}
                />
              )}

              {/* Cards view (default) — needs-you / working split */}
              {sessionViewMode === 'cards' && (
                <>
                  {needsYou.length > 0 && (
                    <>
                      {!focusMode && (
                        <div className="section-head">
                          <span className="section-title">Needs you</span>
                          <span className="section-count">{needsYou.length}</span>
                        </div>
                      )}
                      {focusMode ? (
                        <ul className="working-list">
                          {needsYou.map(({ s, a }) => (
                            <li key={s.id} className={`working-row working-row-needs ${selected?.id === s.id ? 'working-selected' : ''}`}
                                onClick={() => selectSession(s)}>
                              <span className="working-spinner" style={{color:'var(--accent)'}}>◉</span>
                              <span className="working-name" title={s.title || ''}>{s.name}</span>
                              <span className="working-state">{a.action}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <div className="cards">
                          {needsYou.map(({ s, a }) => <SessionCard key={s.id} session={s} attention={a} onClick={selectSession} onOpenFull={openFull} isSelected={selected?.id === s.id} onKill={handleKillSession} onRestart={handleRestartSession} onCancelPending={handleCancelPending} onTakeover={handleTakeover} onAck={handleAckSession} notified={notifiedIds.has(s.id)} acked={ackedIds.has(s.id)} ending={killing.has(s.id)} held={heldBySession.get(s.id)} onRespondApproval={respondApproval} onRespondPrompt={respondPrompt} onSendText={sendToSession} onCorrect={handleCorrectSession} />)}
                        </div>
                      )}
                    </>
                  )}
                  {working.length > 0 && (
                    <>
                      <button className="section-head section-toggle"
                              onClick={() => changeShowWorking(!showWorking)}
                              title={showWorking ? 'Collapse' : 'Show what is running'}>
                        <span className="section-title">Working</span>
                        <span className="section-count">{working.length}</span>
                        <span className="section-chevron">{showWorking ? '▾' : '▸'}</span>
                      </button>
                      {showWorking && (
                        <ul className="working-list">
                          {working.map(({ s, a }) => (
                            <li key={s.id} className={`working-row ${selected?.id === s.id ? 'working-selected' : ''}`}
                                onClick={() => s.control !== 'starting' && selectSession(s)}
                                onDoubleClick={() => s.control !== 'starting' && openFull(s)}>
                              <span className="working-spinner" aria-hidden="true">⟳</span>
                              <span className="working-name" title={s.title || ''}>{s.name}</span>
                              {s.gated && <span className="card-gated" title="Tool calls held for approval">🔒</span>}
                              <span className="working-folder">{s.folder || showPath(s)}</span>
                              <span className="working-state">{a.action}</span>
                              <span className="working-time">{timeAgo(s.updated_at)}</span>
                              {s.nonce ? (
                                <button className="card-kill" title="Give up on this spawn"
                                        onClick={(e) => { e.stopPropagation(); handleCancelPending(s.nonce) }}>×</button>
                              ) : (
                                <button className="card-kill" disabled={killing.has(s.id)}
                                        title="End session (asks it to quit cleanly)"
                                        onClick={(e) => handleKillSession(s.id, e)}>
                                  {killing.has(s.id) ? '⟳' : '×'}
                                </button>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                </>
              )}

              {shownActive.length === 0 && (
                <div className="empty">
                  {active.length === 0
                    ? <>No active sessions. Press <strong>+</strong> to launch one, or resume from Snapshots.</>
                    : <>No {controlFilter} sessions. <button className="link-btn" onClick={() => changeControlFilter('all')}>Show all {active.length}</button></>}
                </div>
              )}
              {shownActive.length > 0 && sessionViewMode === 'cards' && needsYou.length === 0 && (
                <div className="empty empty-calm">
                  Nothing needs you. {working.length} agent{working.length === 1 ? '' : 's'} working.
                </div>
              )}
            </>
          )}
          {view === 'collections' && (
            <CollectionsPanel
              archiveQuery={archiveQuery}
              archiveResults={archiveResults}
              archiveTotal={archiveTotal}
              archiveSelected={archiveSelected}
              onArchiveSearch={handleArchiveSearch}
              onToggleArchiveSelect={handleToggleArchiveSelect}
              onBatchDelete={handleBatchDelete}
              onDeleteArchive={handleDeleteArchive}
              onRenameArchive={handleRenameArchive}
              favourites={favourites}
              onToggleFavourite={handleToggleFavourite}
              onLaunchFavourite={handleLaunchFavourite}
              sessions={sessions}
              snapshots={snapshots}
              onRestoreAll={handleRestoreAll}
              onDeleteSnapshot={handleDeleteSnapshot}
              restoring={restoring}
              projectsData={projectsData}
              projectsLoading={projectsLoading}
              projectsError={projectsError}
              onLoadProjects={loadProjects}
              expandedProjects={expandedProjects}
              onToggleProject={handleToggleProject}
              onDeleteProject={handleDeleteProject}
              selected={selected}
              onSelectSession={selectSession}
              source={collectionSource}
              onChangeSource={changeCollectionSource}
            />
          )}          {view === 'stats' && stats && (
            <div className="stats-panel">
              <div className="stats-period-bar">
                {['7d', '30d', '90d', 'all'].map(p => (
                  <button key={p} className={`stats-period-btn ${statsPeriod === p ? 'active' : ''}`} onClick={() => { setStatsPeriod(p); loadStats(p) }}>{p === 'all' ? 'All Time' : p === '7d' ? '7 Days' : p === '30d' ? '30 Days' : '90 Days'}</button>
                ))}
                <button className={`stats-period-btn ${statsPeriod === 'custom' ? 'active' : ''}`} onClick={() => setStatsPeriod('custom')}>Custom</button>
                {statsPeriod === 'custom' && (
                  <span className="stats-date-range">
                    <input type="date" value={statsFrom} onChange={(e) => setStatsFrom(e.target.value)} />
                    <span>→</span>
                    <input type="date" value={statsTo} onChange={(e) => setStatsTo(e.target.value)} />
                    <button className="stats-period-btn active" onClick={() => loadStats('custom')}>Go</button>
                  </span>
                )}
              </div>

              <div className="stats-grid">
                <div className="stat-card">
                  <div className="stat-value">{stats.total_sessions}</div>
                  <div className="stat-label">Total Sessions</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{stats.avg_duration_min}m</div>
                  <div className="stat-label">Avg Duration</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{stats.messages_sampled}</div>
                  <div className="stat-label">Messages (recent 100)</div>
                </div>
              </div>

              <div className="stats-section">
                <h3 className="stats-title">Top Projects (by messages)</h3>
                <div className="stats-bars">
                  {stats.top_projects.map((p) => (
                    <div key={p.name} className="stats-bar-row clickable" onClick={() => { if (p.cwd) settingsApi.openFolder(p.cwd) }}>
                      <span className="stats-bar-label" title={p.cwd}>{p.name}</span>
                      <div className="stats-bar-track">
                        <div className="stats-bar-fill" style={{ width: `${(p.messages / stats.top_projects[0].messages) * 100}%` }}></div>
                      </div>
                      <span className="stats-bar-count">{p.messages} msg / {p.sessions} sess</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="stats-section">
                <h3 className="stats-title">Monthly Activity
                  <span className="stats-toggle">
                    <button className={`stats-toggle-btn ${!statsShowMessages ? 'active' : ''}`} onClick={() => setStatsShowMessages(false)}>Sessions</button>
                    <button className={`stats-toggle-btn ${statsShowMessages ? 'active' : ''}`} onClick={() => setStatsShowMessages(true)}>Messages</button>
                  </span>
                </h3>
                <div className="stats-bars">
                  {(statsShowMessages ? (stats.monthly_messages || []) : stats.monthly_activity).map(([month, count]) => (
                    <div key={month} className="stats-bar-row">
                      <span className="stats-bar-label">{month}</span>
                      <div className="stats-bar-track">
                        <div className="stats-bar-fill monthly" style={{ width: `${(count / Math.max(...(statsShowMessages ? (stats.monthly_messages || []) : stats.monthly_activity).map(m => m[1]), 1)) * 100}%` }}></div>
                      </div>
                      <span className="stats-bar-count">{count}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="stats-section">
                <h3 className="stats-title">Weekday Activity</h3>
                <div className="stats-weekdays">
                  {stats.weekday_activity.map(([day, count]) => (
                    <div key={day} className="stats-weekday">
                      <div className="stats-weekday-bar" style={{ height: `${(count / Math.max(...stats.weekday_activity.map(d => d[1]))) * 60}px` }}></div>
                      <span className="stats-weekday-label">{day}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="stats-section">
                <h3 className="stats-title">Longest Sessions</h3>
                <div className="stats-list">
                  {stats.longest_sessions.map((s) => (
                    <div key={s.id} className="stats-list-row" onClick={() => selectSession({ id: s.id, title: s.title, cwd: s.cwd, cwd_display: s.cwd_display, status: 'done' })}>
                      <span className="stats-list-duration">{s.duration_min >= 60 ? `${Math.floor(s.duration_min/60)}h${s.duration_min%60}m` : `${s.duration_min}m`}</span>
                      <span className="stats-list-title">{s.title}</span>
                      <span className="stats-list-cwd" title={s.cwd}>{showPath(s)}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="stats-section">
                <h3 className="stats-title">Top Tools (recent 100 sessions)</h3>
                <div className="stats-bars">
                  {stats.top_tools.map(([name, count]) => (
                    <div key={name} className="stats-bar-row">
                      <span className="stats-bar-label">{name}</span>
                      <div className="stats-bar-track">
                        <div className="stats-bar-fill tools" style={{ width: `${(count / stats.top_tools[0][1]) * 100}%` }}></div>
                      </div>
                      <span className="stats-bar-count">{count}</span>
                    </div>
                  ))}
                </div>
              </div>

              {stats.empty_sessions.length > 0 && (
                <div className="stats-section">
                  <h3 className="stats-title">Empty Sessions ({stats.empty_sessions.length}) <button className="stats-delete-all" onClick={async () => { if (await askConfirm('Delete empty sessions?', `${stats.empty_sessions.length} sessions will be deleted.`, 'Delete')) { Promise.all(stats.empty_sessions.map(s => deleteOneSession(s.id))).then(() => loadStats()) } }}>Delete All</button></h3>
                  <div className="stats-list">
                    {stats.empty_sessions.map((s) => (
                      <div key={s.id} className="stats-list-row">
                        <span className="stats-list-date">{s.created_at}</span>
                        <span className="stats-list-title">{s.title}</span>
                        <button className="archive-delete" onClick={() => { deleteOneSession(s.id).then(() => loadStats()) }}>×</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {allCorrections && allCorrections.length > 0 && (
                <div className="stats-section">
                  <h3 className="stats-title">⚑ Corrections ({allCorrections.filter(c=>c.status==='confirmed').length} confirmed)</h3>
                  <div className="corrections-dashboard">
                    {allCorrections.filter(c => c.status === 'confirmed').map(c => (
                      <div key={c.id} className="corrections-dash-row">
                        <div className="corrections-dash-meta">
                          <span className="correction-time">{new Date(c.ts * 1000).toLocaleString()}</span>
                          <span className="correction-seq" title="Message sequence number">seq {c.last_message_seq ?? '?'}</span>
                          <span className="correction-session" title={c.session_id}>{c.session_id.slice(0,8)}</span>
                        </div>
                        {c.note && <div className="corrections-dash-note">📝 {c.note}</div>}
                        {c.assistant_message && (
                          <details className="corrections-dash-msg">
                            <summary>What the agent said</summary>
                            <div className="corrections-dash-msg-body">{c.assistant_message.slice(0, 500)}{c.assistant_message.length > 500 ? '…' : ''}</div>
                          </details>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="stats-section cleanup-section">
                <h3 className="stats-title">🧹 Cleanup
                  <button className="stats-scan-btn" onClick={loadCleanupPreview} disabled={cleanupLoading}>
                    {cleanupLoading ? '⟳ Scanning…' : 'Scan for Zombies'}
                  </button>
                </h3>
                {cleanupPreview && (
                  <div className="cleanup-results">
                    <div className="cleanup-summary">
                      <span className="cleanup-stat">🧟 {cleanupPreview.summary.zombie_count} one-shot zombies</span>
                      <span className="cleanup-stat">💤 {cleanupPreview.summary.stale_count} stale (idle &gt;24h)</span>
                      {cleanupPreview.summary.total_cleanable > 0 && (
                        <button className="stats-delete-all" onClick={() => applyCleanup([...cleanupPreview.zombies.map(z => z.id), ...cleanupPreview.stale.map(s => s.id)])}>
                          Delete All ({cleanupPreview.summary.total_cleanable})
                        </button>
                      )}
                    </div>
                    {cleanupPreview.zombies.length > 0 && (
                      <div className="cleanup-group">
                        <h4 className="cleanup-group-title">One-shot sessions (0-1 turns, &lt;5 min)
                          <button className="stats-delete-all small" onClick={() => applyCleanup(cleanupPreview.zombies.map(z => z.id))}>Delete {cleanupPreview.zombies.length}</button>
                        </h4>
                        <div className="stats-list">
                          {cleanupPreview.zombies.slice(0, 10).map((z) => (
                            <div key={z.id} className="stats-list-row">
                              <span className="stats-list-date">{z.created_at}</span>
                              <span className="stats-list-title">{z.title}</span>
                              <span className="stats-list-meta">{z.turns} turns, {z.duration_min}m</span>
                              <button className="archive-delete" onClick={() => applyCleanup([z.id])}>×</button>
                            </div>
                          ))}
                          {cleanupPreview.zombies.length > 10 && (
                            <div className="stats-list-more">...and {cleanupPreview.zombies.length - 10} more</div>
                          )}
                        </div>
                      </div>
                    )}
                    {cleanupPreview.stale.length > 0 && (
                      <div className="cleanup-group">
                        <h4 className="cleanup-group-title">Stale sessions (idle &gt;24h)
                          <button className="stats-delete-all small" onClick={() => applyCleanup(cleanupPreview.stale.map(s => s.id))}>Delete {cleanupPreview.stale.length}</button>
                        </h4>
                        <div className="stats-list">
                          {cleanupPreview.stale.slice(0, 10).map((s) => (
                            <div key={s.id} className="stats-list-row">
                              <span className="stats-list-duration">{Math.round(s.hours_idle)}h idle</span>
                              <span className="stats-list-title">{s.title}</span>
                              <span className="stats-list-meta">{s.turns} turns</span>
                              <button className="archive-delete" onClick={() => applyCleanup([s.id])}>×</button>
                            </div>
                          ))}
                          {cleanupPreview.stale.length > 10 && (
                            <div className="stats-list-more">...and {cleanupPreview.stale.length - 10} more</div>
                          )}
                        </div>
                      </div>
                    )}
                    {cleanupPreview.summary.total_cleanable === 0 && (
                      <div className="cleanup-empty">✓ No zombie sessions found. Your session list is clean!</div>
                    )}
                  </div>
                )}
                {!cleanupPreview && (
                  <p className="cleanup-hint">Scans for sessions with 0-1 turns and &lt;5 min duration (subagent one-shots) and sessions idle for &gt;24 hours.</p>
                )}
              </div>
            </div>
          )}
          {view === 'settings' && (
            <SettingsPanel options={options} paneTheme={paneTheme} onTogglePaneTheme={togglePaneTheme} showHidden={showHidden} onChangeShowHidden={changeShowHidden} />
          )}
          {view === 'stacks' && (
            <div className="stacks-panel">
              <StacksView onSelectSession={(s) => { selectSession(s); changeView('active') }} />
            </div>
          )}
        </div>
        <AttentionBar sessions={sessions} selectedId={selected?.id} onPick={selectSession} />
        <CommandBar open={cmdBarOpen} onClose={() => setCmdBarOpen(false)} onAction={handleCmdBarAction} onOpenSession={(id) => {
          // Find the session and open it in the detail panel
          const session = sessions.find(s => s.id === id)
          if (session) {
            selectSession(session)
          } else {
            // Session not in current list — fetch it
            api.getSession(id)
              .then(d => {
                if (!d.error) selectSession({ id, ...d })
              })
          }
        }} />
        {selected && <DetailPanel session={sessions.find(s => s.id === selected.id) || selected} onClose={() => { 
          if (returnView) { changeSessionViewMode(returnView); setReturnView(null) }
          selectSession(null); setFocusMode(false)
        }} onTakeover={handleTakeover} onResume={handleResumeSession} onRefresh={fetchSessions} onSelect={selectSession} options={options} expanded={expanded} onToggleExpand={() => setExpanded(v => !v)} focusMode={focusMode} onToggleFocus={toggleFocus} paneTheme={paneTheme} sessions={shownActive} onNewSession={() => { if (expanded) setExpanded(false); setLauncherOpen(true) }} fromWall={returnView === 'wall'} />}
        {/* Wall / ambient view overlay — big tiles, interactive, full screen */}
        {sessionViewMode === 'wall' && (() => {
          const wallSendInput = () => {
            if (!wallInput.trim() || !wallFocused) return
            api.sendInput(wallFocused.id, wallInput.trim())
            setWallInput('')
            // Keep sheet open so user can see response arrive — don't close
          }
          const focusedLive = wallFocused
            ? (shownActive.find(s => s.id === wallFocused.id) || wallFocused)
            : null
          return (
            <div className="wall-overlay">
              <div className="wall-header">
                <div className="wall-header-stats">
                  <img src="/app/icon-32.png" alt="" className="wall-logo" />
                  <span className="wall-brand">Quarterdeck</span>
                  {[
                    { key: 'thinking', label: 'thinking', filter: s => s.status === 'thinking' || s.status === 'running' },
                    { key: 'waiting', label: 'waiting', filter: s => s.status === 'awaiting-approval' },
                  ].map(({ key, label, filter }) => {
                    const count = shownActive.filter(filter).length
                    return count > 0 ? (
                      <span key={key} className={`wall-stat wall-stat-${key}`}>{count} {label}</span>
                    ) : null
                  })}
                </div>
                <div className="wall-header-actions">
                  <ProfilePill visibleSessionIds={shownActive.map(s => s.id)}
                          onProfileSwitch={() => settingsApi.getOptions().then(setOptions).catch(() => {})}
                          onCurrentProfile={setActiveProfileName} />
                  <button className={`wall-group-toggle${wallGrouped ? ' active' : ''}`}
                          title={wallGrouped ? 'Flat grid' : 'Group by collection'}
                          onClick={() => {
                            const v = !wallGrouped
                            setWallGrouped(v)
                            localStorage.setItem('wall-grouped', v ? '1' : '0')
                            if (v) {
                              fetch('/api/collections').then(r => r.json())
                                .then(d => setWallCollections((d.collections || []).filter(c => c.source !== 'snapshot')))
                                .catch(() => {})
                            }
                          }}>
                    {wallGrouped ? '⊟' : '⊞'} {wallGrouped ? 'Flat' : 'Board'}
                  </button>
                  <button className="wall-new" title="New session (⌘↩)"
                          onClick={() => setLauncherOpen(true)}>+ <span className="wall-new-label">New session</span></button>
                  <button className="wall-exit" title="Exit wall view"
                          onClick={() => changeSessionViewMode('cards')}>⊞ Grid</button>
                </div>
              </div>
              {launcherOpen && (
                <div className="wall-launcher-wrap">
                  <NewSessionLauncher options={options} onDispatch={handleDispatch} onCancel={() => setLauncherOpen(false)} />
                </div>
              )}
              {shownActive.length === 0 ? (
                <div className="wall-empty">No active sessions</div>
              ) : wallGrouped ? (() => {
                // Group by collection; sessions in no collection go to "Other"
                const sessionIdSet = new Set(shownActive.map(s => s.id))
                // Build column list: one per non-snapshot collection, plus "Other"
                const colMap = new Map() // colId -> { name, sessions: [] }
                for (const c of wallCollections) {
                  colMap.set(c.id, { name: c.name, sessions: [] })
                }
                const assigned = new Set()
                for (const c of wallCollections) {
                  for (const m of (c.members || [])) {
                    const sid = m.session_id || m.id
                    if (sid && sessionIdSet.has(sid)) {
                      colMap.get(c.id).sessions.push(shownActive.find(s => s.id === sid))
                      assigned.add(sid)
                    }
                  }
                }
                // Sessions not in any collection → "Other" column
                const unassigned = shownActive.filter(s => !assigned.has(s.id))
                const columns = [...colMap.values()].filter(c => c.sessions.length > 0)
                if (unassigned.length > 0) columns.push({ name: 'Other', sessions: unassigned })
                // Fallback: no collections defined → group by folder
                const fallback = columns.length === 0
                if (fallback) {
                  const seen = new Map()
                  for (const s of shownActive) {
                    const key = s.folder || s.cwd || '—'
                    if (!seen.has(key)) seen.set(key, [])
                    seen.get(key).push(s)
                  }
                  for (const [name, sessions] of seen) columns.push({ name, sessions })
                }
                const makeTile = s => {
                  const cfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.idle
                  const isLive = s.status === 'thinking' || s.status === 'running'
                  const isWaiting = s.status === 'awaiting-approval'
                  return (
                    <WallTile key={s.id} s={s} cfg={cfg} isLive={isLive} isWaiting={isWaiting}
                              focused={wallFocused?.id === s.id}
                              onFocus={() => {
                                if (window.innerWidth <= 768) {
                                  setWallFocused(s); setWallOutput('')
                                  api.getSession(s.id).then(d => {
                                    const last = d.output ? [...d.output].reverse().find(e => e.type === 'assistant' || e.role === 'assistant') : null
                                    setWallOutput(last?.text || s.last_message || s.last_output || '')
                                  }).catch(() => {})
                                } else { setReturnView('wall'); changeSessionViewMode('cards'); selectSession(s); setExpanded(true) }
                              }}
                              held={heldBySession.get(s.id)}
                              onRespondApproval={respondApproval}
                              onRestart={e => { e.stopPropagation(); handleRestartSession(s.id, e) }}
                              onKill={e => { e && e.stopPropagation(); killSessionDirect(s.id) }}
                              onOpenFull={s => { setReturnView('wall'); changeSessionViewMode('cards'); selectSession(s); setExpanded(true) }} />
                  )
                }
                return (
                  <div className="wall-board">
                    {columns.map(col => (
                      <div key={col.name} className="wall-board-col">
                        <div className="wall-board-col-header">
                          <span className="wall-board-col-name">{col.name}</span>
                          <span className="wall-board-col-count">{col.sessions.length}</span>
                        </div>
                        <div className="wall-board-col-tiles">
                          {col.sessions.map(makeTile)}
                        </div>
                      </div>
                    ))}
                  </div>
                )
              })() : (
                <div className="wall-grid">
                  {shownActive.map(s => {
                    const cfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.idle
                    const isLive = s.status === 'thinking' || s.status === 'running'
                    const isWaiting = s.status === 'awaiting-approval'
                    return (
                      <WallTile key={s.id} s={s} cfg={cfg} isLive={isLive} isWaiting={isWaiting}
                                focused={wallFocused?.id === s.id}
                                onFocus={() => {
                                  if (window.innerWidth <= 768) {
                                    // Mobile: open bottom sheet
                                    setWallFocused(s)
                                    setWallOutput('')
                                    api.getSession(s.id).then(d => {
                                      const last = d.output
                                        ? [...d.output].reverse().find(e => e.type === 'assistant' || e.role === 'assistant')
                                        : null
                                      setWallOutput(last?.text || s.last_message || s.last_output || '')
                                    }).catch(() => {})
                                  } else {
                                    setReturnView('wall')
                                    changeSessionViewMode('cards')
                                    selectSession(s)
                                    setExpanded(true)
                                  }
                                }}
                                onRestart={e => { e.stopPropagation(); handleRestartSession(s.id, e) }}
                                onKill={e => { e && e.stopPropagation(); killSessionDirect(s.id) }}
                                onOpenFull={s => {
                                  setReturnView('wall')
                                  changeSessionViewMode('cards')
                                  selectSession(s)
                                  setExpanded(true)
                                }}
                                held={heldBySession.get(s.id)}
                                onRespondApproval={respondApproval} />
                    )
                  })}
                </div>
              )}
              {focusedLive && (
                <div className="wall-sheet-backdrop" onClick={() => { setWallFocused(null); setWallOutput('') }}>
                  <div className="wall-sheet" onClick={e => e.stopPropagation()}>
                    <div className="wall-sheet-header">
                      <span className="wall-sheet-name">{wallFocused.name || wallFocused.folder}</span>
                      <button className="wall-sheet-close" onClick={() => { setWallFocused(null); setWallOutput('') }}>✕</button>
                    </div>
                    {wallOutput ? (
                      <div className="wall-sheet-message"><Markdown text={wallOutput} /></div>
                    ) : (
                      <div className="wall-sheet-empty">Loading…</div>
                    )}
                    <div className="wall-sheet-input-row">
                      <textarea
                        ref={wallInputRef}
                        className="wall-sheet-input"
                        placeholder="Reply…"
                        value={wallInput}
                        rows={2}
                        onChange={e => setWallInput(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); wallSendInput() }
                          if (e.key === 'Escape') { setWallFocused(null); setWallOutput('') }
                        }}
                      />
                      <button className="wall-sheet-send" disabled={!wallInput.trim()} onClick={wallSendInput}>↵</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )
        })()}
      </div>
    </div>
  )
}

