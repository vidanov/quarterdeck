import { getJSON, postJSON, patchJSON, del } from './client.js'

export const listPatterns = () => getJSON('/api/deny-patterns')
export const addPattern = (tool, pattern, note) => postJSON('/api/deny-patterns', { tool, pattern, note })
export const setEnabled = (id, enabled) => patchJSON(`/api/deny-patterns/${id}`, { enabled })
export const deletePattern = (id) => del(`/api/deny-patterns/${id}`)
export const listPacks = () => getJSON('/api/deny-patterns/packs')
export const installPack = (packId) => postJSON(`/api/deny-patterns/packs/${packId}/install`, {})
export const removePack = (packId) => del(`/api/deny-patterns/packs/${packId}`)
