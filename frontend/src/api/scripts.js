import { getJSON, postJSON } from './client.js'

export const listScripts = (cwd) =>
  getJSON(`/api/scripts?cwd=${encodeURIComponent(cwd)}`)

export const addScript = (cwd, name, command, description = '', confirm = false) =>
  postJSON('/api/scripts', { cwd, name, command, description, confirm })

export const updateScript = (id, cwd, fields) =>
  fetch(`/api/scripts/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cwd, ...fields }),
  }).then(r => r.json())

export const deleteScript = (id, cwd) =>
  fetch(`/api/scripts/${encodeURIComponent(id)}?cwd=${encodeURIComponent(cwd)}`, {
    method: 'DELETE',
  }).then(r => r.json())

export const runScript = (id, cwd) =>
  postJSON(`/api/scripts/${encodeURIComponent(id)}/run`, { cwd })

export const killScript = (id) =>
  fetch(`/api/scripts/${encodeURIComponent(id)}/run`, { method: 'DELETE' })
    .then(r => r.json())

export const getOutput = (id, after = 0) =>
  getJSON(`/api/scripts/${encodeURIComponent(id)}/output?after=${after}`)

export const detectImports = (cwd) =>
  getJSON(`/api/scripts/imports?cwd=${encodeURIComponent(cwd)}`)
