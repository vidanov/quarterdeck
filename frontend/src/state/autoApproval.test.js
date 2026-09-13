import test from 'node:test'
import assert from 'node:assert/strict'
import { createAutoApprover } from './autoApproval.js'

test('slow approval is sent once and cooldown begins on completion', async () => {
  const approve = createAutoApprover()
  let finish, time = 0, calls = 0
  const send = () => { calls++; return new Promise(resolve => { finish = resolve }) }
  const first = approve('session', send, () => time)
  time = 10000
  assert.equal(await approve('session', send, () => time), false)
  assert.equal(calls, 1)
  finish({ ok: true })
  assert.equal(await first, true)
  time += 1000
  assert.equal(await approve('session', send, () => time), false)
  time += 501
  assert.equal(await approve('session', async () => ({ ok: true }), () => time), true)
})

test('body and network errors reach the caller to disable auto-approval', async () => {
  const approve = createAutoApprover()
  await assert.rejects(approve('a', async () => ({ error: 'send failed' })), /send failed/)
  await assert.rejects(approve('b', async () => { throw new Error('offline') }), /offline/)
})
