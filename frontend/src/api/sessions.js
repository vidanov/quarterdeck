// Sessions: listing, driving, and the queue attached to each one.
import { getJSON, postJSON, patchJSON, post, del } from './client'

export const listSessions = () => getJSON('/api/sessions')
export const getSession = (id) => getJSON(`/api/sessions/${id}`)

// `lines` is deliberately far more than fits on screen — tmux keeps scrollback
// and reading back over it is most of the point of the Live view.
export const getPane = (id, lines) => getJSON(`/api/sessions/${id}/pane?lines=${lines}`)

export const sendInput = (id, text, attachments = []) =>
  postJSON(`/api/sessions/${id}/input`, { text, attachments })

// A choice is either a menu answer (allow / trust / deny / dismiss) or a raw
// key to move around inside it (Up, Down, Enter, Escape, C-c).
export const respond = (id, choice) => postJSON(`/api/sessions/${id}/respond`, { choice })

export const resizeSession = (id, cols, rows) =>
  postJSON(`/api/sessions/${id}/resize`, { cols, rows })

export const renameSession = (id, title) => postJSON(`/api/sessions/${id}/rename`, { title })
export const killSession = (id) => post(`/api/sessions/${id}/kill`)
export const resumeSession = (id) => post(`/api/sessions/${id}/resume`)
export const takeoverSession = (id) => post(`/api/sessions/${id}/takeover`)
export const branchSession = (id) => post(`/api/sessions/${id}/branch`)
export const branchAt = (id, afterSeq) => postJSON(`/api/sessions/${id}/branch-at`, { after_seq: afterSeq })
export const getMessages = (id, after = -1, limit = 200) =>
  getJSON(`/api/sessions/${id}/messages?after=${after}&limit=${limit}`)
export const deleteSession = (id) => post(`/api/sessions/${id}/delete`)
export const handoffSession = (id, terminal) =>
  postJSON(`/api/sessions/${id}/handoff`, { terminal })

// Whether kiro-cli holds this session's tool calls until they are answered.
// Server-side state, so the phone and the desktop see the same answer.
export const getGate = (id) => getJSON(`/api/sessions/${id}/gate`)
export const setGate = (id, enabled) => postJSON(`/api/sessions/${id}/gate`, { enabled })

export const getTrust = (id) => getJSON(`/api/sessions/${id}/trust`)
export const setTrust = (id, minutes = 30) => postJSON(`/api/sessions/${id}/trust`, { minutes })
export const revokeTrust = (id) => del(`/api/sessions/${id}/trust`)

export const getStack = (id) => getJSON(`/api/sessions/${id}/stack`)
export const addStackItem = (id, text) => postJSON(`/api/sessions/${id}/stack`, { text })
export const editStackItem = (id, itemId, text) =>
  patchJSON(`/api/sessions/${id}/stack/${itemId}`, { text })
export const deleteStackItem = (id, itemId) => del(`/api/sessions/${id}/stack/${itemId}`)
export const reorderStack = (id, ids) => postJSON(`/api/sessions/${id}/stack/reorder`, { ids })
export const sendNextStackItem = (id) => post(`/api/sessions/${id}/stack/send-next`)
export const getAutoAdvance = (id) => getJSON(`/api/sessions/${id}/stack/auto-advance`)
export const setAutoAdvance = (id, enabled) =>
  postJSON(`/api/sessions/${id}/stack/auto-advance`, { enabled })

// Steering delivery — which rules are configured vs. probe-confirmed delivered
export const getDelivery = (id) => getJSON(`/api/sessions/${id}/delivery`)
export const recordProbe = (id, payload) => postJSON(`/api/sessions/${id}/delivery/probe`, payload)

// Spawning. The request is forwarded whole rather than field by field: a
// hand-listed subset silently dropped `agent` and `pre_command` once already.
export const dispatch = (request) => postJSON('/api/dispatch', request)

// A spawn that never reported a session id has only its nonce to cancel by.
export const cancelPending = (nonce) => post(`/api/pending/${nonce}/cancel`)

// Slash command queue
export const getSlashQueue = (id) => getJSON(`/api/sessions/${id}/slash-queue`)
export const pushSlashQueue = (id, text) => postJSON(`/api/sessions/${id}/slash-queue`, { text })
export const deleteSlashQueueItem = (id, itemId) => del(`/api/sessions/${id}/slash-queue/${itemId}`)

export const summarize = (id) => postJSON(`/api/sessions/${id}/summarize`, {})
export const dismissSession = (id) => post(`/api/sessions/${id}/dismiss`)

// Corrections
export const addCorrection = (id, payload = {}) => postJSON(`/api/sessions/${id}/corrections`, payload)
export const updateCorrection = (correctionId, status, note = '') =>
  patchJSON(`/api/corrections/${correctionId}`, { status, note })
export const getCorrections = (id) => getJSON(`/api/sessions/${id}/corrections`)

// Paste store
export const createPaste = (text, sessionId = null, name = null) =>
  postJSON('/api/pastes', { text, session_id: sessionId, name })
export const getPasteText = (sessionId, name) =>
  getJSON(`/api/pastes/${encodeURIComponent(sessionId)}/${encodeURIComponent(name)}`)
export const deletePaste = (sessionId, name) =>
  fetch(`/api/pastes/${encodeURIComponent(sessionId)}/${encodeURIComponent(name)}`,
    { method: 'DELETE' }).then(r => r.json())