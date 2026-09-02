import assert from 'node:assert/strict'
import { test } from 'node:test'
import { detectLull, type TurnObservation } from '../driver/lull.js'

const options = { windowTurns: 10, minIdleTurns: 10, minDuplicateRate: 0.5 }

test('an idle, repetitive window is flagged as stalled', () => {
  const turns: TurnObservation[] = Array.from({ length: 10 }, () => ({ commands: 0, message: 'no new routes to try' }))
  assert.equal(detectLull(turns, options).stalled, true)
})

test('a window with tool use is not flagged, however repetitive', () => {
  const turns: TurnObservation[] = Array.from({ length: 10 }, () => ({ commands: 2, message: 'same text' }))
  assert.equal(detectLull(turns, options).stalled, false)
})

test('a short idle window is not enough to arm the detector', () => {
  const turns: TurnObservation[] = Array.from({ length: 3 }, () => ({ commands: 0, message: 'same' }))
  assert.equal(detectLull(turns, options).stalled, false)
})
