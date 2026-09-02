/**
 * The explicit host registries. Adding a scenario or adjudicator is one import
 * and one map entry here — no filesystem discovery, so the whole set of
 * experiments is legible in one place.
 */
import { autoApprove } from '../adjudicators/auto-approve.js'
import { modelReviewer } from '../adjudicators/model-reviewer.js'
import { rejectAll } from '../adjudicators/reject-all.js'
import { githubPolicyReview } from '../scenarios/github-policy-review/scenario.js'
import { helloCanary } from '../scenarios/hello-canary/scenario.js'
import type { AdjudicatorFactory } from './adjudicator.js'
import type { Scenario } from './scenario.js'

export const scenarios: Record<string, Scenario> = {
  'hello-canary': helloCanary,
  'github-policy-review': githubPolicyReview,
}

export const adjudicators: Record<string, AdjudicatorFactory> = {
  'auto-approve': autoApprove,
  'reject-all': rejectAll,
  'model-reviewer': modelReviewer,
}

export function selectScenario(name: string): Scenario {
  const scenario = scenarios[name]
  if (!scenario) throw new Error(`unknown scenario: ${name} (have: ${Object.keys(scenarios).join(', ')})`)
  return scenario
}

export function selectAdjudicator(name: string): AdjudicatorFactory {
  const adjudicator = adjudicators[name]
  if (!adjudicator) throw new Error(`unknown adjudicator: ${name} (have: ${Object.keys(adjudicators).join(', ')})`)
  return adjudicator
}

/** Runtime names live in the driver bundle; re-exported so the CLI can validate and list them. */
export { runtimeNames } from '../driver/runtimes/index.js'
