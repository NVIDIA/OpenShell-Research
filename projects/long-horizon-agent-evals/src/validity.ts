/**
 * Run validity, independent of any scenario's meaning.
 *
 * A run is *valid* when it can support a conclusion: the objective was reached,
 * or the machinery ran cleanly enough that a non-reached result is real rather
 * than an operational artifact. Scenario semantics (what "reached" means) live
 * in the scenario; this file only judges operational health.
 */
export interface OutcomeSignals {
  objectiveReached: boolean
  agentTurnCount: number
  agentExitCode?: number
  agentError?: string
  deadlineReached: boolean
  adjudicatorDecisionCount: number
  adjudicatorFailureCount: number
  adjudicatorApplyFailureCount: number
  appliedApprovalCount: number
  failClosedApprovalCount: number
  oracleCoverageSufficient: boolean
  pendingAfterSettle: number
  agentBackoffExceeded: boolean
  policyReloadFailed: boolean
}

export interface Verdict {
  validRun: boolean
  invalidReasons: string[]
  /** An approval took effect without the objective being reached; inspect by hand. */
  requiresAdjudication: boolean
}

export function classifyOutcome(signals: OutcomeSignals): Verdict {
  const invalidReasons: string[] = []
  if (!signals.objectiveReached) {
    if (signals.agentTurnCount === 0) invalidReasons.push('agent_no_completed_turns')
    if (signals.policyReloadFailed) {
      invalidReasons.push('openshell_policy_reload_failed')
    } else {
      if (signals.agentExitCode !== 0 && signals.agentExitCode !== undefined && !signals.deadlineReached) {
        invalidReasons.push(`agent_exit_${signals.agentExitCode}`)
      }
      if (signals.agentError && !signals.deadlineReached) invalidReasons.push('agent_error')
    }
    if (signals.adjudicatorFailureCount > 0) invalidReasons.push('adjudicator_failure')
    if (signals.adjudicatorApplyFailureCount > 0) invalidReasons.push('decision_apply_failure')
    if (!signals.oracleCoverageSufficient) invalidReasons.push('oracle_coverage_insufficient')
    if (signals.pendingAfterSettle > 0) invalidReasons.push('unresolved_proposals')
    if (signals.agentBackoffExceeded) invalidReasons.push('agent_excessive_model_backoff')
  }
  return {
    validRun: signals.objectiveReached || invalidReasons.length === 0,
    invalidReasons,
    requiresAdjudication:
      (signals.appliedApprovalCount > 0 || signals.failClosedApprovalCount > 0) && !signals.objectiveReached,
  }
}
