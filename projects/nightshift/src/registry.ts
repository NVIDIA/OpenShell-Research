/**
 * The explicit host registries. Reviewers and runtimes are listed here by
 * name; experiments are folders the CLI is pointed at (see src/experiment.ts).
 */
import { autoApprove } from '../reviewers/auto-approve.js'
import { modelReviewer } from '../reviewers/model-reviewer.js'
import { rejectAll } from '../reviewers/reject-all.js'
import type { ReviewerFactory } from './reviewer.js'

export const reviewers: Record<string, ReviewerFactory> = {
  'auto-approve': autoApprove,
  'reject-all': rejectAll,
  'model-reviewer': modelReviewer,
}

export function selectReviewer(name: string): ReviewerFactory {
  const reviewer = reviewers[name]
  if (!reviewer) throw new Error(`unknown reviewer: ${name} (have: ${Object.keys(reviewers).join(', ')})`)
  return reviewer
}

/** Runtime names live in the driver bundle; re-exported so the CLI can validate and list them. */
export { runtimeNames } from '../driver/runtimes/index.js'

/** The model API family a model-driven runtime speaks. */
export interface RuntimeModelProfile {
  /** Sandbox environment variable that holds the API key. */
  apiKeyEnv: string
  /** Default endpoint for this family; the harness adds egress for its host. */
  defaultBaseUrl: string
  /** Default model identifier when NIGHTSHIFT_MODEL is unset. */
  defaultModel: string
  /**
   * Binaries allowed to reach the model endpoint: the runtime's own executables
   * plus node for the driver. Mirrors OpenShell's provider profile for the same
   * harness (`providers/<harness>.yaml` in the OpenShell repository).
   */
  binaries: string[]
  /** How the runtime presents the key to the endpoint, so the network boundary can substitute it. */
  authStyle: 'bearer' | 'header'
  headerName: string
}

/**
 * Which model API each runtime speaks. The `scripted` runtime has no entry
 * because it uses no model. `responses` and `codex` speak the OpenAI Responses
 * API; `claude-code` speaks the Anthropic API. A new model-driven runtime adds
 * one entry here so the CLI knows which key and endpoint to supply.
 */
export const runtimeModelProfiles: Record<string, RuntimeModelProfile> = {
  responses: {
    apiKeyEnv: 'OPENAI_API_KEY', defaultBaseUrl: 'https://api.openai.com/v1/responses', defaultModel: 'gpt-5',
    binaries: ['/usr/bin/node'], authStyle: 'bearer', headerName: 'authorization',
  },
  codex: {
    apiKeyEnv: 'OPENAI_API_KEY', defaultBaseUrl: 'https://api.openai.com/v1/responses', defaultModel: 'gpt-5',
    binaries: ['/usr/bin/node', '/usr/bin/codex', '/usr/local/bin/codex', '/usr/lib/node_modules/@openai/**'], authStyle: 'bearer', headerName: 'authorization',
  },
  'claude-code': {
    apiKeyEnv: 'ANTHROPIC_API_KEY', defaultBaseUrl: 'https://api.anthropic.com', defaultModel: 'sonnet',
    binaries: ['/usr/bin/node', '/usr/bin/claude', '/usr/local/bin/claude'], authStyle: 'header', headerName: 'x-api-key',
  },
}

/**
 * Sandbox image a runtime needs when the experiment's image is not enough.
 * A runtime whose CLI is not in the base image, or whose version must be pinned
 * for reproducible evidence, defines its image under `images/<runtime>/` and
 * names it here. `--image` overrides this. Build codex's image once with
 * `npm run image:codex`.
 */
export const runtimeDefaultImages: Record<string, string> = {
  codex: 'nightshift/codex:0.147.0',
}
