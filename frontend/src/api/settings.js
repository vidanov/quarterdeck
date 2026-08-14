// Everything behind the gear: settings, hooks, remote access, the shell, the
// concierge, and the audit trail. Plus the two folder pickers, which belong
// with them because they are host-machine affordances rather than session data.
import { getJSON, postJSON, post } from './client'

export const getSettings = () => getJSON('/api/settings')
export const saveSettings = (patch) => postJSON('/api/settings', patch)

// Model / effort / command lists the backend will actually accept, so the UI
// never offers a value kiro-cli would reject.
export const getOptions = () => getJSON('/api/options')
export const getCwdSuggestion = () => getJSON('/api/cwd-suggestion')

// A directory can carry its own agents in .kiro/agents, and one of those
// shadows a global agent of the same name — so the list depends on the cwd.
export const getAgents = (cwd) => getJSON(`/api/agents?cwd=${encodeURIComponent(cwd)}`)

export const pickFolder = () => post('/api/pick-folder')
export const openFolder = (path) => postJSON('/api/open-folder', { path })

export const getHooksStatus = () => getJSON('/api/hooks/status')
export const runHookAction = (action) => postJSON(`/api/hooks/${action}`, {})

export const getAudit = (limit = 40) => getJSON(`/api/audit?limit=${limit}`)
export const setAuditEnabled = (enabled) => postJSON('/api/audit/enabled', { enabled })

export const getRemoteStatus = () => getJSON('/api/remote/status')
// action is start / stop / rotate.
export const remoteAction = (action) => post(`/api/remote/${action}`)
export const getRemoteToken = () => getJSON('/api/remote/token')
export const installLaunchAgent = () => post('/api/remote/launchagent/install')
export const uninstallLaunchAgent = () => post('/api/remote/launchagent/uninstall')

export const getShellPane = () => getJSON('/api/shell/pane')
export const resizeShell = (cols, rows) => postJSON('/api/shell/resize', { cols, rows })
// path is open / input / key / close.
export const shellAction = (path, body) => postJSON(`/api/shell/${path}`, body || {})

export const askAssistant = (query) => postJSON('/api/assist', { query })
export const getAssistActivity = () => getJSON('/api/assist/activity')
export const getAssistStatus = () => getJSON('/api/assist/status')
export const restartAssist = () => post('/api/assist/restart')
export const stopAssist = () => post('/api/assist/stop')

// --- Per-device tokens ---
export const listDevices = () => getJSON('/api/devices')
export const createDevice = (name) => postJSON('/api/devices', { name })
export const revokeDevice = (deviceId) => post(`/api/devices/${deviceId}/revoke`)
export const renameDevice = (deviceId, name) => postJSON(`/api/devices/${deviceId}/rename`, { name })
