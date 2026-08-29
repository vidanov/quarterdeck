import React, { useState, useEffect, useRef, useCallback } from 'react'
import { errorOf } from '../api/client'
import * as settingsApi from '../api/settings'
import * as profilesApi from '../api/profiles'
import * as denyApi from '../api/denyPatterns'
import * as secretsApi from '../api/secrets'
import * as scriptsApi from '../api/scripts'
import * as sessionsApi from '../api/sessions'
import { useToast } from '../state/ToastContext'
import { useConfirm } from '../state/ConfirmContext'

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
  }
  const el = document.createElement('textarea')
  el.value = text
  el.style.position = 'fixed'
  el.style.left = '-9999px'
  document.body.appendChild(el)
  el.select()
  document.execCommand('copy')
  document.body.removeChild(el)
}

const PANE_MIN_COLS = 20
const PANE_MIN_ROWS = 6
const CELL_SAMPLE = 40

const ROUTE_LABELS = {
  lineage: 'inspecting the process tree',
  'hook-confirmed': 'the hook, agreeing',
  'hook-corrected': 'the hook, correcting a wrong guess',
  unknown: 'started before this was recorded',
}

function RemoteSettings() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [status, setStatus] = useState(null)
  const [tokenInfo, setTokenInfo] = useState(null)
  const [showQR, setShowQR] = useState(false)
  const [busy, setBusy] = useState('')
  // The token is the one thing here that is a credential, so it is masked until
  // asked for. The QR beside it is not: it carries a single-use code that
  // expires in two minutes, never the token — see /api/remote/token. So the QR
  // can be filmed or screen-shared and this line cannot, and until now they
  // were on screen together with no way to show one without the other.
  const [revealToken, setRevealToken] = useState(false)
  // Addresses are not secret — a 100.x tailnet address is unroutable from
  // anywhere but your own tailnet. What they carry is identity: a MagicDNS name
  // is derived from the account that owns the tailnet. One switch for when the
  // screen is being recorded.
  const [hideAddresses, setHideAddresses] = useState(false)
  const mask = (on, text) => on
    ? <span className="redacted" title="Hidden while addresses are hidden">{text}</span>
    : text

  const loadStatus = () =>
    settingsApi.getRemoteStatus().then(setStatus).catch(() => {})

  useEffect(() => { loadStatus() }, [])

  const act = async (action) => {
    setBusy(action)
    try {
      const d = await settingsApi.remoteAction(action)
      if (d.error) { notify(d.error, 'error'); return }
      notify(action === 'start' ? `Remote started — ${d.url}` :
             action === 'stop' ? 'Remote stopped' :
             action === 'rotate' ? 'Token rotated — existing sessions are logged out' : 'Done', 'info')
      loadStatus()
      setTokenInfo(null)
      setShowQR(false)
    } catch (e) { notify(e?.message || 'Backend unreachable', 'error') }
    finally { setBusy('') }
  }

  const loadToken = () => {
    settingsApi.getRemoteToken().then(d => {
      setTokenInfo(d)
      setShowQR(true)
      // Reopening mints a fresh QR code, and starts the token hidden again —
      // revealing it once should not leave it revealed for the next time.
      setRevealToken(false)
    }).catch(() => notify('Could not load token', 'error'))
  }

  const installLA = async () => {
    setBusy('launchagent')
    const d = await settingsApi.installLaunchAgent()
    if (d.error) notify(d.error, 'error')
    else notify('LaunchAgent installed — remote will start on next login', 'info')
    setBusy('')
    loadStatus()
  }

  const uninstallLA = async () => {
    const ok = await askConfirm('Remove LaunchAgent?',
      'Remote serving will no longer start automatically after a reboot.', 'Remove')
    if (!ok) return
    setBusy('launchagent')
    await settingsApi.uninstallLaunchAgent()
    notify('LaunchAgent removed', 'info')
    setBusy('')
    loadStatus()
  }

  return (
    <>
      <h3 className="settings-title">Remote access</h3>
      <p className="cleanup-hint">
        Serve Quarterdeck on your Tailscale address so you can drive sessions from a phone.
        Bind is Tailscale-only — never 0.0.0.0.
      </p>

      {status && (
        <>
          <div className="settings-row">
            <span className="settings-label">Tailscale</span>
            <span className="settings-value">
              {status.tailscale_up
                ? <span style={{color:'#4ade80'}}>● {mask(hideAddresses, status.tailscale_ip)}</span>
                : <span style={{color:'#f87171'}}>✗ not connected</span>}
            </span>
          </div>
          <div className="settings-row">
            <span className="settings-label">Listener</span>
            <span className="settings-value">
              {status.running
                ? <><span style={{color:'#4ade80'}}>● running</span>{status.url && <span className="settings-detail"> — <a href={status.url} target="_blank" rel="noreferrer">{mask(hideAddresses, status.url)}</a></span>}</>
                : <span style={{color:'var(--muted)'}}>stopped</span>}
            </span>
          </div>
          <div className="settings-row">
            <span className="settings-label">Token</span>
            <span className="settings-value">
              {status.token_set ? <code className="settings-detail">{status.token_masked}</code> : 'not set'}
            </span>
          </div>
          <div className="settings-row">
            <span className="settings-label">LaunchAgent</span>
            <span className="settings-value settings-detail">
              {status.launchagent_installed
                ? (status.launchagent_loaded ? '● loaded (starts on login)' : '○ installed, not loaded')
                : 'not installed'}
            </span>
          </div>
          {status.on_battery !== undefined && (
            <div className="settings-row">
              <span className="settings-label">Reachability</span>
              <span className="settings-value settings-detail">
                {status.on_battery
                  ? <span style={{color:'#ca8a04'}}>⚠ On battery ({status.battery_percent}%) — lid-close will disconnect</span>
                  : <span style={{color:'#4ade80'}}>● On power{status.battery_percent != null ? ` (${status.battery_percent}%)` : ''}</span>}
                {status.last_remote_at && (
                  <span> · last remote request {Math.round((Date.now()/1000 - status.last_remote_at) / 60)}m ago</span>
                )}
              </span>
            </div>
          )}

          <div className="settings-actions" style={{flexWrap:'wrap', gap: 8}}>
            {!status.running
              ? <button className="dispatch-btn" disabled={!status.tailscale_up || busy==='start'} onClick={() => act('start')}>
                  {busy==='start' ? '⟳ Starting…' : '▶ Start remote'}
                </button>
              : <button className="launcher-cancel" disabled={busy==='stop'} onClick={() => act('stop')}>
                  {busy==='stop' ? '⟳ Stopping…' : '■ Stop remote'}
                </button>}
            <button className="dispatch-btn" onClick={loadToken} title="Show token + QR code">
              QR / Token
            </button>
            <button className="launcher-cancel" disabled={busy==='rotate'} onClick={() => act('rotate')}>
              {busy==='rotate' ? '⟳ Rotating…' : '⟳ Rotate token'}
            </button>
            {/* For recording and screen sharing. The addresses are not secrets,
                but they name the machine and the account it belongs to. */}
            <button className={`launcher-cancel ${hideAddresses ? 'active' : ''}`}
                    onClick={() => setHideAddresses(v => !v)}
                    title={hideAddresses
                      ? 'Addresses are hidden — click to show them'
                      : 'Blur the tailnet address and URL, for recording or screen sharing'}>
              {hideAddresses ? '🙈 Addresses hidden' : '👁 Hide addresses'}
            </button>
            {!status.launchagent_installed
              ? <button className="dispatch-btn" disabled={!status.tailscale_up || busy==='launchagent'} onClick={installLA}>
                  {busy==='launchagent' ? '⟳…' : 'Install LaunchAgent'}
                </button>
              : <button className="launcher-cancel" disabled={busy==='launchagent'} onClick={uninstallLA}>
                  Remove LaunchAgent
                </button>}
          </div>

          {showQR && tokenInfo && (
            <div className="remote-qr-panel">
              <div className="remote-qr-row" style={{justifyContent:'flex-end'}}>
                <button className="launcher-cancel" onClick={() => setShowQR(false)}>✕ Hide</button>
              </div>
              {tokenInfo.url && (
                <div className="remote-qr-row">
                  <span className="cleanup-hint" style={{flex:1, margin:0}}>
                    Open on phone: <strong>{mask(hideAddresses, tokenInfo.url)}</strong>
                  </span>
                  <button className="queue-btn" onClick={() => copyText(tokenInfo.url)}
                          title="Copy URL">Copy URL</button>
                </div>
              )}
              {/* Masked unless asked for, and copying never needs it shown:
                  the clipboard gets the real token either way. */}
              <div className="remote-qr-row">
                <code className="remote-token-full">
                  {revealToken && !hideAddresses
                    ? (tokenInfo.token || tokenInfo.token_masked)
                    : tokenInfo.token_masked}
                </code>
                <button className="queue-btn" disabled={hideAddresses}
                        onClick={() => setRevealToken(v => !v)}
                        title={revealToken
                          ? 'Hide the token again'
                          : 'Show the whole token — do not do this while recording'}>
                  {/* Not plain "Hide" — the button that closes the whole panel
                      is already called that, three rows down. */}
                  {revealToken ? 'Hide token' : 'Reveal'}
                </button>
                <button className="queue-btn" onClick={() => {
                  copyText(tokenInfo.token || '')
                  notify('Token copied', 'info')
                }} title="Copy token">Copy token</button>
              </div>
              {tokenInfo.qr_svg
                ? <div className="remote-qr">
                    {/* Safe to film: this encodes a single-use code that expires
                        in two minutes, not the token. */}
                    <p className="cleanup-hint settings-detail" style={{margin:'0 0 6px'}}>Scan to open and log in automatically.</p>
                    <div dangerouslySetInnerHTML={{__html: tokenInfo.qr_svg}} />
                  </div>
                : <p className="cleanup-hint settings-detail">
                    Install <code>qrcode</code> in the venv to generate a QR code.
                  </p>}
            </div>
          )}
        </>
      )}
    </>
  )
}

// Pane text with tappable links. `kiro-cli login` prints a URL and a code and
// waits — on a phone, a URL you cannot tap is a URL you cannot use, which would
// leave the one command this feature exists for still unrunnable from the phone.
function ShellPaneText({ text }) {
  const parts = String(text).split(/(https?:\/\/[^\s"'<>]+)/g)
  return parts.map((part, i) =>
    /^https?:\/\//.test(part)
      ? <a key={i} href={part} target="_blank" rel="noreferrer noopener">{part}</a>
      : part
  )
}

// The keys a terminal needs that text cannot express. Interrupting and ending
// input are the two that matter; the arrows are for the menus `kiro-cli login`
// puts up.
const SHELL_KEYS = [
  ['Tab', 'Tab'], ['Up', '↑'], ['Down', '↓'], ['Enter', '↵'],
  ['Escape', 'Esc'], ['C-c', '^C'], ['C-d', '^D'], ['C-l', '^L'],
]

// Why this exists, in one line: every session is spawned as a single command, so
// an interactive one that is not kiro-cli had nowhere to run.
const SHELL_COMMANDS = ['kiro-cli login', 'kiro-cli logout', 'kiro-cli whoami']

function ShellSettings() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [st, setSt] = useState(null)
  const [pane, setPane] = useState('')
  const [cwd, setCwd] = useState('~')
  const [cmd, setCmd] = useState('')
  const [busy, setBusy] = useState(false)
  const paneRef = useRef(null)
  const metricRef = useRef(null)
  const sentSizeRef = useRef({ cols: 0, rows: 0 })

  const alive = st?.alive === true

  const poll = () =>
    settingsApi.getShellPane().then(d => {
      setSt(d)
      setPane(d.pane || '')
    }).catch(() => {})

  useEffect(() => {
    poll()
    // Only while a shell exists: an idle settings tab should not be polling for
    // something that has to be opened by hand first.
    const t = setInterval(() => { if (st?.exists !== false) poll() }, 1200)
    return () => clearInterval(t)
  }, [st?.exists])

  // Keep the pane scrolled to the newest output, the way a terminal does.
  useEffect(() => {
    const box = paneRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [pane])

  // Reflow tmux to the box we are actually rendering into, so a phone gets a
  // phone-width shell rather than 120 columns wrapped twice.
  useEffect(() => {
    if (!alive) return
    const box = paneRef.current, probe = metricRef.current
    if (!box || !probe) return
    let timer
    const measure = () => {
      const cell = probe.getBoundingClientRect()
      const cw = cell.width / CELL_SAMPLE
      if (!(cw > 0) || !(cell.height > 0)) return
      const style = getComputedStyle(box)
      const usable = box.clientWidth
        - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      const cols = Math.floor(usable / cw)
      const rows = Math.max(PANE_MIN_ROWS, Math.floor(box.clientHeight / cell.height))
      if (cols < PANE_MIN_COLS) return
      const sent = sentSizeRef.current
      if (sent.cols === cols && sent.rows === rows) return
      sentSizeRef.current = { cols, rows }
      settingsApi.resizeShell(cols, rows)
        .catch(() => { sentSizeRef.current = { cols: 0, rows: 0 } })
    }
    const observer = new ResizeObserver(() => {
      clearTimeout(timer); timer = setTimeout(measure, 120)
    })
    observer.observe(box)
    measure()
    return () => { clearTimeout(timer); observer.disconnect() }
  }, [alive])

  const act = (path, body) => {
    setBusy(true)
    return settingsApi.shellAction(path, body)
      .then(d => {
        const err = errorOf(d)
        if (err) notify(err, 'error')
        poll()
        return d
      })
      .catch(() => notify('Could not reach backend', 'error'))
      .finally(() => setBusy(false))
  }

  const send = (text) => {
    if (!text.trim()) return
    setCmd('')
    // Reset the size we believe tmux has: a command that redraws can leave the
    // pane at a stale geometry otherwise.
    act('input', { text })
  }

  const closeShell = async () => {
    const ok = await askConfirm(
      'Close the shell?',
      'The tmux session is killed, and anything still running in it goes with it. A half-finished login has to be started again.',
      'Close')
    if (ok) { setPane(''); act('close') }
  }

  return (
    <>
      <h3 className="settings-title">Shell</h3>
      <p className="cleanup-hint">
        A plain login shell, for the commands that ask questions —{' '}
        <code>kiro-cli login</code> above all. Sessions are spawned as one
        command each, so there was nowhere else to run one.
      </p>
      <p className="cleanup-hint">
        It runs as you, with your PATH, on this Mac. Anything you can type in a
        terminal you can type here, including from your phone.
      </p>
      <div className="settings-row">
        <span className="settings-label">Status</span>
        <span className="settings-value">
          {st === null ? '…' : alive ? '🟢 running' : st?.exists ? '⚠️ exited' : '⚪ not running'}
          {alive && st?.cwd && <span className="settings-detail"> — {st.cwd}</span>}
        </span>
      </div>
      {!alive && (
        <div className="settings-row">
          <span className="settings-label">Start in</span>
          <span className="settings-value shell-open-row">
            <input className="shell-cwd-input" value={cwd} spellCheck={false}
                   onChange={e => setCwd(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter') act('open', { cwd }) }} />
            <button className="shell-btn" disabled={busy}
                    onClick={() => act('open', { cwd })}>
              {st?.exists ? 'Restart shell' : 'Open shell'}
            </button>
          </span>
        </div>
      )}
      {(alive || pane) && (
        <>
          <pre className="shell-pane" ref={paneRef}>
            <ShellPaneText text={pane || '…'} />
          </pre>
          {/* Off-screen ruler for the column measurement above. */}
          <pre className="live-metric" ref={metricRef} aria-hidden="true">{'M'.repeat(CELL_SAMPLE)}</pre>
        </>
      )}
      {alive && (
        <>
          <div className="shell-commands">
            {SHELL_COMMANDS.map(c => (
              <button key={c} className="composer-chip" disabled={busy}
                      onClick={() => send(c)}>{c}</button>
            ))}
          </div>
          <form className="shell-input-row"
                onSubmit={e => { e.preventDefault(); send(cmd) }}>
            <input className="shell-input" value={cmd} spellCheck={false}
                   autoCapitalize="off" autoCorrect="off"
                   placeholder="Type a command…"
                   onChange={e => setCmd(e.target.value)} />
            <button className="shell-btn" type="submit" disabled={busy || !cmd.trim()}>↩</button>
          </form>
          <div className="shell-keys">
            {SHELL_KEYS.map(([key, label]) => (
              <button key={key} className="composer-key" disabled={busy}
                      onClick={() => act('key', { key })}>{label}</button>
            ))}
            <button className="shell-btn shell-close" onClick={closeShell} disabled={busy}>
              Close
            </button>
          </div>
          <p className="cleanup-hint">
            Also reachable from a real terminal with <code>{st?.attach}</code>.
          </p>
        </>
      )}
    </>
  )
}

function ConciergeSettings({ options }) {
  const notify = useToast()
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [disabled, setDisabled] = useState(false)
  const [autoSummaryDisabled, setAutoSummaryDisabled] = useState(false)
  // Sourced from the backend, which reports the model it will actually spawn
  // with. The old version kept this in localStorage, where nothing read it —
  // so picking a model changed precisely nothing.
  const [model, setModel] = useState('auto')
  const [saved, setSaved] = useState('auto')

  const loadStatus = () =>
    settingsApi.getAssistStatus().then(d => {
      setStatus(d)
      if (d.model) { setModel(d.model); setSaved(d.model) }
    }).catch(() => {})

  useEffect(() => {
    loadStatus()
    settingsApi.getSettings().then(s => {
      setDisabled(!!s['concierge_disabled'])
      setAutoSummaryDisabled(!!s['auto_summary_disabled'])
    }).catch(() => {})
  }, [])

  const restart = () => {
    setBusy(true)
    settingsApi.restartAssist()
      .then(d => { notify(d.ok ? 'Concierge restarted' : (d.error || 'Failed'), d.ok ? 'info' : 'error'); loadStatus() })
      .catch(() => notify('Could not reach backend', 'error'))
      .finally(() => setBusy(false))
  }

  const stop = () => {
    setBusy(true)
    settingsApi.stopAssist()
      .then(d => { notify(d.ok ? 'Concierge stopped' : (d.error || 'Failed'), d.ok ? 'info' : 'error'); loadStatus() })
      .catch(() => notify('Could not reach backend', 'error'))
      .finally(() => setBusy(false))
  }

  const toggleDisabled = (off) => {
    setDisabled(off)
    settingsApi.saveSettings({ concierge_disabled: off })
      .catch(() => notify('Could not save', 'error'))
    if (off && status?.alive) stop()
  }

  const saveModel = (m) => {
    setModel(m)
    settingsApi.saveSettings({ concierge_model: m })
      .then(() => setSaved(m))
      .catch(() => notify('Could not save the model', 'error'))
  }

  const enabled = status?.alive === true
  // A model change only reaches kiro-cli on the next spawn. Say so rather than
  // letting the dropdown imply the running session has moved.
  const pendingRestart = enabled && status?.running_model
                         && status.running_model !== saved

  return (
    <>
      <h3 className="settings-title">Concierge (⌘K)</h3>
      <p className="cleanup-hint">
        The smart command bar starts a persistent kiro-cli session on first use.
        It uses real tokens when you ask it something.
      </p>
      <div className="settings-row">
        <span className="settings-label">Enabled</span>
        <span className="settings-value">
          <label className="settings-check">
            <input type="checkbox" checked={!disabled}
                   onChange={e => toggleDisabled(!e.target.checked)} />
            {disabled ? 'off — ⌘K will not start a session' : 'on'}
          </label>
        </span>
      </div>
      {!disabled && (
        <>
          <div className="settings-row">
            <span className="settings-label">Status</span>
            <span className="settings-value">
              {status === null ? '…' : enabled ? '🟢 running' : '⚪ stopped'}
              {status?.session_id && <span className="settings-detail"> — {status.session_id.slice(0, 8)}</span>}
            </span>
          </div>
          <div className="settings-row">
            <span className="settings-label">Model</span>
            <select className="launcher-select" value={model} onChange={e => saveModel(e.target.value)}>
              {(options?.models?.length ? options.models : [model]).map(m =>
                <option key={m} value={m}>{m}</option>
              )}
            </select>
          </div>
          {pendingRestart && (
            <p className="cleanup-hint">
              The running session is on <code>{status.running_model}</code>. Restart to
              move it to <code>{saved}</code>.
            </p>
          )}
          <div className="settings-actions">
            <button className="dispatch-btn" disabled={busy} onClick={restart}>
              {busy ? '⟳ Restarting…' : enabled ? 'Restart' : 'Start'}
            </button>
            {enabled && (
              <button className="launcher-cancel" disabled={busy} onClick={stop}>
                Stop
              </button>
            )}
          </div>
        </>
      )}
      <div className="settings-row" style={{marginTop: 12}}>
        <span className="settings-label">Auto summaries</span>
        <span className="settings-value">
          <label className="settings-check">
            <input type="checkbox" checked={!autoSummaryDisabled}
                   onChange={e => {
                     const off = !e.target.checked
                     setAutoSummaryDisabled(off)
                     settingsApi.saveSettings({ auto_summary_disabled: off }).catch(() => {})
                   }} />
            {autoSummaryDisabled ? 'off' : 'on — one-line context generated on stop events'}
          </label>
        </span>
      </div>
      <p className="cleanup-hint">
        When a session finishes a turn, Quarterdeck generates a one-line summary using kiro-cli.
        Shown on cards and in the detail panel. Uses tokens.
      </p>
    </>
  )
}

function AppearanceSettings({ paneTheme, onTogglePaneTheme }) {
  const [goalMax, setGoalMaxState] = useState(() => localStorage.getItem('goal-max-iterations') || '')

  const saveGoalMax = (v) => {
    setGoalMaxState(v)
    if (v && parseInt(v, 10) > 0) localStorage.setItem('goal-max-iterations', v)
    else localStorage.removeItem('goal-max-iterations')
  }

  return (
    <>
      <h3 className="settings-title">Appearance</h3>
      <div className="settings-row">
        <span className="settings-label">Pane theme</span>
        <button className="dispatch-btn" onClick={onTogglePaneTheme} style={{ width: 'auto', padding: '4px 12px' }}>
          {paneTheme === 'dark' ? '☀ Switch to light' : '☾ Switch to dark'}
        </button>
      </div>
      <h3 className="settings-title">/goal defaults</h3>
      <p className="cleanup-hint">
        Max iterations for <code>/goal</code>. When set, clicking the goal chip
        inserts <code>/goal --max N …</code> instead of <code>/goal …</code>.
        Leave blank to use kiro-cli's default.
      </p>
      <div className="settings-row">
        <span className="settings-label">--max iterations</span>
        <input
          type="number" min="1" max="100"
          className="settings-number-input"
          placeholder="default"
          value={goalMax}
          onChange={e => saveGoalMax(e.target.value)}
        />
      </div>
    </>
  )
}

function StartingFolderSettings() {
  const notify = useToast()
  const [mode, setMode] = useState('auto')
  const [fixed, setFixed] = useState('')

  useEffect(() => {
    settingsApi.getSettings().then(s => {
      setMode(s['dispatch-cwd-mode'] || 'auto')
      setFixed(s['dispatch-cwd-fixed'] || '')
    }).catch(() => {})
  }, [])

  const saveMode = (m) => {
    setMode(m)
    settingsApi.saveSettings({ 'dispatch-cwd-mode': m }).catch(() => {})
  }

  const saveFixed = (path) => {
    setFixed(path)
    settingsApi.saveSettings({ 'dispatch-cwd-fixed': path }).catch(() => {})
  }

  const pick = () => {
    settingsApi.pickFolder().then(d => {
      if (d.path) saveFixed(d.path)
    }).catch(() => {})
  }

  return (
    <>
      <h3 className="settings-title">Starting folder</h3>
      <p className="cleanup-hint">
        Which folder a new session opens in when you don't pick one manually.
      </p>
      <div className="settings-row">
        <span className="settings-label">Default folder</span>
        <select
          className="settings-select"
          value={mode}
          onChange={e => saveMode(e.target.value)}
        >
          <option value="auto">Auto — frontmost Finder window</option>
          <option value="last">Last — folder used in the previous session</option>
          <option value="fixed">Fixed — always use a specific folder</option>
        </select>
      </div>
      {mode === 'fixed' && (
        <div className="settings-row">
          <span className="settings-label">Folder</span>
          <input
            className="settings-text-input"
            placeholder="~/Documents/Projects"
            value={fixed}
            onChange={e => setFixed(e.target.value)}
            onBlur={() => saveFixed(fixed)}
          />
          <button className="settings-pick-btn" onClick={pick} title="Pick folder">…</button>
        </div>
      )}
    </>
  )
}

function ScreenshotsSettings() {
  const notify = useToast()
  const [status, setStatus] = useState(null)
  const [folder, setFolder] = useState('')

  useEffect(() => {
    settingsApi.getSettings().then(s => {
      setFolder(s['screenshots_folder'] || '')
    }).catch(() => {})
    // Also load watcher status
    fetch('/api/screenshots/status').then(r => r.json())
      .then(setStatus).catch(() => {})
  }, [])

  const save = (path) => {
    setFolder(path)
    fetch('/api/screenshots/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    }).then(r => r.json())
      .then(d => {
        if (d.error) notify(d.error, 'error')
        else notify(path ? `Watching ${path}` : 'Watcher stopped', 'info')
        fetch('/api/screenshots/status').then(r => r.json()).then(setStatus).catch(() => {})
      })
      .catch(() => notify('Could not save', 'error'))
  }

  const pick = () => {
    settingsApi.pickFolder().then(d => {
      if (d.path) save(d.path)
    }).catch(() => {})
  }

  return (
    <>
      <h3 className="settings-title">Screenshots folder</h3>
      <p className="cleanup-hint">
        Watch a folder for new images. When a screenshot appears, the composer
        offers it as a file path to insert into the next message.
      </p>
      <div className="settings-row">
        <span className="settings-label">Folder</span>
        <span className="settings-value" style={{display:'flex', gap: 6, alignItems:'center'}}>
          <input type="text" className="device-name-input" style={{flex:1}}
                 placeholder="~/Desktop or pick…" value={folder}
                 onChange={e => setFolder(e.target.value)}
                 onBlur={() => save(folder)} />
          <button className="dispatch-btn" onClick={pick}
                  style={{width:'auto', padding:'4px 10px', whiteSpace:'nowrap'}}>
            Pick…
          </button>
        </span>
      </div>
      {status && (
        <div className="settings-row">
          <span className="settings-label">Status</span>
          <span className="settings-value settings-detail">
            {status.watching
              ? <span style={{color:'#4ade80'}}>● watching</span>
              : folder ? 'stopped' : 'not configured'}
            {status.pending > 0 && ` · ${status.pending} pending`}
          </span>
        </div>
      )}
      {folder && (
        <div className="settings-actions">
          <button className="launcher-cancel" onClick={() => save('')}>
            Stop watching
          </button>
        </div>
      )}
    </>
  )
}

function UpdateSettings() {
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [log, setLog] = useState([])
  const [applying, setApplying] = useState(false)
  const [done, setDone] = useState(false)
  const logRef = useRef(null)
  const { notify } = useToast()

  const check = async () => {
    setChecking(true)
    setInfo(null)
    try {
      const d = await fetch('/api/update/check').then(r => r.json())
      setInfo(d)
    } catch (e) {
      notify('Could not check for updates', 'error')
    } finally {
      setChecking(false)
    }
  }

  const apply = async () => {
    setLog([])
    setApplying(true)
    setDone(false)
    try {
      const resp = await fetch('/api/update/apply', { method: 'POST' })
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done: streamDone, value } = await reader.read()
        if (streamDone) break
        const chunk = decoder.decode(value, { stream: true })
        setLog(prev => {
          const lines = [...prev, ...chunk.split('\n').filter(l => l)]
          if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
          return lines
        })
        if (chunk.includes('__DONE__')) { setDone(true); setInfo(null) }
        if (chunk.includes('__ERROR__')) { /* leave log visible */ }
      }
    } catch (e) {
      setLog(prev => [...prev, `Error: ${e.message}`])
    } finally {
      setApplying(false)
    }
  }

  return (
    <>
      <h3 className="settings-title">Updates</h3>
      <p className="cleanup-hint">
        Compares the running commit against the remote repository.
        Requires a git remote to be configured and a clean working tree.
      </p>
      {info && (
        <div className="settings-update-info">
          <div className="settings-row">
            <span className="settings-label">Current</span>
            <code className="settings-mono">{info.short || info.current?.slice(0, 7)}</code>
          </div>
          {!info.no_remote && !info.error && (
            <div className="settings-row">
              <span className="settings-label">Latest</span>
              <code className="settings-mono">{info.latest_short || info.latest?.slice(0, 7)}</code>
            </div>
          )}
          {info.no_remote && (
            <p className="cleanup-hint" style={{color: 'var(--text-dim)'}}>No git remote configured — cannot check for updates.</p>
          )}
          {info.error && (
            <p className="cleanup-hint" style={{color: 'var(--muted)'}}>⚠ {info.error}</p>
          )}
          {info.up_to_date && !info.no_remote && !info.error && (
            <p className="cleanup-hint" style={{color: '#16a34a'}}>✓ Already up to date.</p>
          )}
          {!info.up_to_date && !info.no_remote && (
            <>
              <p className="cleanup-hint">Update available.</p>
              {!info.clean && (
                <p className="cleanup-hint" style={{color: 'var(--muted)'}}>⚠ Working tree has uncommitted changes — commit or stash before updating.</p>
              )}
              {info.clean && (
                <button className="settings-btn settings-btn-primary"
                        onClick={apply} disabled={applying}>
                  {applying ? 'Updating…' : 'Update now'}
                </button>
              )}
            </>
          )}
        </div>
      )}
      {log.length > 0 && (
        <pre className="settings-update-log" ref={logRef}>
          {log.filter(l => !l.startsWith('__')).join('\n')}
        </pre>
      )}
      {done && (
        <p className="cleanup-hint" style={{color: '#16a34a', fontWeight: 600}}>
          ✓ Update applied. Restart Quarterdeck to use the new version.
        </p>
      )}
      <button className="settings-btn" onClick={check} disabled={checking} style={{marginTop: 8}}>
        {checking ? 'Checking…' : 'Check for updates'}
      </button>
    </>
  )
}

function PollSettings() {
  const notify = useToast()
  const [intervals, setIntervals] = useState({
    poll_busy: '', poll_idle: '', poll_sessions: ''
  })

  useEffect(() => {
    settingsApi.getSettings().then(s => setIntervals({
      poll_busy: s['poll-busy-ms'] || '',
      poll_idle: s['poll-idle-ms'] || '',
      poll_sessions: s['poll-sessions-ms'] || '',
    })).catch(() => {})
  }, [])

  const save = (key, value) => {
    setIntervals(prev => ({ ...prev, [key]: value }))
    const settingsKey = key.replace('_', '-').replace('_', '-')
    const ms = parseInt(value, 10)
    if (value && ms > 0) {
      settingsApi.saveSettings({ [`poll-${key.replace('poll_', '')}-ms`]: ms })
        .catch(() => notify('Could not save', 'error'))
    } else if (!value) {
      settingsApi.saveSettings({ [`poll-${key.replace('poll_', '')}-ms`]: null })
        .catch(() => notify('Could not save', 'error'))
    }
  }

  return (
    <>
      <h3 className="settings-title">Poll intervals</h3>
      <p className="cleanup-hint">
        How often the frontend checks for changes. Lower values feel snappier but
        cost more CPU and battery. Leave blank for defaults.
      </p>
      <div className="settings-row">
        <span className="settings-label">Busy pane (ms)</span>
        <input type="number" min="100" max="5000" step="100"
               className="settings-number-input" placeholder="400"
               value={intervals.poll_busy}
               onChange={e => save('poll_busy', e.target.value)} />
      </div>
      <div className="settings-row">
        <span className="settings-label">Idle pane (ms)</span>
        <input type="number" min="200" max="10000" step="100"
               className="settings-number-input" placeholder="1200"
               value={intervals.poll_idle}
               onChange={e => save('poll_idle', e.target.value)} />
      </div>
      <div className="settings-row">
        <span className="settings-label">Session list (ms)</span>
        <input type="number" min="500" max="10000" step="500"
               className="settings-number-input" placeholder="2000"
               value={intervals.poll_sessions}
               onChange={e => save('poll_sessions', e.target.value)} />
      </div>
    </>
  )
}

function RetentionSettings() {
  const notify = useToast()
  const [days, setDays] = useState('')

  useEffect(() => {
    settingsApi.getSettings().then(s => {
      setDays(s['retention-days'] || '')
    }).catch(() => {})
  }, [])

  const save = (value) => {
    setDays(value)
    const n = parseInt(value, 10)
    settingsApi.saveSettings({ 'retention-days': (n > 0) ? n : null })
      .catch(() => notify('Could not save', 'error'))
  }

  return (
    <>
      <h3 className="settings-title">Retention</h3>
      <p className="cleanup-hint">
        Sessions older than this many days are offered for cleanup. The audit trail
        also drops records beyond this age. Leave blank to keep everything.
      </p>
      <div className="settings-row">
        <span className="settings-label">Days</span>
        <input type="number" min="1" max="365" step="1"
               className="settings-number-input" placeholder="∞"
               value={days}
               onChange={e => save(e.target.value)} />
      </div>
    </>
  )
}

function DiskCleanup() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [status, setStatus] = useState(null)
  const [snapshots, setSnapshots] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setBusy(true)
    Promise.all([
      fetch('/api/disk/status').then(r => r.json()),
      fetch('/api/disk/tm-snapshots').then(r => r.json()),
    ]).then(([s, t]) => {
      setStatus(s)
      setSnapshots(t.snapshots || [])
    }).catch(() => notify('Could not load disk info', 'error'))
      .finally(() => setBusy(false))
  }

  const deleteOldKas = async () => {
    const versions = (status?.kas_versions || []).filter(v => v.old)
    const ok = await askConfirm(
      `Delete ${versions.length} old kiro-crew bundle${versions.length === 1 ? '' : 's'}?`,
      `${status?.old_kas_display} will be freed. kiro-cli re-downloads bundles as needed. The current version is kept.`,
      'Delete'
    )
    if (!ok) return
    setBusy(true)
    fetch('/api/disk/kas-old', { method: 'DELETE' })
      .then(r => r.json())
      .then(d => {
        notify(`Freed ${d.freed_display} (${d.deleted.length} old version${d.deleted.length === 1 ? '' : 's'} removed)`, 'info')
        load()
      })
      .catch(() => notify('Delete failed', 'error'))
      .finally(() => setBusy(false))
  }

  const deleteSnapshots = async (dates, label) => {
    const ok = await askConfirm(
      `Delete ${label}?`,
      'Time Machine local snapshots will be permanently removed from this Mac. Remote backups are not affected.',
      'Delete'
    )
    if (!ok) return
    setBusy(true)
    fetch('/api/disk/tm-snapshots', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dates === 'all' ? { all: true } : { dates }),
    }).then(r => r.json())
      .then(d => {
        notify(`Deleted ${d.count} snapshot${d.count === 1 ? '' : 's'}`, 'info')
        load()
      })
      .catch(() => notify('Delete failed', 'error'))
      .finally(() => setBusy(false))
  }

  const s = status
  const disk = s?.disk
  const sizes = s?.sizes || {}
  const kasVersions = s?.kas_versions || []
  const oldKas = kasVersions.filter(v => v.old)

  const rows = [
    { label: 'kiro-cli total',          key: 'kiro_cli_data' },
    { label: '↳ data.sqlite3',          key: 'kiro_sqlite' },
    { label: '↳ kas bundles',           key: 'kiro_kas' },
    { label: 'Docker',                   key: 'docker' },
    { label: 'Homebrew',                 key: 'homebrew' },
    { label: 'Downloads',                key: 'downloads' },
    { label: 'Caches',                   key: 'caches' },
    { label: 'Quarterdeck state',        key: 'osa_kiro' },
  ]

  return (
    <>
      <h3 className="settings-title">Disk usage</h3>
      <p className="cleanup-hint">Scan to see what's using space on this Mac.</p>
      <div className="settings-actions">
        <button className="launcher-cancel" disabled={busy} onClick={load}>
          {busy ? '⟳ Scanning…' : 'Scan disk'}
        </button>
      </div>

      {disk && (
        <div style={{ marginTop: 12 }}>
          <p className="cleanup-hint" style={{ marginBottom: 6 }}>
            <strong>Disk:</strong> {disk.used_gb} GB used / {disk.total_gb} GB total
            — <span style={{ color: disk.free_gb < 20 ? '#ef4444' : 'var(--text-dim)' }}>
              {disk.free_gb} GB free
            </span>
          </p>

          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <tbody>
              {rows.map(({ label, key }) => {
                const entry = sizes[key]
                if (!entry) return null
                const mb = entry.mb
                const pct = disk.total_gb > 0 && mb > 0
                  ? Math.min(100, (mb / (disk.total_gb * 1024)) * 100)
                  : 0
                return (
                  <tr key={key} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 0', color: 'var(--text-dim)', width: '55%' }}>{label}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>{entry.display}</td>
                    <td style={{ padding: '4px 0', width: 80 }}>
                      {pct > 0 && (
                        <div style={{ height: 4, background: 'var(--border)', borderRadius: 2 }}>
                          <div style={{
                            width: `${pct}%`, height: '100%', borderRadius: 2,
                            background: pct > 15 ? '#ef4444' : pct > 5 ? '#f59e0b' : '#22c55e',
                          }} />
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* kas old version cleanup */}
      {kasVersions.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <p className="cleanup-hint">
            <strong>kiro-crew bundles (kas)</strong> — {kasVersions.length} version{kasVersions.length === 1 ? '' : 's'} installed,
            current: <code style={{ fontSize: 11 }}>{kasVersions[kasVersions.length - 1]?.version}</code>
          </p>
          {oldKas.length > 0 && (
            <>
              <ul className="cleanup-list">
                {oldKas.map(v => (
                  <li key={v.name}>
                    <span className="settings-detail">{v.version}</span>
                    <span className="settings-detail" style={{ marginLeft: 8, color: 'var(--text-dim)' }}>{v.display}</span>
                  </li>
                ))}
              </ul>
              <button className="launcher-cancel" disabled={busy} onClick={deleteOldKas}>
                Delete {oldKas.length} old version{oldKas.length === 1 ? '' : 's'} ({s.old_kas_display})
              </button>
              <p className="cleanup-hint" style={{ marginTop: 6, color: 'var(--text-dim)' }}>
                kiro-cli re-downloads bundles as needed. Only old versions are removed.
              </p>
            </>
          )}
          {oldKas.length === 0 && (
            <p className="cleanup-hint" style={{ color: 'var(--text-dim)' }}>Only one version installed — nothing to remove.</p>
          )}
        </div>
      )}

      {/* Time Machine snapshots */}
      {snapshots !== null && (
        <div style={{ marginTop: 16 }}>
          <p className="cleanup-hint">
            <strong>Time Machine local snapshots</strong> — {snapshots.length === 0
              ? 'none found'
              : `${snapshots.length} snapshot${snapshots.length === 1 ? '' : 's'} on this disk`}
          </p>
          {snapshots.length > 0 && (
            <>
              <ul className="cleanup-list">
                {snapshots.map(snap => (
                  <li key={snap.date} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span className="settings-detail">{snap.date}</span>
                    <button
                      className="launcher-cancel"
                      style={{ padding: '2px 8px', fontSize: 11 }}
                      disabled={busy}
                      onClick={() => deleteSnapshots([snap.date], `snapshot ${snap.date}`)}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
              <button className="launcher-cancel" disabled={busy}
                      onClick={() => deleteSnapshots('all', `all ${snapshots.length} snapshots`)}>
                Delete all snapshots
              </button>
              <p className="cleanup-hint" style={{ marginTop: 6, color: 'var(--text-dim)' }}>
                Deletes local copies only. Remote Time Machine backups are not affected.
              </p>
            </>
          )}
        </div>
      )}
    </>
  )
}

function DangerZone() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)

  const loadPreview = () => {
    setBusy(true)
    fetch('/api/cleanup/preview').then(r => r.json())
      .then(d => setPreview(d))
      .catch(() => notify('Could not load cleanup preview', 'error'))
      .finally(() => setBusy(false))
  }

  const runCleanup = async (ids) => {
    const ok = await askConfirm(
      `Delete ${ids.length} session${ids.length === 1 ? '' : 's'}?`,
      'Session files (.json, .jsonl, .lock, .history) will be permanently removed. This cannot be undone.',
      'Delete')
    if (!ok) return
    setBusy(true)
    fetch('/api/cleanup/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_ids: ids }),
    }).then(r => r.json())
      .then(d => {
        if (d.deleted) notify(`Deleted ${d.deleted.length} session(s)`, 'info')
        else if (d.error) notify(d.error, 'error')
        loadPreview()
      })
      .catch(() => notify('Cleanup failed', 'error'))
      .finally(() => setBusy(false))
  }

  const total = preview ? (preview.zombies?.length || 0) + (preview.stale?.length || 0) : 0

  return (
    <>
      <h3 className="settings-title settings-danger-title">Danger zone</h3>
      <p className="cleanup-hint">
        Find zombie sessions (0–1 turns, under 5 minutes) and stale sessions
        (idle more than 24 hours with a dead process). Review before deleting.
      </p>
      <div className="settings-actions">
        <button className="launcher-cancel" disabled={busy} onClick={loadPreview}>
          {busy ? '⟳ Scanning…' : 'Scan for cleanup candidates'}
        </button>
      </div>
      {preview && (
        <div className="danger-preview">
          {total === 0 && <p className="cleanup-hint">Nothing to clean up.</p>}
          {preview.paste_bytes > 0 && (
            <p className="cleanup-hint" style={{marginTop: 8}}>
              <strong>Paste files</strong> — {preview.paste_size_display} stored in ~/.osa-kiro/pastes
              (files older than 30 days are swept automatically on cleanup).
            </p>
          )}
          {(preview.zombies || []).length > 0 && (
            <>
              <p className="cleanup-hint" style={{marginTop: 8}}>
                <strong>Zombies</strong> ({preview.zombies.length}) — empty or near-empty sessions:
              </p>
              <ul className="cleanup-list">
                {preview.zombies.map(s => (
                  <li key={s.id}>
                    <span className="cleanup-title">{s.title}</span>
                    <span className="settings-detail"> — {s.turns} turn{s.turns !== 1 ? 's' : ''}, {s.duration_min} min</span>
                  </li>
                ))}
              </ul>
              <button className="launcher-cancel" disabled={busy}
                      onClick={() => runCleanup(preview.zombies.map(s => s.id))}>
                Delete all zombies
              </button>
            </>
          )}
          {(preview.stale || []).length > 0 && (
            <>
              <p className="cleanup-hint" style={{marginTop: 8}}>
                <strong>Stale</strong> ({preview.stale.length}) — idle {'>'}24h with dead process:
              </p>
              <ul className="cleanup-list">
                {preview.stale.map(s => (
                  <li key={s.id}>
                    <span className="cleanup-title">{s.title}</span>
                    <span className="settings-detail"> — idle {s.hours_idle}h</span>
                  </li>
                ))}
              </ul>
              <button className="launcher-cancel" disabled={busy}
                      onClick={() => runCleanup(preview.stale.map(s => s.id))}>
                Delete all stale
              </button>
            </>
          )}
        </div>
      )}
    </>
  )
}

function DeviceSettings() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [devices, setDevices] = useState([])
  const [newName, setNewName] = useState('')
  const [newToken, setNewToken] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => settingsApi.listDevices()
    .then(d => setDevices(d.devices || []))
    .catch(() => {})

  useEffect(() => { load() }, [])

  const create = () => {
    if (!newName.trim()) { notify('Enter a device name', 'error'); return }
    setBusy(true)
    settingsApi.createDevice(newName.trim())
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setNewToken(d.token)
        setNewName('')
        load()
      })
      .catch(() => notify('Could not create device', 'error'))
      .finally(() => setBusy(false))
  }

  const revoke = async (id, name) => {
    const ok = await askConfirm(
      `Revoke "${name}"?`,
      'This device will no longer be able to access Quarterdeck remotely. The token cannot be recovered.',
      'Revoke')
    if (!ok) return
    settingsApi.revokeDevice(id)
      .then(d => { if (d.ok) { notify(`Revoked "${name}"`, 'info'); load() } else notify(d.error, 'error') })
      .catch(() => notify('Could not revoke', 'error'))
  }

  return (
    <>
      <h3 className="settings-title">Device tokens</h3>
      <p className="cleanup-hint">
        Named tokens replace the single shared secret. Each device gets its own
        token, independently revocable. A lost phone is a two-click problem: revoke
        its token here. If no device tokens exist, the legacy single token in{' '}
        <code>~/.osa-kiro/token</code> is used.
      </p>
      {devices.length > 0 && (
        <ul className="device-list">
          {devices.map(d => (
            <li key={d.id} className="device-item">
              <div className="device-info">
                <span className="device-name">{d.name}</span>
                <span className="settings-detail">
                  {d.token_prefix}… · {d.last_used_at
                    ? `last used ${new Date(d.last_used_at).toLocaleDateString()}`
                    : 'never used'}
                  {d.last_ip && ` from ${d.last_ip}`}
                </span>
              </div>
              <button className="launcher-cancel device-revoke"
                      onClick={() => revoke(d.id, d.name)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
      {newToken && (
        <div className="device-token-reveal">
          <p className="cleanup-hint" style={{color: '#4ade80'}}>
            Token created. Copy it now — it will not be shown again:
          </p>
          <code className="device-token-value">{newToken}</code>
          <button className="dispatch-btn" style={{marginTop: 6, width: 'auto', padding: '4px 12px'}}
                  onClick={() => { navigator.clipboard.writeText(newToken); notify('Copied', 'info'); setNewToken(null) }}>
            Copy and dismiss
          </button>
        </div>
      )}
      <div className="device-create-row">
        <input type="text" className="device-name-input" placeholder="Device name (e.g. phone)"
               value={newName} onChange={e => setNewName(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && create()} />
        <button className="dispatch-btn" disabled={busy} onClick={create}
                style={{width: 'auto', padding: '6px 14px'}}>
          Create token
        </button>
      </div>
    </>
  )
}

// The settings view. Until now it was three paragraphs of prose behind a gear
// icon — documentation, not settings. Everything here drives something real.
// Formats a decision, a request or a tool call as one line. Deliberately plain:
// this is evidence, and evidence that has been styled into a story is worth less
// than evidence you can read.
function auditLine(entry) {
  if (entry.kind === 'decision') {
    const verb = entry.allow ? 'allowed' : 'denied'
    const how = entry.how === 'api' ? '' : ` (${entry.how})`
    return `${verb}${how} ${entry.tool || 'tool'}`
  }
  if (entry.kind === 'tool') {
    return `${entry.tool || 'tool'} ${entry.ok === false ? '✗ failed' : 'ran'}`
  }
  return `${entry.method || ''} ${entry.path || ''}`.trim()
}

function AuditSettings() {
  const notify = useToast()
  const [stats, setStats] = useState(null)
  const [records, setRecords] = useState([])
  const [open, setOpen] = useState(false)

  const load = () => settingsApi.getAudit(40)
    .then(d => { setStats(d); setRecords(d.records || []) })
    .catch(() => {})

  useEffect(() => { load() }, [])

  const toggle = (on) => {
    // Optimistic, then reconciled from the server's own view of the flag file —
    // the setting and the file have to agree, and the server is the one that
    // knows whether they do.
    setStats(prev => ({ ...prev, enabled: on }))
    settingsApi.setAuditEnabled(on)
      .then(d => { if (d.error) notify(d.error, 'error'); load() })
      .catch(() => { notify('Could not change that', 'error'); load() })
  }

  const size = stats?.bytes
    ? stats.bytes > 1048576
      ? `${(stats.bytes / 1048576).toFixed(1)} MB`
      : `${Math.max(1, Math.round(stats.bytes / 1024))} KB`
    : '0 KB'

  return (
    <>
      <h3 className="settings-title">Audit trail</h3>
      <p className="cleanup-hint">
        Anything that reaches this API can start processes and type into an agent
        with a shell. This is the record of what was actually done, and from which
        device: every mutating request, every tool call a hooked session made, and
        every held call you allowed or denied. It stays on this machine, and the
        access token is never written into it.
      </p>
      <div className="settings-row">
        <span className="settings-label">Recording</span>
        <span className="settings-value">
          <label className="settings-check">
            <input type="checkbox" checked={stats?.enabled ?? false}
                   onChange={e => toggle(e.target.checked)} />
            {stats?.enabled ? 'on' : 'off'}
          </label>
          {stats && stats.enabled !== stats.configured && (
            <span className="settings-warn"> · the setting and the hook’s flag
              file disagree — restarting the backend reconciles them</span>
          )}
        </span>
      </div>
      <div className="settings-row">
        <span className="settings-label">Kept</span>
        <span className="settings-value settings-detail">
          {stats ? `${stats.days} day${stats.days === 1 ? '' : 's'}, ${size}` : '…'}
          {stats?.retention_days ? ` — days older than ${stats.retention_days} are deleted` : ''}
        </span>
      </div>
      <p className="cleanup-hint">
        Tool calls need the <code>postToolUse</code> hook installed above. Requests
        and approval decisions are recorded either way, because Quarterdeck sees those
        itself.
      </p>
      <div className="settings-actions">
        <button className="launcher-cancel" onClick={() => { setOpen(!open); load() }}>
          {open ? 'Hide the last 40' : 'Show the last 40'}
        </button>
      </div>
      {open && (
        <ul className="audit-list">
          {records.length === 0 && (
            <li className="audit-empty">Nothing recorded yet.</li>
          )}
          {records.map((entry, i) => (
            <li key={i} className="audit-entry" title={JSON.stringify(entry)}>
              <span className="audit-at">{(entry.at || '').slice(11, 19)}</span>
              <span className={`audit-kind audit-${entry.kind}`}>{entry.kind}</span>
              <span className="audit-what">{auditLine(entry)}</span>
              {entry.actor?.via === 'remote' && (
                <span className="audit-remote" title={entry.actor.host}>remote</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function ProfileSettings() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [profiles, setProfiles] = useState([])
  const [current, setCurrent] = useState(null)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => {
    profilesApi.listProfiles().then(d => setProfiles(d.profiles || [])).catch(() => {})
    profilesApi.currentProfile().then(setCurrent).catch(() => {})
  }

  useEffect(() => { load() }, [])

  const handleSave = async () => {
    const name = newName.trim()
    if (!name) { notify('Enter a profile name', 'error'); return }
    setBusy(true)
    const d = await profilesApi.saveProfile(name).catch(() => ({ error: 'Network error' }))
    setBusy(false)
    if (d.error) { notify(d.error, 'error'); return }
    notify(`Saved profile "${name}" (${d.email})`, 'info')
    setNewName('')
    load()
  }

  const handleSwitch = async (name) => {
    setBusy(true)
    const d = await profilesApi.switchProfile(name).catch(() => ({ error: 'Network error' }))
    setBusy(false)
    if (d.error) { notify(d.error, 'error'); return }
    notify(`Switched to "${name}" (${d.email}). New sessions will use this identity.`, 'info')
    load()
  }

  const handleDelete = async (name) => {
    const ok = await askConfirm(
      `Delete profile "${name}"?`,
      'The saved credentials will be removed. This does not log you out.',
      'Delete')
    if (!ok) return
    setBusy(true)
    const d = await profilesApi.deleteProfile(name).catch(() => ({ error: 'Network error' }))
    setBusy(false)
    if (d.error) { notify(d.error, 'error'); return }
    notify(`Deleted "${name}"`, 'info')
    load()
  }

  return (
    <>
      <h3 className="settings-title">Identity profiles</h3>
      <p className="cleanup-hint">
        Save multiple kiro-cli logins and switch between them without re-authenticating.
        Running sessions keep their original identity; new sessions pick up the switch.
      </p>

      {current && (
        <div className="settings-row">
          <span className="settings-label">Current</span>
          <span className="settings-value">
            {current.email || '?'}
            {current.active_profile && (
              <span className="settings-detail"> — profile "{current.active_profile}"</span>
            )}
          </span>
        </div>
      )}

      {profiles.length > 0 && (
        <div className="profile-list">
          {profiles.map(p => (
            <div key={p.name} className={`profile-row ${current?.active_profile === p.name ? 'profile-active' : ''}`}>
              <div className="profile-info">
                <span className="profile-name">{p.name}</span>
                <span className="profile-email">{p.email}</span>
              </div>
              <div className="profile-actions">
                {current?.active_profile !== p.name && (
                  <button className="dispatch-btn dispatch-btn-sm" disabled={busy}
                          onClick={() => handleSwitch(p.name)}>
                    Switch
                  </button>
                )}
                {current?.active_profile === p.name && (
                  <span className="profile-badge">active</span>
                )}
                <button className="dispatch-btn dispatch-btn-sm dispatch-btn-danger" disabled={busy}
                        onClick={() => handleDelete(p.name)}>
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="profile-save-row">
        <input className="launcher-input"
               placeholder="Profile name (e.g. work, personal)"
               value={newName}
               onChange={e => setNewName(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && handleSave()} />
        <button className="dispatch-btn" disabled={busy || !newName.trim()}
                onClick={handleSave}>
          Save current as…
        </button>
      </div>
    </>
  )
}

function HiddenPrefixesSettings() {
  const notify = useToast()
  const [prefixes, setPrefixes] = React.useState('')
  const [loaded, setLoaded] = React.useState(false)

  React.useEffect(() => {
    settingsApi.getSettings().then(s => {
      const val = s['hidden-title-prefixes']
      setPrefixes(Array.isArray(val) ? val.join(', ') : 'You are Bosun')
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  const save = (value) => {
    setPrefixes(value)
    const list = value.split(',').map(s => s.trim()).filter(Boolean)
    settingsApi.saveSettings({ 'hidden-title-prefixes': list })
      .then(() => notify('Saved', 'success'))
      .catch(() => notify('Could not save', 'error'))
  }

  if (!loaded) return null
  return (
    <>
      <h3 className="settings-title">Hidden session prefixes</h3>
      <p className="cleanup-hint">
        Sessions whose title starts with any of these prefixes are hidden from the
        grid by default. Use the 🧭 Captain button in the toolbar to reveal them.
        Comma-separated.
      </p>
      <div className="settings-row">
        <input
          className="settings-text-input"
          style={{ flex: 1 }}
          value={prefixes}
          placeholder="You are Bosun"
          onChange={e => setPrefixes(e.target.value)}
          onBlur={e => save(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') save(e.target.value) }}
        />
      </div>
    </>
  )
}

export const DEFAULT_COMPOSER_CHIPS = [
  { label: "What's up?", mode: 'send', prompt: 'Where are we? Read the project docs and tell me what is done and what is next.' },
  { label: 'What next?', mode: 'send', prompt: 'What should we do next and why? Name one concrete task.' },
  { label: "What's blocked?", mode: 'send', prompt: 'What is currently blocked or waiting? Check the docs and any open steering tasks.' },
  { label: 'Refactor', mode: 'send', prompt: 'Read the code and name the refactor worth doing most — what is carrying too much, and what would you split out?' },
  { label: 'Write a task', mode: 'paste', prompt: 'Add a task: ' },
]

export const CHIP_MODES = ['send', 'paste']

export const validChip = (c) => c && typeof c.label === 'string' && c.label.trim()
  && typeof c.prompt === 'string' && CHIP_MODES.includes(c.mode)

function ComposerChipsSettings() {
  const notify = useToast()
  const [chips, setChips] = React.useState(null)

  React.useEffect(() => {
    settingsApi.getSettings().then(s => {
      const v = s['composer-chips']
      setChips(Array.isArray(v) && v.length ? v.filter(validChip) : DEFAULT_COMPOSER_CHIPS)
    }).catch(() => setChips(DEFAULT_COMPOSER_CHIPS))
  }, [])

  const persist = (list) => {
    settingsApi.saveSettings({ 'composer-chips': list })
      .catch(() => notify('Could not save', 'error'))
  }

  const update = (next) => { setChips(next); persist(next) }

  const setChip = (i, patch) => update(chips.map((x, j) => j === i ? { ...x, ...patch } : x))

  const move = (i, dir) => {
    const next = [...chips]
    const j = i + dir
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    update(next)
  }

  if (!chips) return null
  return (
    <>
      <h3 className="settings-title">Composer chips</h3>
      <p className="cleanup-hint">
        Buttons above the input composer. <strong>send</strong> fires immediately;{' '}
        <strong>paste</strong> drops the text in the box for you to finish.
      </p>
      <div className="chip-list">
        {chips.map((c, i) => (
          <div className="chip-row" key={i}>
            <div className="chip-row-top">
              <input className="chip-label" value={c.label} placeholder="label"
                onChange={e => setChip(i, { label: e.target.value })} />
              <select className="chip-mode" value={c.mode}
                onChange={e => setChip(i, { mode: e.target.value })}>
                {CHIP_MODES.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <button className="chip-move" title="Move up" disabled={i === 0}
                onClick={() => move(i, -1)}>↑</button>
              <button className="chip-move" title="Move down" disabled={i === chips.length - 1}
                onClick={() => move(i, 1)}>↓</button>
              <button className="chip-del" title="Remove"
                onClick={() => update(chips.filter((_, j) => j !== i))}>×</button>
            </div>
            <textarea className="chip-prompt" rows={2} value={c.prompt}
              placeholder="Prompt text sent or pasted when clicked"
              onChange={e => setChip(i, { prompt: e.target.value })} />
          </div>
        ))}
      </div>
      <div className="settings-row" style={{ marginTop: 8, gap: 6 }}>
        <button className="launcher-btn" onClick={() => update([...chips, { label: 'New chip', prompt: '', mode: 'send' }])}>
          + Add chip
        </button>
        <button className="launcher-btn" onClick={() => update(DEFAULT_COMPOSER_CHIPS)} title="Restore defaults">
          Reset
        </button>
      </div>
    </>
  )
}

function ScriptsSettings() {
  const notify = useToast()
  const { askConfirm } = useConfirm()
  const [folders, setFolders] = React.useState([])
  const [cwd, setCwd] = React.useState('')
  const [scripts, setScripts] = React.useState(null)
  const [imports, setImports] = React.useState(null)
  const [showImports, setShowImports] = React.useState(false)
  const [importing, setImporting] = React.useState(null)
  // Add form state
  const [name, setName] = React.useState('')
  const [command, setCommand] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [confirm, setConfirm] = React.useState(false)
  const [adding, setAdding] = React.useState(false)

  React.useEffect(() => {
    settingsApi.getSettings().then(s => {
      const pinned = s['pinned-folders'] || []
      const cwds = pinned.map(f => ({ label: f.folder || f.cwd, cwd: f.cwd }))
      setFolders(cwds)
      if (cwds.length > 0 && !cwd) setCwd(cwds[0].cwd)
    }).catch(() => {})
  }, [])

  const load = (targetCwd) => {
    const target = targetCwd || cwd
    if (!target) { setScripts([]); return }
    scriptsApi.listScripts(target)
      .then(d => setScripts(d.scripts || []))
      .catch(() => setScripts([]))
  }

  React.useEffect(() => {
    if (cwd) { load(cwd); setShowImports(false); setImports(null) }
    else setScripts([])
  }, [cwd])

  const handleAdd = () => {
    if (!name.trim() || !command.trim() || !cwd) return
    setAdding(true)
    scriptsApi.addScript(cwd, name.trim(), command.trim(), description.trim(), confirm)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setName(''); setCommand(''); setDescription(''); setConfirm(false)
        load()
      })
      .catch(() => notify('Could not add script', 'error'))
      .finally(() => setAdding(false))
  }

  const handleDelete = async (script) => {
    const ok = await askConfirm(
      `Delete script "${script.name}"?`,
      'This cannot be undone.',
      'Delete'
    )
    if (!ok) return
    scriptsApi.deleteScript(script.id, cwd)
      .then(d => {
        if (d.ok) setScripts(prev => prev.filter(s => s.id !== script.id))
        else notify('Could not delete', 'error')
      })
      .catch(() => notify('Could not delete', 'error'))
  }

  const handleLoadImports = () => {
    if (!cwd) return
    setImporting(null)
    scriptsApi.detectImports(cwd)
      .then(d => { setImports(d.imports || []); setShowImports(true) })
      .catch(() => notify('Could not detect imports', 'error'))
  }

  const handleImport = (imp) => {
    setImporting(imp.name)
    scriptsApi.addScript(cwd, imp.name, imp.command, imp.description || '', false)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        load()
        setImports(prev => prev ? prev.filter(i => i.name !== imp.name) : prev)
      })
      .catch(() => notify('Could not import', 'error'))
      .finally(() => setImporting(null))
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault(); handleAdd()
    }
  }

  // Filter out already-added scripts from imports list
  const addedCommands = new Set((scripts || []).map(s => s.command))
  const filteredImports = (imports || []).filter(i => !addedCommands.has(i.command))

  return (
    <div className="scripts-settings">
      <h3 className="settings-title">Folder scripts</h3>
      <p className="cleanup-hint">
        Named shell commands bound to a project. Run them one-click from the toolbar
        — no LLM session needed.
      </p>

      {folders.length === 0 ? (
        <p className="cleanup-hint" style={{ color: '#94a3b8' }}>
          Pin a project folder in Settings → General to add scripts for it.
        </p>
      ) : (
        <>
          <div className="settings-row" style={{ marginBottom: 12 }}>
            <span className="settings-label">Project</span>
            <select
              className="launcher-select"
              value={cwd}
              onChange={e => setCwd(e.target.value)}
            >
              {folders.map(f => (
                <option key={f.cwd} value={f.cwd}>{f.label}</option>
              ))}
            </select>
            <button
              className="settings-btn"
              style={{ marginLeft: 8 }}
              onClick={handleLoadImports}
              title="Import targets from Makefile or package.json"
            >↓ Import</button>
          </div>

          {/* Import suggestions */}
          {showImports && filteredImports.length > 0 && (
            <div className="script-import-list">
              <p className="cleanup-hint" style={{ marginBottom: 6 }}>
                Detected in {cwd.split('/').pop()}:
              </p>
              {filteredImports.map(imp => (
                <div key={imp.name} className="script-import-row">
                  <span className="script-import-source">{imp.source}</span>
                  <span className="script-import-name">{imp.name}</span>
                  <span className="script-import-cmd">{imp.command}</span>
                  <button
                    className="settings-btn settings-btn-primary"
                    style={{ padding: '2px 10px', fontSize: 11 }}
                    onClick={() => handleImport(imp)}
                    disabled={importing === imp.name}
                  >{importing === imp.name ? '…' : '+ Add'}</button>
                </div>
              ))}
            </div>
          )}
          {showImports && filteredImports.length === 0 && (
            <p className="cleanup-hint" style={{ color: '#64748b', marginBottom: 8 }}>
              No new targets found (all already added, or no Makefile/package.json).
            </p>
          )}

          {/* Script list */}
          {scripts === null ? (
            <p className="cleanup-hint" style={{ color: '#64748b' }}>Loading…</p>
          ) : scripts.length === 0 ? (
            <p className="cleanup-hint" style={{ color: '#64748b' }}>No scripts yet.</p>
          ) : (
            <div className="scripts-list">
              {scripts.map(s => (
                <div key={s.id} className="script-row">
                  <span className="script-row-name">{s.name}</span>
                  <code className="script-row-cmd">{s.command}</code>
                  {s.confirm && (
                    <span className="script-confirm-badge" title="Asks before running">⚠</span>
                  )}
                  <button
                    className="secret-delete"
                    onClick={() => handleDelete(s)}
                    title={`Delete "${s.name}"`}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Add form */}
          <div className="script-add-form">
            <div className="script-add-row">
              <input
                className="deny-input"
                placeholder="Name"
                value={name}
                onChange={e => setName(e.target.value)}
                onKeyDown={handleKeyDown}
                style={{ width: 110 }}
              />
              <input
                className="deny-input"
                placeholder="shell command"
                value={command}
                onChange={e => setCommand(e.target.value)}
                onKeyDown={handleKeyDown}
                style={{ flex: 1, fontFamily: 'monospace', fontSize: 12 }}
              />
            </div>
            <div className="script-add-row" style={{ marginTop: 4 }}>
              <input
                className="deny-input"
                placeholder="Description (optional)"
                value={description}
                onChange={e => setDescription(e.target.value)}
                onKeyDown={handleKeyDown}
                style={{ flex: 1 }}
              />
              <label className="script-confirm-label">
                <input
                  type="checkbox"
                  checked={confirm}
                  onChange={e => setConfirm(e.target.checked)}
                  style={{ marginRight: 4 }}
                />
                Confirm before run
              </label>
              <button
                className="deny-add-btn"
                onClick={handleAdd}
                disabled={adding || !name.trim() || !command.trim()}
              >{adding ? '…' : 'Add'}</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function SecretsSettings() {
  const notify = useToast()
  const { askConfirm } = useConfirm()
  const [folders, setFolders] = React.useState([])
  const [cwd, setCwd] = React.useState('')
  const [secrets, setSecrets] = React.useState(null) // null = loading
  const [name, setName] = React.useState('')
  const [value, setValue] = React.useState('')
  const [adding, setAdding] = React.useState(false)
  const [showValue, setShowValue] = React.useState(false)

  // Load pinned folders from settings as cwd source
  React.useEffect(() => {
    settingsApi.getSettings().then(s => {
      const pinned = (s['pinned-folders'] || [])
      const cwds = pinned.map(f => ({ label: f.folder || f.cwd, cwd: f.cwd }))
      setFolders(cwds)
      if (cwds.length > 0 && !cwd) setCwd(cwds[0].cwd)
    }).catch(() => {})
  }, [])

  const load = (targetCwd) => {
    const target = targetCwd || cwd
    if (!target) { setSecrets([]); return }
    secretsApi.listSecrets(target)
      .then(d => setSecrets(d.secrets || []))
      .catch(() => setSecrets([]))
  }

  React.useEffect(() => {
    if (cwd) load(cwd)
    else setSecrets([])
  }, [cwd])

  const handleAdd = () => {
    if (!name.trim() || !value.trim() || !cwd) return
    setAdding(true)
    secretsApi.addSecret(cwd, name.trim(), value.trim())
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setName(''); setValue(''); setShowValue(false); load()
      })
      .catch(() => notify('Could not add secret', 'error'))
      .finally(() => setAdding(false))
  }

  const handleDelete = async (secretName) => {
    const ok = await askConfirm(
      `Delete secret ${secretName}?`,
      'This removes it from the keychain. Any running session using it will continue until restarted.',
      'Delete'
    )
    if (!ok) return
    secretsApi.deleteSecret(cwd, secretName)
      .then(d => {
        if (d.ok) setSecrets(prev => prev.filter(s => s.name !== secretName))
        else notify('Could not remove', 'error')
      })
      .catch(() => notify('Could not remove', 'error'))
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdd() }
  }

  return (
    <div className="secrets-settings">
      <h3 className="settings-title">Per-project secrets</h3>
      <p className="cleanup-hint">
        Secret values are stored in the macOS keychain, never on disk.
        Names are injected as environment variables when a session starts in that folder.
      </p>

      {folders.length === 0 ? (
        <p className="cleanup-hint" style={{ color: '#94a3b8' }}>
          Pin a project folder in Settings → General to add secrets for it.
        </p>
      ) : (
        <>
          <div className="settings-row" style={{ marginBottom: 12 }}>
            <span className="settings-label">Project</span>
            <select
              className="launcher-select"
              value={cwd}
              onChange={e => setCwd(e.target.value)}
            >
              {folders.map(f => (
                <option key={f.cwd} value={f.cwd}>{f.label}</option>
              ))}
            </select>
          </div>

          {/* Secret list */}
          {secrets === null ? (
            <p className="cleanup-hint" style={{ color: '#64748b' }}>Loading…</p>
          ) : secrets.length === 0 ? (
            <p className="cleanup-hint" style={{ color: '#64748b' }}>No secrets for this project yet.</p>
          ) : (
            <div className="secrets-list">
              {secrets.map(s => (
                <div key={s.name} className="secret-row">
                  <span className="secret-name">{s.name}</span>
                  <span className="secret-value-masked">••••••••</span>
                  <span className="secret-date">
                    {s.updated_at
                      ? new Date(s.updated_at).toLocaleDateString([], { month: 'short', day: 'numeric' })
                      : s.created_at
                        ? new Date(s.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })
                        : ''}
                  </span>
                  <button
                    className="secret-delete"
                    onClick={() => handleDelete(s.name)}
                    title={`Delete ${s.name}`}
                  >×</button>
                </div>
              ))}
            </div>
          )}

          {/* Add form */}
          <div className="secrets-add-row">
            <input
              className="deny-input"
              placeholder="NAME"
              value={name}
              onChange={e => setName(e.target.value.toUpperCase().replace(/\s/g, '_'))}
              onKeyDown={handleKeyDown}
              style={{ width: 120, fontFamily: 'monospace', textTransform: 'uppercase' }}
            />
            <div className="secret-value-wrap">
              <input
                className="deny-input"
                placeholder="value"
                type={showValue ? 'text' : 'password'}
                value={value}
                onChange={e => setValue(e.target.value)}
                onKeyDown={handleKeyDown}
                style={{ flex: 1, minWidth: 160 }}
              />
              <button
                className="secret-reveal-btn"
                onClick={() => setShowValue(v => !v)}
                title={showValue ? 'Hide' : 'Show'}
                type="button"
              >{showValue ? '🙈' : '👁'}</button>
            </div>
            <button
              className="deny-add-btn"
              onClick={handleAdd}
              disabled={adding || !name.trim() || !value.trim()}
            >
              {adding ? '…' : 'Add'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function DenyPatternsSettings() {
  const notify = useToast()
  const [patterns, setPatterns] = React.useState(null)
  const [packs, setPacks] = React.useState(null)
  const [tool, setTool] = React.useState('execute_bash')
  const [pattern, setPattern] = React.useState('')
  const [note, setNote] = React.useState('')
  const [adding, setAdding] = React.useState(false)
  const [installing, setInstalling] = React.useState(null) // pack id being installed
  const [removing, setRemoving] = React.useState(null) // pack id being removed

  const load = () => {
    denyApi.listPatterns()
      .then(d => setPatterns(d.patterns || []))
      .catch(() => setPatterns([]))
    denyApi.listPacks()
      .then(d => setPacks(d.packs || []))
      .catch(() => setPacks([]))
  }

  React.useEffect(() => { load() }, [])

  const handleAdd = () => {
    if (!pattern.trim()) return
    setAdding(true)
    denyApi.addPattern(tool, pattern.trim(), note.trim())
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        setPattern(''); setNote(''); load()
      })
      .catch(() => notify('Could not add pattern', 'error'))
      .finally(() => setAdding(false))
  }

  const handleToggle = (id, currentEnabled) => {
    denyApi.setEnabled(id, !currentEnabled)
      .then(d => {
        if (d.ok) setPatterns(prev => prev.map(p => p.id === id ? { ...p, enabled: !currentEnabled } : p))
        else notify('Could not update', 'error')
      })
      .catch(() => notify('Could not update', 'error'))
  }

  const handleDelete = (id) => {
    denyApi.deletePattern(id)
      .then(d => {
        if (d.ok) setPatterns(prev => prev.filter(p => p.id !== id))
        else notify('Could not remove', 'error')
      })
      .catch(() => notify('Could not remove', 'error'))
  }

  const handleInstallPack = (packId) => {
    setInstalling(packId)
    denyApi.installPack(packId)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        notify(`Pack installed — ${d.added} added, ${d.skipped} already present`, 'success')
        load()
      })
      .catch(() => notify('Could not install pack', 'error'))
      .finally(() => setInstalling(null))
  }

  const handleRemovePack = (packId) => {
    setRemoving(packId)
    denyApi.removePack(packId)
      .then(d => {
        if (d.error) { notify(d.error, 'error'); return }
        notify(`Pack removed — ${d.removed} pattern${d.removed !== 1 ? 's' : ''} deleted`, 'success')
        load()
      })
      .catch(() => notify('Could not remove pack', 'error'))
      .finally(() => setRemoving(null))
  }

  const autoIds = new Set(['auto-secrets-fs', 'auto-secrets-echo'])

  if (patterns === null) return null

  return (
    <>
      {/* Packs section */}
      {packs && packs.length > 0 && (
        <div className="deny-packs">
          <h3 className="settings-title">Ready-to-use packs</h3>
          {packs.map(pack => (
            <div key={pack.id} className="deny-pack-row">
              <div className="deny-pack-info">
                <span className="deny-pack-name">{pack.name}</span>
                <span className="deny-pack-desc">{pack.description}</span>
              </div>
              <div className="deny-pack-right">
                {pack.installed === pack.total
                  ? <span className="deny-pack-installed">✓ {pack.total} installed</span>
                  : pack.installed > 0
                    ? <span className="deny-pack-count">{pack.installed}/{pack.total} installed</span>
                    : null
                }
                {pack.installed > 0 && (
                  <button className="deny-pack-remove-btn" disabled={removing === pack.id}
                    title="Remove all patterns from this pack"
                    onClick={() => handleRemovePack(pack.id)}>
                    {removing === pack.id ? '…' : 'Remove'}
                  </button>
                )}
                <button className="launcher-btn" disabled={installing === pack.id}
                  onClick={() => handleInstallPack(pack.id)}>
                  {installing === pack.id ? '…' : pack.installed === pack.total ? 'Re-install' : 'Install'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 className="settings-title" style={{ marginTop: 16 }}>Active patterns</h3>
      <p className="cleanup-hint">
        Regex patterns matched against <code>execute_bash</code> (or other tool) input
        during <code>preToolUse</code>. Matching calls are blocked automatically — no approval
        prompt, no agent continuation. Case-insensitive. Toggle to disable without deleting.
      </p>
      {patterns.length > 0 ? (
        <ul className="deny-list">
          {patterns.map(p => {
            const isAuto = autoIds.has(p.id)
            const enabled = p.enabled !== false
            return (
              <li key={p.id} className={`deny-item${isAuto ? ' deny-auto' : ''}${!enabled ? ' deny-disabled' : ''}`}>
                <button className={`deny-toggle ${enabled ? 'deny-toggle-on' : 'deny-toggle-off'}`}
                  title={enabled ? 'Click to disable' : 'Click to enable'}
                  onClick={() => !isAuto && handleToggle(p.id, enabled)}
                  disabled={isAuto}
                  style={isAuto ? { cursor: 'default' } : {}}>
                  {enabled ? '●' : '○'}
                </button>
                <span className="deny-tool">{p.tool || 'execute_bash'}</span>
                <code className="deny-pattern">{p.pattern}</code>
                {p.note && <span className="deny-note">{p.note}</span>}
                {isAuto
                  ? <span className="deny-auto-label">auto</span>
                  : <button className="deny-delete" onClick={() => handleDelete(p.id)} title="Remove">×</button>
                }
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="cleanup-hint">No patterns yet — only the defaults will fire when secrets are stored.</p>
      )}

      <h3 className="settings-title" style={{ marginTop: 16 }}>Add pattern</h3>
      <div className="deny-add-row">
        <select className="launcher-select deny-tool-select" value={tool}
          onChange={e => setTool(e.target.value)}>
          <option value="execute_bash">execute_bash</option>
          <option value="fs_write">fs_write</option>
          <option value="fs_read">fs_read</option>
          <option value="*">any tool</option>
        </select>
        <input className="settings-text-input deny-pattern-input" placeholder="regex pattern"
          value={pattern} onChange={e => setPattern(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd() }} />
        <input className="settings-text-input deny-note-input" placeholder="note (optional)"
          value={note} onChange={e => setNote(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd() }} />
        <button className="launcher-btn" onClick={handleAdd}
          disabled={adding || !pattern.trim()}>
          {adding ? '…' : 'Add'}
        </button>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Templates management
// ---------------------------------------------------------------------------
function TemplatesSettings() {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [templates, setTemplates] = React.useState([])
  const [loading, setLoading] = React.useState(true)
  const [editing, setEditing] = React.useState(null)
  const [editDraft, setEditDraft] = React.useState({})

  const load = React.useCallback(() => {
    setLoading(true)
    sessionsApi.listTemplates()
      .then(d => setTemplates(d.templates || []))
      .catch(() => notify('Could not load templates', 'error'))
      .finally(() => setLoading(false))
  }, [notify])

  React.useEffect(() => { load() }, [load])

  const startEdit = (t) => {
    setEditing(t.id)
    setEditDraft({ name: t.name, task: t.task || '', cwd: t.cwd || '' })
  }

  const saveEdit = (id) => {
    sessionsApi.updateTemplate(id, editDraft)
      .then(d => {
        if (d.ok) { setEditing(null); load() }
        else notify(d.error || 'Update failed', 'error')
      })
      .catch(e => notify(e.message || 'Update failed', 'error'))
  }

  const remove = (t) => {
    askConfirm(`Delete template "${t.name}"?`, () => {
      sessionsApi.deleteTemplate(t.id)
        .then(d => { if (d.ok) load(); else notify(d.error || 'Delete failed', 'error') })
        .catch(e => notify(e.message || 'Delete failed', 'error'))
    })
  }

  return (
    <div className="templates-settings">
      <h3 className="settings-title">Templates</h3>
      <p className="cleanup-hint">
        Templates are session recipes with <code>{'{{var}}'}</code> slots.
        Context-seeded ones start from a frozen conversation snapshot.
        Create them from the transcript by clicking&nbsp;📋 on a user turn.
      </p>
      {loading && <p className="cleanup-hint">Loading…</p>}
      {!loading && templates.length === 0 && (
        <p className="cleanup-hint templates-empty">
          No templates yet. Open a session and click 📋 on a user turn to save one.
        </p>
      )}
      {templates.map(t => (
        <div key={t.id} className="template-row">
          {editing === t.id ? (
            <div className="template-edit">
              <input
                className="sat-input"
                value={editDraft.name}
                onChange={e => setEditDraft(d => ({ ...d, name: e.target.value }))}
                placeholder="Template name"
                autoFocus
              />
              <textarea
                className="sat-textarea"
                value={editDraft.task}
                onChange={e => setEditDraft(d => ({ ...d, task: e.target.value }))}
                placeholder="Task / prompt with {{var}} slots"
                rows={3}
              />
              <input
                className="sat-input"
                value={editDraft.cwd}
                onChange={e => setEditDraft(d => ({ ...d, cwd: e.target.value }))}
                placeholder="Default working directory (optional)"
              />
              <div className="template-edit-actions">
                <button className="sat-btn-save" onClick={() => saveEdit(t.id)}>Save</button>
                <button className="sat-btn-cancel" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              <div className="template-info">
                <span className="template-name">{t.name}</span>
                {t.snapshot_id && (
                  <span className="template-badge" title="Starts from a conversation snapshot">📎 context</span>
                )}
                {t.cwd && <span className="template-cwd">{t.cwd.split('/').pop()}</span>}
                {t.task && (
                  <p className="template-task">
                    {t.task.slice(0, 120)}{t.task.length > 120 ? '…' : ''}
                  </p>
                )}
              </div>
              <div className="template-actions">
                <button className="script-btn" onClick={() => startEdit(t)}>Edit</button>
                <button className="script-btn script-btn-danger" onClick={() => remove(t)}>Delete</button>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  )
}

function SettingsPanel({ options, paneTheme, onTogglePaneTheme, showHidden, onChangeShowHidden, showCrew, onChangeShowCrew, sessionViewMode, onChangeViewDefault }) {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [tab, setTab] = useState('remote')
  const [hooks, setHooks] = useState(null)
  const [busy, setBusy] = useState(false)
  const [defaults, setDefaults] = useState({ model: '', effort: '', agent: '', handoffTerminal: 'terminal' })
  // View defaults — kept in state so changing the other-device select re-renders
  const [viewDefaults, setViewDefaults] = useState(() => ({
    desktop: localStorage.getItem('session-view-mode-desktop') || null,
    mobile:  localStorage.getItem('session-view-mode-mobile')  || null,
  }))

  // Keep viewDefaults in sync: when the active view changes (e.g. user switches
  // view then opens Settings), update the current-device slot so the select
  // reflects the current value rather than a stale initial snapshot.
  useEffect(() => {
    if (!sessionViewMode) return
    const isMobile = window.innerWidth <= 768
    const device = isMobile ? 'mobile' : 'desktop'
    setViewDefaults(prev => prev[device] === sessionViewMode ? prev : { ...prev, [device]: sessionViewMode })
  }, [sessionViewMode])

  const loadHooks = () =>
    settingsApi.getHooksStatus().then(setHooks).catch(() => {})

  useEffect(() => {
    loadHooks()
    settingsApi.getSettings().then(s => setDefaults({
      model: s['dispatch-model'] || '',
      effort: s['dispatch-effort'] || '',
      engine: s['dispatch-engine'] || '',
      agent: localStorage.getItem('launch-agent') || '',
      handoffTerminal: s['handoff-terminal'] || 'terminal',
    })).catch(() => {})
  }, [])

  const saveDefault = (key, value) => {
    setDefaults(prev => ({ ...prev, [key]: value }))
    if (key === 'agent') {
      // The launcher reads this one from localStorage, so it stays per device.
      if (value) localStorage.setItem('launch-agent', value)
      else localStorage.removeItem('launch-agent')
      return
    }
    if (key === 'handoffTerminal') {
      // Mirrored to localStorage so an open DetailPanel picks it up without a
      // round trip, and saved server-side so a phone sees the same choice.
      localStorage.setItem('handoff-terminal', value)
      settingsApi.saveSettings({ 'handoff-terminal': value })
        .catch(() => notify('Could not save that setting', 'error'))
      return
    }
    settingsApi.saveSettings({ [`dispatch-${key}`]: value })
      .catch(() => notify('Could not save that setting', 'error'))
  }

  const runHookAction = async (action) => {
    if (action === 'uninstall') {
      const ok = await askConfirm(
        'Remove Quarterdeck’s hooks?',
        'All three are taken out of every agent config they were added to. Nothing else in those files is touched. Session ids go back to being inferred from the process tree, end-of-turn to being guessed from file timestamps, and approval gating stops working entirely — a gated session will run its tools without asking.',
        'Remove')
      if (!ok) return
    }
    setBusy(true)
    settingsApi.runHookAction(action)
      .then(d => {
        const err = errorOf(d)
        if (err) { notify(err, 'error'); return }
        const counts = Object.values(d.results || {})
        const changed = counts.filter(r => r === 'installed' || r === 'removed').length
        const failed = counts.filter(r => r.endsWith('failed') || r === 'unreadable')
        notify(failed.length
          ? `${changed} agent${changed === 1 ? '' : 's'} updated, ${failed.length} failed`
          : `${changed} agent${changed === 1 ? '' : 's'} updated`,
          failed.length ? 'error' : 'info')
      })
      .catch(() => notify('Could not reach the backend', 'error'))
      .then(() => { setBusy(false); loadHooks() })
  }

  const routes = hooks?.correlated_via || {}
  const routeTotal = Object.values(routes).reduce((a, b) => a + b, 0)

  const TABS = [
    { id: 'remote',    label: 'Remote' },
    { id: 'general',   label: 'General' },
    { id: 'chips',     label: 'Chips' },
    { id: 'hooks',     label: 'Hooks' },
    { id: 'scripts',   label: 'Scripts' },
    { id: 'templates', label: 'Templates' },
    { id: 'security',  label: 'Security' },
    { id: 'advanced',  label: 'Advanced' },
  ]

  return (
    <div className="settings-panel">
      <div className="settings-tabs">
        {TABS.map(t => (
          <button key={t.id}
                  className={`settings-tab ${tab === t.id ? 'active' : ''}`}
                  onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'remote' && (
        <>
          <RemoteSettings />
          <DeviceSettings />
        </>
      )}

      {tab === 'general' && (
        <>
          <h3 className="settings-title">New session defaults</h3>
          <p className="cleanup-hint">
            What a new session starts with when you do not choose otherwise. The agent
            is remembered on this device; the rest are shared by every client.
          </p>
          <div className="settings-row">
            <span className="settings-label">Agent</span>
            <select className="launcher-select" value={defaults.agent}
                    onChange={(e) => saveDefault('agent', e.target.value)}>
              <option value="">
                {options.default_agent ? `${options.default_agent} (kiro-cli default)` : 'kiro-cli default'}
              </option>
              {(options.agents || []).map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-label">Model</span>
            <select className="launcher-select" value={defaults.model}
                    onChange={(e) => saveDefault('model', e.target.value)}>
              <option value="">kiro-cli default</option>
              {(options.models || []).map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-label">Effort</span>
            <select className="launcher-select" value={defaults.effort}
                    onChange={(e) => saveDefault('effort', e.target.value)}>
              <option value="">kiro-cli default</option>
              {(options.efforts || []).map(x => <option key={x} value={x}>{x}</option>)}
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-label">Engine</span>
            <select className="launcher-select" value={defaults.engine}
                    onChange={(e) => saveDefault('engine', e.target.value)}>
              <option value="">kiro-cli default (v2)</option>
              {(options.engines || ['v1','v2','v3']).map(e => <option key={e} value={e}>{e}</option>)}
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-label">Hand off to</span>
            <select className="launcher-select" value={defaults.handoffTerminal}
                    onChange={(e) => saveDefault('handoffTerminal', e.target.value)}>
              {(options.terminals || []).map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
          </div>
          <p className="cleanup-hint">
            Which terminal the <strong>Hand off</strong> button in a session opens. One
            button, no picker: handing off is a decision you make once, not per session.
          </p>
          <ProfileSettings />
          <AppearanceSettings paneTheme={paneTheme} onTogglePaneTheme={onTogglePaneTheme} />
          <StartingFolderSettings />
          <ScreenshotsSettings />
          <UpdateSettings />
          <PollSettings />
          <RetentionSettings />
          <h3 className="settings-title">Grid</h3>
          {(() => {
            // Show the *saved default* for each device (from state, not live view).
            // Falls back to sessionViewMode for the current device on first use.
            const isMobile = window.innerWidth <= 768
            const desktopVal = ['cards','list','wall'].includes(viewDefaults.desktop) ? viewDefaults.desktop
              : (!isMobile ? (sessionViewMode || 'wall') : 'wall')
            const mobileVal  = ['cards','list','wall'].includes(viewDefaults.mobile)  ? viewDefaults.mobile
              : (isMobile  ? (sessionViewMode || 'cards') : 'cards')
            const handleChange = (device, v) => {
              setViewDefaults(prev => ({ ...prev, [device]: v }))
              onChangeViewDefault && onChangeViewDefault(device, v)
            }
            return (<>
          <div className="settings-row">
            <span className="settings-label">Default view — desktop</span>
            <select className="launcher-select"
                    value={desktopVal}
                    onChange={e => handleChange('desktop', e.target.value)}>
              <option value="wall">Wall</option>
              <option value="cards">Cards</option>
              <option value="list">List</option>
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-label">Default view — mobile</span>
            <select className="launcher-select"
                    value={mobileVal}
                    onChange={e => handleChange('mobile', e.target.value)}>
              <option value="wall">Wall</option>
              <option value="cards">Cards</option>
              <option value="list">List</option>
            </select>
          </div>
            </>)
          })()}
          <p className="cleanup-hint">
            The view that opens on load. Changes apply immediately on the current device.
            Desktop and mobile can have different defaults.
          </p>
          <div className="settings-row">
            <span className="settings-label">Show Captain sessions</span>
            <button
              className={`settings-toggle ${showHidden ? 'on' : 'off'}`}
              onClick={() => onChangeShowHidden && onChangeShowHidden(!showHidden)}
              title="Show machine-owned captain/worker sessions as individual cards (default: grouped)">
              {showHidden ? 'on' : 'off'}
            </button>
          </div>
          <p className="cleanup-hint">
            Worker sessions owned by KiroCrew or another orchestrator are normally
            collapsed into a group card. Turn this on to see them individually.
          </p>
          <div className="settings-row">
            <span className="settings-label">Show Crew sessions</span>
            <button
              className={`settings-toggle ${showCrew ? 'on' : 'off'}`}
              onClick={() => onChangeShowCrew && onChangeShowCrew(!showCrew)}
              title="Show KiroCrew sessions in the grid (default: on)">
              {showCrew ? 'on' : 'off'}
            </button>
          </div>
          <p className="cleanup-hint">
            Turn off to hide all Crew-controlled sessions from the grid and the Crew filter button.
          </p>
          <HiddenPrefixesSettings />
        </>
      )}

      {tab === 'chips' && (
        <ComposerChipsSettings />
      )}

      {tab === 'hooks' && (
        <>
          <h3 className="settings-title">Kiro hooks</h3>
          <p className="cleanup-hint">
            Hooks are how kiro-cli tells Quarterdeck what it is doing, instead of
            Quarterdeck watching from the outside and inferring. Each one runs a single
            command at a specific moment, and none can fail in a way that affects kiro-cli.
          </p>
          <p className="cleanup-hint">
            <strong>Approval gating needs these installed.</strong> The 🔒 Gate
            switch on a session holds every tool call until you allow it, from here
            or from your phone — and it can only do that through the{' '}
            <code>preToolUse</code> hook below.
          </p>
          {hooks && (
            <>
              <div className="settings-row">
                <span className="settings-label">All three installed in</span>
                <span className="settings-value">
                  {hooks.installed.length} of {hooks.eligible.length} agents
                  {hooks.installed.length > 0 && (
                    <span className="settings-detail"> — {hooks.installed.join(', ')}</span>
                  )}
                  {hooks.installed.length === 0 && hooks.eligible.length > 0 && (
                    <span className="settings-detail"> — nothing is hooked, so gating
                      is unavailable and session ids are inferred</span>
                  )}
                </span>
              </div>
              {(hooks.hooks || []).map(h => (
                <div className="settings-row" key={h.event}>
                  <span className="settings-label"><code>{h.event}</code></span>
                  <span className="settings-value settings-detail">
                    {h.installed.length} of {hooks.eligible.length} — {h.purpose}
                    {(h.stale || []).length > 0 && (
                      <span className="settings-warn"> · {h.stale.length} out of date</span>
                    )}
                  </span>
                </div>
              ))}
              {(hooks.stale || []).length > 0 && (
                <div className="settings-row">
                  <span className="settings-label settings-warn">Out of date</span>
                  <span className="settings-value settings-detail">
                    {hooks.stale.join(', ')} — installed, but running an older version.
                    Install again to update them.
                  </span>
                </div>
              )}
              {hooks.cannot_hook.length > 0 && (
                <div className="settings-row">
                  <span className="settings-label">Cannot be hooked</span>
                  <span className="settings-value settings-detail">
                    {hooks.cannot_hook.join(', ')} — built into kiro-cli
                  </span>
                </div>
              )}
              {routeTotal > 0 && (
                <div className="settings-row">
                  <span className="settings-label">Live sessions resolved by</span>
                  <span className="settings-value settings-detail">
                    {Object.entries(routes)
                      .map(([k, v]) => `${v} ${ROUTE_LABELS[k] || k}`).join(', ')}
                  </span>
                </div>
              )}
              <div className="settings-actions">
                <button className="dispatch-btn" disabled={busy}
                        onClick={() => runHookAction('install')}>
                  {busy ? '⟳ Working…' : 'Install into all agents'}
                </button>
                <button className="dispatch-btn dispatch-btn-secondary" disabled={busy}
                        onClick={() => runHookAction('uninstall')}>
                  Remove
                </button>
              </div>
            </>
          )}
          <ConciergeSettings options={options} />
          <AuditSettings />
        </>
      )}

      {tab === 'scripts' && (
        <ScriptsSettings />
      )}

      {tab === 'templates' && (
        <TemplatesSettings />
      )}

      {tab === 'security' && (
        <>
          <SecretsSettings />
          <DenyPatternsSettings />
        </>
      )}

      {tab === 'advanced' && (
        <>
          <h3 className="settings-title">How sessions run</h3>
          <p className="cleanup-hint">
            Sessions dispatched from here run in detached tmux sessions named
            <code> kiro-&lt;id&gt;</code>, so they survive a backend restart.
          </p>
          <p className="cleanup-hint">
            Sessions started by hand elsewhere show as <strong>foreign</strong> and are
            read-only. Taking one over kills its process and restarts it under tmux,
            continuing the same conversation.
          </p>
          <ShellSettings />
          <DiskCleanup />
          <DangerZone />
        </>
      )}
    </div>
  )
}

export default SettingsPanel
