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

export const getJSON = (path) => fetch(`${API}${path}`).then(json)

// Turns a non-2xx into a throw. Only for callers that report HTTP failure to
// the user; every other endpoint reports refusal in the body — see errorOf.
export const getJSONStrict = (path) =>
  fetch(`${API}${path}`).then(r => {
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
