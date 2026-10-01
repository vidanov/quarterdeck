// Quick chat (⌘J): one global, always-on assistant for short questions.
import { getJSON, postJSON } from './client'

export const open = () => postJSON('/api/quick-chat/open', {})
export const send = (text) => postJSON('/api/quick-chat/send', { text })
export const key = (k) => postJSON('/api/quick-chat/key', { key: k })
export const poll = () => getJSON('/api/quick-chat/poll')
export const close = () => postJSON('/api/quick-chat/close', {})
