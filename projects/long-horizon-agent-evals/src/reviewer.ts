/**
 * The reviewer contract.
 *
 * When the agent asks OpenShell for more capability, the harness fetches the
 * pending proposal and asks the reviewer to approve or reject it. The
 * harness (via src/openshell.ts) applies the decision and fails closed; the
 * reviewer only decides. Two ship today: `auto-approve` and `model-reviewer`.
 */
import type { Decision, Proposal } from './openshell.js'

export interface ReviewContext {
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

export interface Reviewer {
  name: string
  decide(proposal: Proposal, context: ReviewContext): Promise<Decision>
}

/** A reviewer that needs per-run setup (history, a model client) is built by a factory. */
export type ReviewerFactory = (options: ReviewerOptions) => Reviewer

export interface ReviewerOptions {
  runDir: string
  instanceFacts: Record<string, unknown>
}
