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

/** The model API family a model-driven runtime speaks. */
export interface RuntimeModelProfile {
  /** Sandbox environment variable that holds the API key. */
  apiKeyEnv: string
  /** Default endpoint for this family; the harness adds egress for its host. */
  defaultBaseUrl: string
  /** Default model identifier when LAB_MODEL is unset. */
  defaultModel: string
}

/**
 * Which model API each runtime speaks. The `scripted` runtime has no entry
 * because it uses no model. `responses` and `codex` speak the OpenAI Responses
 * API; `claude-code` speaks the Anthropic API. A new model-driven runtime adds
 * one entry here so the CLI knows which key and endpoint to supply.
 */
export const runtimeModelProfiles: Record<string, RuntimeModelProfile> = {
  responses: { apiKeyEnv: 'OPENAI_API_KEY', defaultBaseUrl: 'https://api.openai.com/v1/responses', defaultModel: 'gpt-5' },
  codex: { apiKeyEnv: 'OPENAI_API_KEY', defaultBaseUrl: 'https://api.openai.com/v1/responses', defaultModel: 'gpt-5' },
  'claude-code': { apiKeyEnv: 'ANTHROPIC_API_KEY', defaultBaseUrl: 'https://api.anthropic.com', defaultModel: 'sonnet' },
}
