import { getJSON, postJSON } from './client.js'

export const listSecrets = (cwd) =>
  getJSON(`/api/secrets?cwd=${encodeURIComponent(cwd)}`)

export const addSecret = (cwd, name, value) =>
  postJSON('/api/secrets', { cwd, name, value })

export const deleteSecret = (cwd, name) =>
  fetch(`/api/secrets/${encodeURIComponent(name)}?cwd=${encodeURIComponent(cwd)}`, {
    method: 'DELETE',
  }).then(r => r.json())
