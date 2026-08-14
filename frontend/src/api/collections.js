// The three overlapping ways sessions are grouped today — snapshots,
// favourites, archive — plus projects, stats and cleanup, which read the same
// history from the other end. Roadmap section 6 folds the first three into one
// concept; keeping them in one module is the first step towards that.
import { getJSON, getJSONStrict, postJSON, del } from './client'

// --- Collections (section 6) ---
export const getCollections = () => getJSON('/api/collections')
export const getCollectionsEnriched = () => getJSON('/api/collections/enriched')
export const listCollectionsEnriched = getCollectionsEnriched
export const getCollection = (id) => getJSON(`/api/collections/${id}`)
export const getCollectionEnriched = (id) => getJSON(`/api/collections/${id}/enriched`)
export const createCollection = (name, opts = {}) => postJSON('/api/collections', { name, ...opts })
export const renameCollection = (id, name) => postJSON(`/api/collections/${id}/rename`, { name })
export const deleteCollection = (id) => del(`/api/collections/${id}`)
export const addMember = (id, member) => postJSON(`/api/collections/${id}/members`, member)
export const removeMember = (collId, sessionId) => postJSON(`/api/collections/${collId}/members/remove`, { session_id: sessionId })
export const removeCollectionMember = removeMember
export const reorderMembers = (id, sessionIds) => postJSON(`/api/collections/${id}/reorder`, { session_ids: sessionIds })
export const startCollection = (id) => postJSON(`/api/collections/${id}/start`, {})

// --- Legacy (kept for backward compat, used by concierge) ---
export const getSnapshots = () => getJSON('/api/snapshots')
export const saveSnapshots = (snapshots) => postJSON('/api/snapshots', { snapshots })

export const getFavourites = () => getJSON('/api/favourites')
export const addFavourite = (id) => postJSON('/api/favourites/add', { id })
export const removeFavourite = (id) => postJSON('/api/favourites/remove', { id })
export const purgeStalesFavourites = () => postJSON('/api/favourites/purge-stale', {})

export const searchArchive = (query, limit = 50) =>
  getJSON(`/api/archive?q=${encodeURIComponent(query)}&limit=${limit}`)

// The scan walks every session file and takes tens of seconds, so the result is
// cached server-side. Strict, because this is the one view that reports an HTTP
// failure to the user rather than quietly showing nothing.
export const getProjects = (refresh = false) =>
  getJSONStrict(`/api/projects${refresh ? '?refresh=true' : ''}`)

export const previewProjectDelete = (cwd) => postJSON('/api/projects/delete-preview', { cwd })
export const deleteProjectSessions = (cwd) => postJSON('/api/projects/delete', { cwd })

export const getStats = ({ period, from, to } = {}) => {
  let path = `/api/stats?period=${period}`
  if (period === 'custom') {
    if (from) path += `&date_from=${from}`
    if (to) path += `&date_to=${to}`
  }
  return getJSON(path)
}

export const getCleanupPreview = () => getJSON('/api/cleanup/preview')
export const applyCleanup = (sessionIds) => postJSON('/api/cleanup/apply', { session_ids: sessionIds })
