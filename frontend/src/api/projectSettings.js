import { getJSON, postJSON } from './client.js'

export const getProjectSettings = (cwd) =>
  getJSON(`/api/project-settings?cwd=${encodeURIComponent(cwd)}`)

export const saveProjectSettings = (cwd, data) =>
  postJSON('/api/project-settings', { cwd, ...data })
