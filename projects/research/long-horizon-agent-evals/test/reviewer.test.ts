import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isProposalAlreadyRejectedError,
  isProposalReviewStaleError,
  proposalPreflightError,
  reviewerHistoryPacket,
  reviewerModelPacket,
} from '../src/reviewer.js'

test('recognizes a rejection that OpenShell already satisfied', () => {
  assert.equal(isProposalAlreadyRejectedError(new Error(
    "[failed_precondition] chunk status is 'rejected', expected 'pending' or 'approved'",
  )), true)
  assert.equal(isProposalAlreadyRejectedError(new Error(
    "[failed_precondition] chunk status is 'approved', expected 'pending' or 'approved'",
  )), false)
})

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

test('recognizes only non-empty OpenShell candidate preflight errors', () => {
  assert.equal(proposalPreflightError({ applicationError: ' candidate invalid ' }), 'candidate invalid')
  assert.equal(proposalPreflightError({ applicationError: '' }), undefined)
  assert.equal(proposalPreflightError({}), undefined)
})

test('reviewer packet includes one authoritative candidate policy without duplicate snapshots', () => {
  const packet = reviewerModelPacket('sandbox-1', 'owner/repo', {
    id: 'chunk-1',
    ruleName: 'candidate',
    rationale: 'needed for work',
    proposedRule: { name: 'candidate' },
    currentEffectivePolicy: { version: 1, marker: 'current' },
    candidateEffectivePolicy: { version: 1, marker: 'candidate' },
    reviewToken: 'not-for-the-model',
  })
  const proposal = packet.proposal as Record<string, unknown>
  assert.deepEqual(proposal.candidateEffectivePolicy, { version: 1, marker: 'candidate' })
  assert.equal('currentEffectivePolicy' in proposal, false)
  assert.equal('currentPolicy' in packet, false)
  assert.equal('reviewToken' in proposal, false)

  const historyProposal = reviewerHistoryPacket(packet).proposal as Record<string, unknown>
  assert.equal('candidateEffectivePolicy' in historyProposal, false)
  assert.deepEqual(historyProposal.proposedRule, { name: 'candidate' })
})
