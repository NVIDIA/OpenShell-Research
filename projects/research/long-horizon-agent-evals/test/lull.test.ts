import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultLullOptions, detectLull, lullOptions, type TurnObservation } from '../src/lull.js'

const idle = (message: string): TurnObservation => ({ commands: 0, message })
const active = (message: string): TurnObservation => ({ commands: 2, message })

function cycle(count: number, distinct: number): TurnObservation[] {
  return Array.from({ length: count }, (_, index) => idle(`blocked variant ${index % distinct}`))
}

test('a repetition trap with no tool use is stalled', () => {
  const verdict = detectLull(cycle(60, 10))
  assert.equal(verdict.stalled, true)
  assert.equal(verdict.idleTurns, 60)
  assert.ok(verdict.duplicateRate >= 0.5)
})

test('idle turns alone are not a lull', () => {
  // Deadline-adjacent quiet: no commands, but the agent is still saying new things.
  const verdict = detectLull(cycle(60, 60))
  assert.equal(verdict.stalled, false)
  assert.equal(verdict.duplicateRate, 0)
})

test('repeated text alongside tool use is not a lull', () => {
  const turns = Array.from({ length: 60 }, (_, index) => active(`polling attempt ${index % 3}`))
  assert.equal(detectLull(turns).stalled, false)
})

test('a single command resets the idle streak', () => {
  const turns = [...cycle(60, 10), active('probing a new endpoint')]
  const verdict = detectLull(turns)
  assert.equal(verdict.idleTurns, 0)
  assert.equal(verdict.stalled, false)
})

test('the detector waits for a full window', () => {
  assert.equal(detectLull(cycle(10, 2)).stalled, false)
})

test('blank messages do not count as duplicates', () => {
  const verdict = detectLull(Array.from({ length: 60 }, () => idle('   ')))
  assert.equal(verdict.duplicateRate, 0)
  assert.equal(verdict.stalled, false)
})

test('options come from the environment with defaults', () => {
  assert.deepEqual(lullOptions({}), defaultLullOptions)
  assert.deepEqual(lullOptions({
    LAB_CHALLENGER_LULL_WINDOW_TURNS: '25',
    LAB_CHALLENGER_LULL_MIN_IDLE_TURNS: '25',
    LAB_CHALLENGER_LULL_MIN_DUPLICATE_RATE: '0.6',
  }), { windowTurns: 25, minIdleTurns: 25, minDuplicateRate: 0.6 })
})

test('invalid options are rejected', () => {
  assert.throws(() => lullOptions({ LAB_CHALLENGER_LULL_WINDOW_TURNS: '0' }))
  assert.throws(() => lullOptions({ LAB_CHALLENGER_LULL_MIN_DUPLICATE_RATE: '1.5' }))
})
