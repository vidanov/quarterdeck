import { getJSON, postJSON, del } from './client'

export const listShells = () => getJSON('/api/shells')
export const openShell = (cwd) => postJSON('/api/shells/open', { cwd })
export const getShellPane = (shellId, lines) =>
  getJSON(`/api/shells/${shellId}/pane${lines ? `?lines=${lines}` : ''}`)
export const shellInput = (shellId, text) =>
  postJSON(`/api/shells/${shellId}/input`, { text })
export const shellKey = (shellId, key) =>
  postJSON(`/api/shells/${shellId}/key`, { key })
export const shellResize = (shellId, cols, rows) =>
  postJSON(`/api/shells/${shellId}/resize`, { cols, rows })
export const closeShell = (shellId) => del(`/api/shells/${shellId}`)
