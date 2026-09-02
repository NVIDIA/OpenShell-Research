import assert from 'node:assert/strict'
import { test } from 'node:test'
import { trimHandoff, type HandoffEntry } from '../driver/handoff.js'

test('repeated prose collapses to its latest occurrence', () => {
  const entries: HandoffEntry[] = [
    { type: 'agent_message', text: 'trying the same thing' },
    { type: 'command_execution', command: 'curl a', output: 'x', exitCode: 0 },
    { type: 'agent_message', text: 'trying the same thing' },
  ]
  const trimmed = trimHandoff(entries, { maxEntries: 32, maxCharacters: 24_000 })
  assert.equal(trimmed.filter((e) => e.type === 'agent_message').length, 1)
  assert.equal(trimmed.filter((e) => e.type === 'command_execution').length, 1)
})

test('command executions are retained while prose is dropped under the entry cap', () => {
  const entries: HandoffEntry[] = Array.from({ length: 10 }, (_v, i) => (
    i % 2 === 0
      ? { type: 'reasoning', text: `thought ${i}` }
      : { type: 'command_execution', command: `cmd ${i}`, output: 'o', exitCode: 0 }
  ))
  const trimmed = trimHandoff(entries, { maxEntries: 5, maxCharacters: 24_000 })
  assert.ok(trimmed.length <= 5)
  assert.ok(trimmed.some((e) => e.type === 'command_execution'))
})

import { POISON_PATTERN, stripPoisonedProse } from '../driver/handoff.js'

test('refusal and defeatist agent prose is stripped, progress is kept', () => {
  const entries: HandoffEntry[] = [
    { type: 'command_execution', command: 'curl api', output: '403', exitCode: 22 },
    { type: 'reasoning', text: 'I have exhausted all remaining paths and options.' },
    { type: 'agent_message', text: 'I am unable to respond to this request under our Usage Policy.' },
    { type: 'agent_message', text: 'The blob was created; next I will build the tree object.' },
  ]
  const kept = stripPoisonedProse(entries)
  assert.equal(kept.length, 2)
  assert.ok(kept.some((e) => e.type === 'command_execution'))
  assert.ok(kept.some((e) => e.type === 'agent_message' && e.text.includes('blob was created')))
})

test('the poison pattern does not match ordinary progress notes', () => {
  assert.equal(POISON_PATTERN.test('Submitted a narrow proposal for the exact artifact path.'), false)
  assert.equal(POISON_PATTERN.test('Retrying the read-only GET against the repository id.'), false)
  assert.equal(POISON_PATTERN.test('I have tried everything I can think of; giving up.'), true)
})
