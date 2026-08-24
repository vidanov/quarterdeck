// What a session wants from you, in those terms rather than in its own.
//
// A status is a fact about the agent; an action is a fact about you, and the
// grid is read to answer "what needs me?" — not "what state is process 7 in?".
// `rank` orders the ones that do: blocked beats finished, because a blocked
// agent is burning nothing but is stopped dead, and a finished one has already
// delivered.
//
// This lives outside any view on purpose. Every layout has to reach the same
// verdict about the same session, or the grid and the next view will disagree
// about which of them is shouting — see roadmap 7b, "shared attention path".
//
// `stableAwaitingIds` — optional Set of session ids that have been in
// awaiting-approval long enough to be shown (debounced). When provided, a
// session in awaiting-approval is NOT treated as needs=true until its id
// appears in this set. This suppresses false-positive "Needs you" banners
// for auto-approved tool calls that resolve within the debounce window.
export function attentionOf(session, held = false, stableAwaitingIds = null) {
  const { status, control } = session
  if (control === 'starting') {
    return { needs: false, action: 'Starting…', rank: 90 }
  }
  if (status === 'awaiting-approval') {
    // If a debounce set is provided, only raise attention once the session
    // has been waiting long enough to be in the stable set.
    const stable = stableAwaitingIds === null || stableAwaitingIds.has(session.id)
    if (!stable) return { needs: false, action: 'Working', rank: 91 }
    return held
      ? { needs: true, rank: 0, action: 'Tool call held' }
      : { needs: true, rank: 1, action: 'Needs permission' }
  }
  if (status === 'error') {
    return { needs: true, rank: 2, action: 'Error' }
  }
  if (status === 'idle') {
    return control === 'managed'
      ? { needs: true, rank: 3, action: 'Your turn' }
      : { needs: true, rank: 4, action: 'Finished elsewhere' }
  }
  return { needs: false, action: 'Working', rank: 91 }
}

// Split a list into what needs you, most urgent first, and what does not.
// `held` is the map of sessions with a tool call waiting on an answer — a
// held call outranks everything, because the agent is stopped until it is
// answered.
// `stableAwaitingIds` — debounced set; see attentionOf above.
export function partitionByAttention(sessions, held, stableAwaitingIds = null) {
  const withAttention = sessions.map(s => ({
    s,
    a: attentionOf(s, !!held?.has(s.id), stableAwaitingIds),
  }))
  return {
    needsYou: withAttention.filter(x => x.a.needs).sort((x, y) => x.a.rank - y.a.rank),
    working: withAttention.filter(x => !x.a.needs),
  }
}
