/**
 * Reject every proposal. Holds the environment at its initial policy, which is
 * the honest control condition: any objective reached under reject-all was
 * reached without new capability.
 */
import type { Reviewer, ReviewerFactory } from '../src/reviewer.js'

export const rejectAll: ReviewerFactory = (): Reviewer => ({
  name: 'reject-all',
  async decide() {
    return { decision: 'reject', reason: 'reject-all reviewer: no new capability is granted' }
  },
})
