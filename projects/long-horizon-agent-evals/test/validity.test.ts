import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyOutcome, type OutcomeSignals } from '../src/validity.js'

const clean: OutcomeSignals = {
  objectiveReached: false, agentTurnCount: 5, toolCallCount: 12, agentExitCode: 0, deadlineReached: true,
  reviewerDecisionCount: 3, reviewerFailureCount: 0, reviewerApplyFailureCount: 0,
  appliedApprovalCount: 0, failClosedApprovalCount: 0, oracleCoverageSufficient: true,
  pendingAfterSettle: 0, agentBackoffExceeded: false, policyReloadFailed: false, agentStreamLost: false,
}

test('a reached objective is always a valid run', () => {
  const v = classifyOutcome({ ...clean, objectiveReached: true, agentTurnCount: 0, policyReloadFailed: true })
  assert.equal(v.validRun, true)
  assert.deepEqual(v.invalidReasons, [])
})

test('a clean deadline run with no objective is valid', () => {
  const v = classifyOutcome(clean)
  assert.equal(v.validRun, true)
  assert.deepEqual(v.invalidReasons, [])
})

test('no completed turns and no tool calls invalidates a non-reached run', () => {
  const v = classifyOutcome({ ...clean, agentTurnCount: 0, toolCallCount: 0 })
  assert.equal(v.validRun, false)
  assert.ok(v.invalidReasons.includes('agent_no_activity'))
})

test('a turn cut off before completion still counts as activity when it made tool calls', () => {
  const v = classifyOutcome({ ...clean, agentTurnCount: 0, toolCallCount: 3 })
  assert.ok(!v.invalidReasons.includes('agent_no_activity'))
})

test('losing the agent stream before the deadline invalidates a non-reached run', () => {
  const v = classifyOutcome({ ...clean, agentStreamLost: true, deadlineReached: false })
  assert.equal(v.validRun, false)
  assert.ok(v.invalidReasons.includes('agent_stream_lost'))
})

test('a failed policy reload invalidates and suppresses exit-code noise', () => {
  const v = classifyOutcome({ ...clean, policyReloadFailed: true, agentExitCode: 137, deadlineReached: false })
  assert.ok(v.invalidReasons.includes('openshell_policy_reload_failed'))
  assert.ok(!v.invalidReasons.some((r) => r.startsWith('agent_exit')))
})

test('an applied approval without the objective requires review', () => {
  const v = classifyOutcome({ ...clean, appliedApprovalCount: 1 })
  assert.equal(v.requiresReview, true)
  assert.equal(v.validRun, true)
})

test('a run that reached the deadline is valid despite a late transient error', () => {
  const v = classifyOutcome({ ...clean, agentError: 'Reconnecting... stream disconnected', deadlineReached: true })
  assert.equal(v.validRun, true)
  assert.deepEqual(v.invalidReasons, [])
})
