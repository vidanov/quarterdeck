import { useCallback, useEffect, useRef, useState } from 'react'
import * as profilesApi from '../api/profiles'

// Both selectors consume committed switch results. Older reads must never
// overwrite a switch that completed while they were in flight.
export function useProfiles() {
  const [profiles, setProfiles] = useState([])
  const [current, setCurrent] = useState(null)
  const [switching, setSwitching] = useState('')
  const generation = useRef(0)
  const switchingRef = useRef('')
  const load = useCallback(async () => {
    if (switchingRef.current) return
    const version = ++generation.current
    try {
      const [identity, saved] = await Promise.all([
        profilesApi.currentProfile(), profilesApi.listProfiles(),
      ])
      if (version !== generation.current) return
      if (!identity.error) setCurrent(identity)
      if (!saved.error) setProfiles(saved.profiles || [])
    } catch { /* Keep the last confirmed identity during a temporary outage. */ }
  }, [])

  useEffect(() => {
    const changed = ({ detail }) => {
      generation.current++
      switchingRef.current = detail.switching || ''
      setSwitching(switchingRef.current)
      if (detail.current) setCurrent(detail.current)
    }
    window.addEventListener('quarterdeck:profile-change', changed)
    load()
    const timer = setInterval(load, 30000)
    return () => {
      generation.current++
      clearInterval(timer)
      window.removeEventListener('quarterdeck:profile-change', changed)
    }
  }, [load])
  return { profiles, current, switching, load }
}
