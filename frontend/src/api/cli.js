import { getJSON, postJSON, del } from './client'

/** List all discoverable kiro-cli panes with their status. */
export const listCLI = () => getJSON('/api/cli/list')

/** Get the binding + live status for a session's bound CLI pane. */
export const getCLIStatus = (sessionId) => getJSON(`/api/cli/status/${sessionId}`)

/** Bind a Quarterdeck session to a CLI tmux pane. */
export const bindCLI = (sessionId, tmuxSession) =>
  postJSON('/api/cli/bind', { session_id: sessionId, tmux_session: tmuxSession })

/** Remove the CLI binding for a session. */
export const unbindCLI = (sessionId) => del(`/api/cli/bind/${sessionId}`)

/** Send text to the CLI pane bound to a session. */
export const sendToCLI = (sessionId, text) =>
  postJSON('/api/cli/send', { session_id: sessionId, text })
