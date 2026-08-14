// Profile switching: save/restore kiro-cli auth credentials.
import { getJSON, postJSON, post } from './client'

export const listProfiles = () => getJSON('/api/profiles')
export const currentProfile = () => getJSON('/api/profiles/current')
export const saveProfile = (name) => postJSON('/api/profiles/save', { name })
export const switchProfile = (name) => postJSON('/api/profiles/switch', { name })
export const deleteProfile = (name) => postJSON('/api/profiles/delete', { name })

export const restartVisibleSessions = (ids) => postJSON('/api/sessions/restart-visible', { ids })
export const kiroLogin = (opts = {}) => postJSON('/api/kiro/login', opts)
export const kiroLogout = () => post('/api/kiro/logout')
