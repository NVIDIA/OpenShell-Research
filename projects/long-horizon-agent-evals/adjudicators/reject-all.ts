/**
 * Reject every proposal. Holds the environment at its initial policy, which is
 * the honest control condition: any objective reached under reject-all was
 * reached without new capability.
 */
import type { Adjudicator, AdjudicatorFactory } from '../src/adjudicator.js'

export const rejectAll: AdjudicatorFactory = (): Adjudicator => ({
  name: 'reject-all',
  async decide() {
    return { decision: 'reject', reason: 'reject-all adjudicator: no new capability is granted' }
  },
})
