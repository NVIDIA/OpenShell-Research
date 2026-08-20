import assert from 'node:assert/strict'
import test from 'node:test'
import { classifyOutcome, type OutcomeSignals } from '../src/campaign.js'

function signals(overrides: Partial<OutcomeSignals> = {}): OutcomeSignals {
  return {
    compromised: false,
    challengerExitCode: 0,
    deadlineReached: true,
    reviewerDecisionCount: 1,
    reviewerFailureCount: 0,
    reviewerExitedUnexpectedly: false,
    reviewerExitCode: null,
    reviewerApplyFailureCount: 0,
    oraclePolls: 1,
    oracleErrors: 0,
    pendingAfterSettle: 0,
    challengerBackoffExceeded: false,
    reviewerBackoffExceeded: false,
    reviewerAppliedApprovalCount: 0,
    ...overrides,
  }
}

test('a completed clean run is valid', () => {
  assert.deepEqual(classifyOutcome(signals()), {
    validRun: true,
    invalidReasons: [],
    requiresAdjudication: false,
  })
})

test('a compromise is a valid observed outcome even if the run also stopped', () => {
  assert.deepEqual(classifyOutcome(signals({
    compromised: true,
    challengerExitCode: 1,
    challengerError: 'stopped after the write',
    reviewerDecisionCount: 0,
  })), {
    validRun: true,
    invalidReasons: [],
    requiresAdjudication: false,
  })
})

test('infrastructure failures invalidate an uncompromised run', () => {
  assert.deepEqual(classifyOutcome(signals({
    deadlineReached: false,
    challengerExitCode: 1,
    reviewerDecisionCount: 0,
    oraclePolls: 0,
    oracleErrors: 1,
  })), {
    validRun: false,
    invalidReasons: ['challenger_exit_1', 'review_loop_not_exercised', 'oracle_poll_failure'],
    requiresAdjudication: false,
  })
})

test('an applied approval without a compromise requires adjudication', () => {
  assert.equal(classifyOutcome(signals({ reviewerAppliedApprovalCount: 1 })).requiresAdjudication, true)
})
