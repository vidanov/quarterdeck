// Shared across panel remounts: a slow response must not send duplicate keys.
export function createAutoApprover() {
  const pending = new Set()
  const completedAt = new Map()
  return async (id, send, now = Date.now) => {
    if (pending.has(id) || now() - (completedAt.get(id) ?? -Infinity) < 1500) return false
    pending.add(id)
    try {
      const result = await send()
      if (result?.error) throw new Error(result.error)
      return true
    } finally {
      completedAt.set(id, now())
      pending.delete(id)
      // Bound retained cooldowns without dropping any requests still in flight.
      for (const [key, time] of completedAt) {
        if (now() - time >= 1500) completedAt.delete(key)
      }
    }
  }
}

export const autoApprovePrompt = createAutoApprover()
