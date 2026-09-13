import test from 'node:test'
import assert from 'node:assert/strict'
import { partitionByAttention, dockBadgeCount } from './attention.js'

test('idle sessions offer Your turn without a Dock notification', () => {
  const { needsYou } = partitionByAttention([
    { id: 'own', status: 'idle', control: 'managed' },
    { id: 'other', status: 'idle', control: 'foreign' },
  ])
  assert.equal(needsYou.length, 2)
  assert.equal(dockBadgeCount(needsYou), 0)
})

test('only stable approvals and errors count, then clear when resolved', () => {
  const sessions = [
    { id: 'approval', status: 'awaiting-approval' },
    { id: 'transient', status: 'awaiting-approval' },
    { id: 'error', status: 'error' },
    { id: 'busy', status: 'thinking' },
  ]
  const stable = new Set(['approval'])
  const entries = partitionByAttention(sessions, new Map(), stable).needsYou
  assert.equal(dockBadgeCount(entries), 2)
  const resolved = sessions.map(s => ({ ...s, status: 'idle' }))
  assert.equal(dockBadgeCount(partitionByAttention(resolved).needsYou), 0)
  assert.equal(dockBadgeCount([]), 0)
})
