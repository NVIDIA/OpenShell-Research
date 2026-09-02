/**
 * The adjudicator contract.
 *
 * When the agent asks OpenShell for more capability, the harness fetches the
 * pending proposal and asks the adjudicator to approve or reject it. The
 * harness (via src/openshell.ts) applies the decision and fails closed; the
 * adjudicator only decides. Two ship today: `auto-approve` and `model-reviewer`.
 */
import type { Decision, Proposal } from './openshell.js'

export interface AdjudicationContext {
  runDir: string
  /** Scenario instance facts, e.g. the protected repository name. */
  instanceFacts: Record<string, unknown>
  /** 1-based index of this decision within the run. */
  decisionNumber: number
  /** Current effective sandbox policy, for context. */
  effectivePolicy: unknown
  /** Milliseconds remaining before the run deadline. */
  remainingMs: number
}

export interface Adjudicator {
  name: string
  decide(proposal: Proposal, context: AdjudicationContext): Promise<Decision>
}

/** An adjudicator that needs per-run setup (history, a model client) is built by a factory. */
export type AdjudicatorFactory = (options: AdjudicatorOptions) => Adjudicator

export interface AdjudicatorOptions {
  runDir: string
  instanceFacts: Record<string, unknown>
}
