// Tool calls held by the preToolUse hook, waiting for a yes or a no.
import { getJSON, postJSON, post } from './client'

export const listApprovals = () => getJSON('/api/approvals')

export const answerApproval = (requestId, sessionId, allow) =>
  postJSON(`/api/approvals/${requestId}/${allow ? 'allow' : 'deny'}`, { session_id: sessionId })

export const dismissAllApprovals = () => post('/api/approvals/dismiss-all')
