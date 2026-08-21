import assert from 'node:assert/strict'
import test from 'node:test'
import { isProposalReviewStaleError } from '../src/reviewer.js'

test('recognizes OpenShell responses that require a fresh review', () => {
  for (const message of [
    '[failed_precondition] proposal inputs changed; evaluation refreshed, refetch and review again',
    '[failed_precondition] review token does not match the fetched proposal; refetch and review again',
    '[failed_precondition] proposal inputs changed before persistence; refetch and review again',
  ]) {
    assert.equal(isProposalReviewStaleError(new Error(message)), true)
  }
})

test('does not retry unrelated approval failures', () => {
  assert.equal(isProposalReviewStaleError(
    new Error('[failed_precondition] proposal is not applicable: endpoint ambiguity'),
  ), false)
})
