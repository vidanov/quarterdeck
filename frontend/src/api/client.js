// The transport, and the only place in the frontend that knows how a request is
// shaped. Everything above this file talks in endpoint functions.
//
// Same origin, always: the backend serves the built frontend from /app, and the
// vite dev server proxies /api to the dev backend. There is no cross-origin
// case to configure, which is why the base is empty rather than absent.
export const API = ''

// Parse a response as JSON. On HTTP error, try to read a JSON error body and
// re-throw with the real message; on a parse failure, throw a typed error that
// callers can distinguish from a network rejection.
const json = async (r) => {
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try {
      const body = await r.json()
      if (body && body.error) msg = body.error
      else if (body && body.detail) msg = body.detail
    } catch { /* body was not JSON — keep the status code message */ }
    const err = new Error(msg)
    err.status = r.status
    throw err
  }
  return r.json()
}

// Endpoints answer with 200 and an `error` key rather than an HTTP status, so a
// refusal is invisible unless the body is read. Returns the message, or null
// when the call succeeded.
export const errorOf = (body) => (body && typeof body === 'object' && body.error) || null

// Reads get a deadline. Without one, a request that never comes back — a
// backend whose thread pool is starved, a tmux call wedged behind a hung server
// — leaves the caller holding an unsettled promise. The session poll schedules
// its next tick from `.then`, so one hung read stopped the polling loop
// altogether: no error, no updates, a UI that just quietly stopped.
//
// GET only. A mutation may already have been applied by the time a client gives
// up on it, and aborting would leave the two disagreeing about whether it
// happened, so those are left to finish.
const READ_TIMEOUT_MS = 20000

const readSignal = () => {
  if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    return AbortSignal.timeout(READ_TIMEOUT_MS)
  }
  const controller = new AbortController()
  setTimeout(() => controller.abort(), READ_TIMEOUT_MS)
  return controller.signal
}

export const getJSON = (path) => fetch(`${API}${path}`, { signal: readSignal() }).then(json)

// Turns a non-2xx into a throw. Only for callers that report HTTP failure to
// the user; every other endpoint reports refusal in the body — see errorOf.
export const getJSONStrict = (path) =>
  fetch(`${API}${path}`, { signal: readSignal() }).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })

const withBody = (method) => (path, body) =>
  fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }).then(json)

export const postJSON = withBody('POST')
export const patchJSON = withBody('PATCH')

// POST with no body at all, for the endpoints that take none.
export const post = (path) => fetch(`${API}${path}`, { method: 'POST' }).then(json)

export const del = (path) => fetch(`${API}${path}`, { method: 'DELETE' }).then(json)
