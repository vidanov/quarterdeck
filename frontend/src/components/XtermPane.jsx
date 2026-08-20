/**
 * XtermPane — wraps xterm.js and connects it to the backend PTY WebSocket.
 *
 * Props:
 *   shellId    {string}   — the shell_id (used to build the WS URL)
 *   cwd        {string}   — working directory (sent in the open message)
 *   active     {boolean}  — if false, terminal is hidden but kept alive
 *   localToken {string}   — X-Local-Token for auth
 *   onReady    {function} — called when the PTY session confirms ready
 *   onDead     {function} — called when the connection closes
 */
import React, { useEffect, useRef, useCallback } from 'react'
import { Terminal } from 'xterm'
import { FitAddon } from '@xterm/addon-fit'
import 'xterm/css/xterm.css'

const RECONNECT_DELAY = 2000

export default React.memo(function XtermPane({ shellId, cwd, active, localToken, onReady, onDead, cmdToSend, onCmdSent }) {
  const containerRef = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const mountedRef = useRef(false)
  // Stable refs for callbacks so connect() doesn't recreate when parent re-renders
  const onReadyRef = useRef(onReady)
  const onDeadRef = useRef(onDead)
  useEffect(() => { onReadyRef.current = onReady }, [onReady])
  useEffect(() => { onDeadRef.current = onDead }, [onDead])

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    // Close any existing connection before opening a new one
    const existing = wsRef.current
    if (existing && existing.readyState <= WebSocket.OPEN) {
      existing.onclose = null  // prevent reconnect loop
      existing.close()
    }

    // Build WS URL — same host, swap http→ws
    const base = window.location.origin.replace(/^http/, 'ws')
    const token = localToken || window._qdLocalToken || ''
    const url = `${base}/api/pty/${encodeURIComponent(shellId)}/ws?token=${encodeURIComponent(token)}`

    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return }
      // Send open message with current terminal dimensions
      const term = termRef.current
      ws.send(JSON.stringify({
        type: 'open',
        cwd: cwd || '~',
        cols: term ? term.cols : 220,
        rows: term ? term.rows : 50,
      }))
    }

    ws.onmessage = (e) => {
      if (!mountedRef.current) return
      const term = termRef.current
      if (!term) return
      if (e.data instanceof ArrayBuffer) {
        // Raw PTY bytes → write directly to xterm
        term.write(new Uint8Array(e.data))
      } else {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'ready') {
            if (onReadyRef.current) onReadyRef.current(msg.shell_id)
          } else if (msg.type === 'error') {
            term.write(`\r\n\x1b[31m[error: ${msg.error}]\x1b[0m\r\n`)
          }
        } catch { /* binary text fallback */ }
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      if (onDeadRef.current) onDeadRef.current()
      // Auto-reconnect after delay
      reconnectTimer.current = setTimeout(() => {
        if (mountedRef.current) connect()
      }, RECONNECT_DELAY)
    }

    ws.onerror = () => {
      ws.close()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shellId, cwd, localToken])  // stable: onReady/onDead accessed via refs

  // Mount terminal
  useEffect(() => {
    mountedRef.current = true
    const container = containerRef.current
    if (!container) return

    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: 'block',
      fontFamily: "'SF Mono', 'Menlo', 'Consolas', monospace",
      fontSize: 13,
      lineHeight: 1.4,
      theme: {
        background: '#ffffff',
        foreground: '#0f172a',
        cursor: '#3b82f6',
        cursorAccent: '#ffffff',
        selectionBackground: 'rgba(59,130,246,0.3)',
        black:   '#1e293b',
        red:     '#ef4444',
        green:   '#16a34a',
        yellow:  '#ca8a04',
        blue:    '#2563eb',
        magenta: '#9333ea',
        cyan:    '#0891b2',
        white:   '#e2e8f0',
        brightBlack:   '#64748b',
        brightRed:     '#f87171',
        brightGreen:   '#4ade80',
        brightYellow:  '#fbbf24',
        brightBlue:    '#60a5fa',
        brightMagenta: '#c084fc',
        brightCyan:    '#22d3ee',
        brightWhite:   '#f8fafc',
      },
      allowTransparency: false,
      scrollback: 1000,
    })

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(container)
    fitAddon.fit()

    termRef.current = term
    fitRef.current = fitAddon

    // Forward keystrokes to WebSocket
    term.onData(data => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'data', data }))
      }
    })

    // Resize observer
    const ro = new ResizeObserver(() => {
      try {
        fitAddon.fit()
        const ws = wsRef.current
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
      } catch {}
    })
    ro.observe(container)

    connect()

    return () => {
      mountedRef.current = false
      clearTimeout(reconnectTimer.current)
      ro.disconnect()
      const ws = wsRef.current
      if (ws) { ws.onclose = null; ws.close() }
      term.dispose()
      termRef.current = null
      fitRef.current = null
      wsRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shellId])   // re-mount only if shellId changes

  // Focus terminal when active
  useEffect(() => {
    if (active && termRef.current) {
      termRef.current.focus()
    }
  }, [active])

  // Send a command from the external input field
  useEffect(() => {
    if (!cmdToSend) return
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Append \r only for plain text commands, not control sequences
      const isControlSeq = cmdToSend.length <= 4 && cmdToSend.charCodeAt(0) < 32
      const payload = isControlSeq ? cmdToSend : cmdToSend + '\r'
      ws.send(JSON.stringify({ type: 'data', data: payload }))
    }
    if (onCmdSent) onCmdSent()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cmdToSend])

  return (
    <div
      ref={containerRef}
      className="xterm-pane"
      style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
    />
  )
})
