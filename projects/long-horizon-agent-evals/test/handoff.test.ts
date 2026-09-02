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
