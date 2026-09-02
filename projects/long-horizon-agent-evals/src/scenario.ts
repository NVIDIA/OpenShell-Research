/**
 * The scenario contract.
 *
 * A scenario is one long-horizon task: what the agent is asked to do, the
 * policy that initially forbids it, the credentials it may use, and how the
 * host observes whether the objective was reached. Everything runtime-agnostic
 * and task-agnostic lives in the harness (src/horizon.ts); a scenario only
 * supplies the parts that make it *this* experiment.
 *
 * Keep an implementation small: a folder with `scenario.json` (static config),
 * `task.md` (the prompt), and a `scenario.ts` under ~100 lines. Two scenarios
 * ship today (hello-canary, github-policy-review); do not grow this interface
 * for a hypothetical third.
 */
import type { DriverConfig } from '../driver/config.js'
import type { Gateway, Policy, ProviderSpec } from './openshell.js'

/** Static, human-editable scenario settings loaded from `scenario.json`. */
export interface ScenarioConfig {
  name: string
  description: string
  /** Sandbox image reference. */
  image: string
  /** Runtime used unless overridden on the command line; a name from driver/runtimes/index.ts. */
  defaultRuntime: string
  /** Reviewer used unless overridden on the command line. */
  defaultReviewer: string
  durationMinutes: number
  oraclePollSeconds: number
  /** Keep the agent running after the objective is first observed. */
  continueAfterObjective: boolean
}

/** One prepared attempt: the concrete branch, path, marker, or none. */
export interface Instance {
  /** Identity of this attempt, recorded verbatim in run.json. */
  facts: Record<string, unknown>
  /** Literal strings to redact from every saved artifact (tokens, markers). */
  secrets: string[]
}

export interface ScenarioContext {
  gateway: Gateway
  runDir: string
  runId: string
}

/** What the oracle saw on one poll. `objectiveReached` is the only load-bearing field. */
export interface OracleResult {
  objectiveReached: boolean
  detail?: Record<string, unknown>
}

export interface Scenario {
  config: ScenarioConfig
  /** Fully rendered agent prompt (task.md with instance facts substituted). */
  prompt(instance: Instance): Promise<string>
  /** Prepare one attempt; may create external resources or none. */
  prepare(context: ScenarioContext): Promise<Instance>
  /** Create-time policy that forbids the objective. */
  policy(instance: Instance): Policy
  /** Credential providers to attach; empty for a zero-credential scenario. */
  providers(instance: Instance): ProviderSpec[]
  /** Scenario-specific driver settings merged over the tuning defaults. */
  driverConfig(instance: Instance): Partial<DriverConfig>
  /** Start host-side infrastructure (e.g. a canary listener); return a teardown. */
  setup?(context: ScenarioContext, instance: Instance): Promise<() => Promise<void>>
  /** Poll the oracle. Called on an interval during the run and once at the end. */
  observe(context: ScenarioContext, instance: Instance): Promise<OracleResult>
  /** Extra facts for outcome.json; the returned object is merged verbatim. */
  finalize?(context: ScenarioContext, instance: Instance): Promise<Record<string, unknown>>
}
