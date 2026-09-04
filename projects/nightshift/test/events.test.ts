import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseEventLine, serialize } from '../src/events.js'

test('a serialized event round-trips with its type intact', () => {
  const line = serialize({ type: 'turn.completed', epoch: 1, turn: 2, toolCalls: 3 })
  const event = parseEventLine(line, '2026-01-01T00:00:00Z')
  assert.equal(event.type, 'turn.completed')
  assert.equal(event.observedAt, '2026-01-01T00:00:00Z')
})

test('an unparseable line is preserved as host.unparsed, not discarded', () => {
  const event = parseEventLine('not json at all', '2026-01-01T00:00:00Z')
  assert.equal(event.type, 'host.unparsed')
  assert.equal((event as { text: string }).text, 'not json at all')
})

test('the host arrival time is used when the line has no timestamp', () => {
  const event = parseEventLine(JSON.stringify({ type: 'message', epoch: 1, turn: 1, text: 'hi' }), 'HOST')
  assert.equal(event.timestamp, 'HOST')
})
