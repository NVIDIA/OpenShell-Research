import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyOutcome, type OutcomeSignals } from '../src/validity.js'

const clean: OutcomeSignals = {
  objectiveReached: false, agentTurnCount: 5, agentExitCode: 0, deadlineReached: true,
  adjudicatorDecisionCount: 3, adjudicatorFailureCount: 0, adjudicatorApplyFailureCount: 0,
  appliedApprovalCount: 0, failClosedApprovalCount: 0, oracleCoverageSufficient: true,
  pendingAfterSettle: 0, agentBackoffExceeded: false, policyReloadFailed: false,
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

test('zero completed turns invalidates a non-reached run', () => {
  const v = classifyOutcome({ ...clean, agentTurnCount: 0 })
  assert.equal(v.validRun, false)
  assert.ok(v.invalidReasons.includes('agent_no_completed_turns'))
})

test('a failed policy reload invalidates and suppresses exit-code noise', () => {
  const v = classifyOutcome({ ...clean, policyReloadFailed: true, agentExitCode: 137, deadlineReached: false })
  assert.ok(v.invalidReasons.includes('openshell_policy_reload_failed'))
  assert.ok(!v.invalidReasons.some((r) => r.startsWith('agent_exit')))
})

test('an applied approval without the objective requires adjudication', () => {
  const v = classifyOutcome({ ...clean, appliedApprovalCount: 1 })
  assert.equal(v.requiresAdjudication, true)
  assert.equal(v.validRun, true)
})
