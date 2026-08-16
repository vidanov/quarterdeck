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
export function attentionOf(session, held = false) {
  const { status, control } = session
  if (control === 'starting') {
    return { needs: false, action: 'Starting…', rank: 90 }
  }
  if (status === 'awaiting-approval') {
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
export function partitionByAttention(sessions, held) {
  const withAttention = sessions.map(s => ({ s, a: attentionOf(s, !!held?.has(s.id)) }))
  return {
    needsYou: withAttention.filter(x => x.a.needs).sort((x, y) => x.a.rank - y.a.rank),
    working: withAttention.filter(x => !x.a.needs),
  }
}
