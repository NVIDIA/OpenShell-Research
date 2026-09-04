/**
 * The experiment contract: one folder the researcher edits.
 *
 * An experiment is a long task for an agent, how to score progress on it, and
 * the OpenShell envelope it runs in. Everything task-agnostic (sandbox
 * lifecycle, proposal review, evidence, validity) lives in src/horizon.ts; the
 * folder supplies only what makes it *this* experiment:
 *
 *   experiment.json   name, image, defaults, duration, scorer, profiles, providers
 *   program.md        the task, read by the agent ({{PLACEHOLDER}} templating)
 *   reviewer.md       what capability expansion is allowed, read by a model reviewer
 *   policy.json       the opening OpenShell policy (templated JSON)
 *   workdir/          files uploaded to /sandbox/work; the agent's git repository
 *   score.ts          optional host-side hooks: prepare, setup, score, finalize
 *
 * The score is produced on the trusted side, never taken from the agent's
 * claims. A `command` scorer runs a read-only command inside the sandbox and
 * parses one JSON line; a `module` scorer is a host function in score.ts.
 */
import { access, readFile, stat } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import type { DriverConfig } from '../driver/config.js'
import type { Gateway, ProviderSpec } from './openshell.js'

/** Static, human-editable settings from `experiment.json`. */
export interface ExperimentConfig {
  name: string
  description: string
  /** Sandbox image; a profile or the runtime's pinned image may override it. */
  image: string
  /** Runtime used unless overridden on the command line; a name from driver/runtimes/index.ts. */
  defaultRuntime: string
  /** Reviewer used unless overridden on the command line; a name from src/registry.ts. */
  defaultReviewer: string
  /** Default horizon; absent means run until stopped. */
  durationMinutes?: number
  /** Interval between score checks. */
  scorePollSeconds: number
  /** Keep the agent running after the scorer first reports `done`. */
  continueAfterDone: boolean
  score: ScoreConfig
  /** Named variants (laptop, station): image, environment, per-turn timeout. */
  profiles?: Record<string, Profile>
  defaultProfile?: string
  /** Credentials delivered as OpenShell providers; values are `$ENV_VAR` references resolved on the host. */
  providers?: ProviderConfig[]
  /** Driver tuning merged over the defaults in driver/config.ts. */
  driver?: Partial<Pick<DriverConfig, 'turnTimeoutSeconds' | 'resumeNudge' | 'backoff' | 'rotation' | 'handoff' | 'lull'>>
}

export type ScoreConfig =
  | { kind: 'command'; command: string[]; direction: 'min' | 'max' }
  | { kind: 'module'; direction: 'min' | 'max' }

export interface Profile {
  image?: string
  /** Environment for the agent process and the score command; also available as `{{NAME}}` in templates. */
  env?: Record<string, string>
  turnTimeoutSeconds?: number
  /** Request a GPU for the sandbox. */
  gpu?: boolean
}

export interface ProviderConfig {
  /** OpenShell provider profile id, for example `github`. */
  type: string
  credentials: Record<string, string>
}

/** One prepared attempt: random identifiers, external resources, or nothing. */
export interface Instance {
  /** Identity of this attempt, recorded in instance.json and available as template values. */
  facts: Record<string, unknown>
  /** Literal strings to redact from every saved artifact. */
  secrets: string[]
}

export interface ExperimentContext {
  gateway: Gateway
  runDir: string
  runId: string
}

/** What the scorer saw on one check. `value` and `done` are the load-bearing fields. */
export interface Score {
  /** The score, or null when there is nothing to score yet (no trial has finished). */
  value: number | null
  done: boolean
  /** Identifies the artifact scored; a new ledger row is written when it changes. Defaults to the value. */
  trial?: string
  notes?: string
  detail?: Record<string, unknown>
}

/** Optional host-side hooks exported from `score.ts`. */
export interface ExperimentHooks {
  /** Prepare one attempt before any sandbox exists; create external resources or none. */
  prepare?(context: ExperimentContext): Promise<Instance>
  /** Start host-side infrastructure (a listener); return a teardown. */
  setup?(context: ExperimentContext, instance: Instance): Promise<() => Promise<void>>
  /** Required for a `module` scorer. Called on every poll and once after the agent stops. */
  score?(context: ExperimentContext, instance: Instance): Promise<Score>
  /** Extra facts for outcome.json, merged verbatim. */
  finalize?(context: ExperimentContext, instance: Instance): Promise<Record<string, unknown>>
  /** Runtime-specific driver settings, for example the scripted runtime's target. */
  driverConfig?(instance: Instance): Partial<DriverConfig>
}

export interface Experiment {
  dir: string
  config: ExperimentConfig
  hooks: ExperimentHooks
  /** Absolute path of `workdir/` when the folder has one. */
  workdir?: string
  /** Template of the agent prompt. */
  program: string
  /** Template of the model reviewer's instructions, when the folder has reviewer.md. */
  reviewerInstructions?: string
  /** Template of the opening policy as JSON text. */
  policy: string
}

const REQUIRED_FIELDS = ['name', 'description', 'image', 'defaultRuntime', 'defaultReviewer', 'scorePollSeconds', 'continueAfterDone', 'score'] as const
const PLACEHOLDER = /\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g

/**
 * Load an experiment folder. `ref` is a folder path, or a name resolved under
 * `experimentsRoot`.
 */
export async function loadExperiment(ref: string, experimentsRoot: string): Promise<Experiment> {
  const dir = await resolveExperimentDir(ref, experimentsRoot)
  const config = JSON.parse(await readFile(path.join(dir, 'experiment.json'), 'utf8')) as ExperimentConfig
  for (const field of REQUIRED_FIELDS) if (config[field] === undefined) throw new Error(`${dir}/experiment.json is missing "${field}"`)
  if (config.score.kind !== 'command' && config.score.kind !== 'module') throw new Error(`${dir}/experiment.json: score.kind must be "command" or "module"`)
  if (config.score.direction !== 'min' && config.score.direction !== 'max') throw new Error(`${dir}/experiment.json: score.direction must be "min" or "max"`)
  if (config.defaultProfile && !config.profiles?.[config.defaultProfile]) throw new Error(`${dir}/experiment.json: defaultProfile "${config.defaultProfile}" is not in profiles`)

  const program = await readFile(path.join(dir, 'program.md'), 'utf8')
  const policy = await readFile(path.join(dir, 'policy.json'), 'utf8')
  const reviewerInstructions = await readFile(path.join(dir, 'reviewer.md'), 'utf8').catch(() => undefined)
  const workdir = path.join(dir, 'workdir')
  const hasWorkdir = await stat(workdir).then((s) => s.isDirectory()).catch(() => false)

  const scoreModule = path.join(dir, 'score.ts')
  const hasModule = await access(scoreModule).then(() => true).catch(() => false)
  const hooks: ExperimentHooks = hasModule ? ((await import(pathToFileURL(scoreModule).href)) as { hooks?: ExperimentHooks }).hooks ?? {} : {}
  if (config.score.kind === 'module' && !hooks.score) throw new Error(`${dir}: score.kind is "module" but score.ts does not export hooks.score`)

  return { dir, config, hooks, workdir: hasWorkdir ? workdir : undefined, program, reviewerInstructions, policy }
}

/** Fill `{{NAME}}` placeholders. Every placeholder must resolve, so a typo fails before a sandbox exists. */
export function render(template: string, values: Record<string, unknown>, source: string): string {
  return template.replace(PLACEHOLDER, (_match, name: string) => {
    const value = values[name]
    if (value === undefined || value === null) throw new Error(`${source}: no value for {{${name}}}`)
    return String(value)
  })
}

/** The profile selected for a run, or an empty one. */
export function selectProfile(config: ExperimentConfig, name: string | undefined): { name: string | undefined; profile: Profile } {
  const selected = name ?? config.defaultProfile
  if (!selected) return { name: undefined, profile: {} }
  const profile = config.profiles?.[selected]
  if (!profile) throw new Error(`unknown profile: ${selected} (have: ${Object.keys(config.profiles ?? {}).join(', ') || 'none'})`)
  return { name: selected, profile }
}

/** Resolve `$ENV_VAR` credential references into provider specs. Fails fast with the variable name. */
export function resolveProviders(config: ExperimentConfig, prefix: string, env: NodeJS.ProcessEnv): { specs: ProviderSpec[]; secrets: string[] } {
  const specs: ProviderSpec[] = []
  const secrets: string[] = []
  for (const [index, provider] of (config.providers ?? []).entries()) {
    const credentials: Record<string, string> = {}
    for (const [key, reference] of Object.entries(provider.credentials)) {
      const variable = reference.startsWith('$') ? reference.slice(1) : undefined
      const value = variable ? env[variable] : reference
      if (!value) throw new Error(`experiment "${config.name}" needs ${variable ?? key} in .env for its ${provider.type} provider`)
      credentials[key] = value
      secrets.push(value)
    }
    specs.push({ name: `${prefix}-${provider.type}-${index}`.slice(0, 63), type: provider.type, credentials })
  }
  return { specs, secrets }
}

async function resolveExperimentDir(ref: string, experimentsRoot: string): Promise<string> {
  const candidates = [path.resolve(ref), path.join(experimentsRoot, ref)]
  for (const candidate of candidates) {
    if (await access(path.join(candidate, 'experiment.json')).then(() => true).catch(() => false)) return candidate
  }
  throw new Error(`no experiment at ${ref} (looked for experiment.json in ${candidates.join(' and ')})`)
}
