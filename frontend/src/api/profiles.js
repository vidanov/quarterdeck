// Profile switching: save/restore kiro-cli auth credentials.
import { getJSON, postJSON, post } from './client'

export const listProfiles = () => getJSON('/api/profiles')
export const currentProfile = () => getJSON('/api/profiles/current')
export const saveProfile = (name) => postJSON('/api/profiles/save', { name })
let switchPending = false
const announce = (detail) => window.dispatchEvent(new CustomEvent('quarterdeck:profile-change', { detail }))
export const switchProfile = async (name) => {
  if (switchPending) return { error: 'A profile switch is already in progress.' }
  switchPending = true
  announce({ switching: name })
  try {
    const result = await postJSON('/api/profiles/switch', { name })
    if (result.ok && !result.error) {
      announce({ current: { ...result, active_profile: result.active_profile || result.name } })
    }
    return result
  } finally {
    switchPending = false
    announce({ switching: '' })
  }
}
export const deleteProfile = (name) => postJSON('/api/profiles/delete', { name })

export const restartVisibleSessions = (ids) => postJSON('/api/sessions/restart-visible', { ids })
export const kiroLogin = (opts = {}) => postJSON('/api/kiro/login', opts)
export const kiroLogout = () => post('/api/kiro/logout')
