/**
 * The Nightshift CLI. Five verbs, no framework:
 *
 *   nightshift init [experiment] [directory]   copy a bundled experiment folder to edit and run
 *   nightshift run <experiment> [--profile P] [--minutes N] [--runtime R] [--model ID] [--reviewer A]
 *                               [--image REF] [--turn-timeout S] [--keep] [--continue]
 *   nightshift report [run-id | run-dir]       defaults to the latest run
 *   nightshift doctor
 *
 * `<experiment>` is a folder path or the name of a folder bundled under
 * `experiments/`. Without `--minutes` (and without `durationMinutes` in
 * experiment.json) a run continues until Ctrl-C, which ends it cleanly and
 * prints the report. Runs are written under `./runs` in the current directory.
 */
import { randomBytes } from 'node:crypto'
import { access, cp, readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { defaultDriverTuning } from '../driver/config.js'
import { bundleDriver } from './driver-bundle.js'
import { json, readJsonl, status } from './evidence.js'
import { loadExperiment, selectProfile } from './experiment.js'
import { runHorizon } from './horizon.js'
import { connectGateway, message, minimumOpenShellVersion } from './openshell.js'
import { reviewers, runtimeDefaultImages, runtimeModelProfiles, runtimeNames, selectReviewer } from './registry.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const experimentsRoot = path.join(root, 'experiments')
/** Share of expected score polls that must succeed for a run that never reached `done` to count as valid. */
const SCORE_MIN_SUCCESS_PERCENT = 80

const runsDir = (): string => process.env.NIGHTSHIFT_RUNS_DIR ?? path.join(process.cwd(), 'runs')

/** `.env` in the current directory wins over the package's own; values already in the environment win over both. */
async function loadEnv(): Promise<void> {
  for (const dir of [process.cwd(), root]) {
    const text = await readFile(path.join(dir, '.env'), 'utf8').catch(() => '')
    for (const rawLine of text.split(/\r?\n/)) {
      const line = rawLine.trim()
      if (!line || line.startsWith('#')) continue
      const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
      if (!match) continue
      const value = match[2]?.replace(/^["']|["']$/g, '') ?? ''
      // An empty assignment means "unset": the runtime or experiment default applies.
      if (value && process.env[match[1] as string] === undefined) process.env[match[1] as string] = value
    }
  }
}

function newRunId(): string {
  return `${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`
}

async function bundledExperiments(): Promise<string[]> {
  const entries = await readdir(experimentsRoot, { withFileTypes: true })
  return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort()
}

async function init(argv: string[]): Promise<number> {
  const names = await bundledExperiments()
  const name = argv[0]
  if (!name) {
    process.stdout.write(`usage: nightshift init <experiment> [directory]\n\nbundled experiments:\n${names.map((n) => `  ${n}`).join('\n')}\n`)
    return 2
  }
  if (!names.includes(name)) throw new Error(`no bundled experiment named ${name} (have: ${names.join(', ')})`)
  const destination = path.resolve(argv[1] ?? name)
  if (await access(destination).then(() => true).catch(() => false)) throw new Error(`${destination} already exists; choose another directory`)
  await cp(path.join(experimentsRoot, name), destination, { recursive: true })
  process.stdout.write(`copied ${name} to ${destination}\n\nedit program.md, reviewer.md, policy.json, and experiment.json there, then:\n  nightshift run ${path.relative(process.cwd(), destination) || '.'}\n`)
  return 0
}

async function run(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv, allowPositionals: true,
    options: {
      profile: { type: 'string' }, minutes: { type: 'string' }, runtime: { type: 'string' }, model: { type: 'string' }, reviewer: { type: 'string' }, image: { type: 'string' },
      'turn-timeout': { type: 'string' },
      keep: { type: 'boolean' }, 'continue': { type: 'boolean' },
    },
  })
  const ref = positionals[0]
  if (!ref) throw new Error('usage: nightshift run <experiment folder or name> [--profile P] [--minutes N] [--runtime R] [--reviewer A]')
  const experiment = await loadExperiment(ref, experimentsRoot)
  const { config } = experiment
  const { name: profileName, profile } = selectProfile(config, values.profile)
  const runtime = values.runtime ?? config.defaultRuntime
  if (!runtimeNames.includes(runtime)) throw new Error(`unknown runtime: ${runtime} (have: ${runtimeNames.join(', ')})`)
  const reviewerName = values.reviewer ?? config.defaultReviewer
  // No horizon means run until stopped.
  const minutes = values.minutes ? Number(values.minutes) : config.durationMinutes
  if (minutes !== undefined && (!Number.isFinite(minutes) || minutes <= 0)) throw new Error('--minutes must be a positive number')
  // Image precedence: command line, then the profile, then the runtime's pinned image, then the experiment.
  const image = values.image ?? profile.image ?? runtimeDefaultImages[runtime] ?? config.image

  // A runtime is model-driven exactly when it declares a model profile.
  const modelProfile = runtimeModelProfiles[runtime]
  const agentApiKey = modelProfile ? (process.env.NIGHTSHIFT_MODEL_API_KEY || process.env[modelProfile.apiKeyEnv]) : undefined
  if (modelProfile && !agentApiKey) throw new Error(`runtime "${runtime}" needs ${modelProfile.apiKeyEnv} (or NIGHTSHIFT_MODEL_API_KEY) in .env`)
  const model = modelProfile ? {
    baseUrl: process.env.NIGHTSHIFT_MODEL_BASE_URL || modelProfile.defaultBaseUrl,
    model: values.model || process.env.NIGHTSHIFT_MODEL || modelProfile.defaultModel,
    reasoning: process.env.NIGHTSHIFT_MODEL_REASONING || 'medium',
    apiKeyEnv: modelProfile.apiKeyEnv,
    contextWindow: Number(process.env.NIGHTSHIFT_MODEL_CONTEXT_WINDOW || defaultDriverTuning.model.contextWindow),
    effectiveContextPercent: defaultDriverTuning.model.effectiveContextPercent,
  } : undefined
  if (model && (!Number.isFinite(model.contextWindow) || model.contextWindow <= 0)) throw new Error('NIGHTSHIFT_MODEL_CONTEXT_WINDOW must be a positive number')
  const turnTimeoutRaw = values['turn-timeout'] ?? process.env.NIGHTSHIFT_TURN_TIMEOUT_SECONDS
  const turnTimeoutSeconds = turnTimeoutRaw ? Number(turnTimeoutRaw) : undefined
  if (turnTimeoutSeconds !== undefined && (!Number.isFinite(turnTimeoutSeconds) || turnTimeoutSeconds <= 0)) throw new Error('--turn-timeout must be a positive number of seconds')
  const modelCredential = process.env.NIGHTSHIFT_MODEL_CREDENTIAL || 'provider'
  if (modelCredential !== 'provider' && modelCredential !== 'env') throw new Error('NIGHTSHIFT_MODEL_CREDENTIAL must be provider or env')

  const gateway = await connectGateway()
  status('gateway.connected', { version: gateway.version, sdkVersion: gateway.sdkVersion })
  const driverBundle = await bundleDriver()

  const result = await runHorizon(gateway, {
    experiment, profileName, profile, reviewer: selectReviewer(reviewerName), reviewerName, runtime, minutes, image,
    runId: process.env.NIGHTSHIFT_RUN_ID ?? newRunId(), workspace: gateway.workspace,
    runsDir: runsDir(), driverBundle,
    keepSandbox: values.keep === true, continueAfterDone: values['continue'] === true || config.continueAfterDone,
    scoreMinSuccessPercent: SCORE_MIN_SUCCESS_PERCENT, model, agentApiKey, modelProfile, modelCredential,
    turnTimeoutSeconds,
  })
  process.stdout.write('\n')
  await printReport(result.runDir)
  return result.done || result.validRun ? 0 : 1
}

async function doctor(): Promise<number> {
  const lines: string[] = []
  let ok = true
  try {
    const gateway = await connectGateway()
    lines.push(`gateway:      ${gateway.endpoint}  version ${gateway.version}  (healthy)`)
    lines.push(`sdk:          @nvidia/openshell-sdk ${gateway.sdkVersion}  (matches gateway, min ${minimumOpenShellVersion})`)
  } catch (error) {
    ok = false
    lines.push(`gateway:      ERROR — ${message(error)}`)
  }
  lines.push(`experiments:  ${(await bundledExperiments()).join(', ')}  (bundled; or any folder with experiment.json)`)
  lines.push(`reviewers:    ${Object.keys(reviewers).join(', ')}`)
  lines.push(`runtimes:     ${runtimeNames.join(', ')}`)
  lines.push(`runs:         ${runsDir()}`)
  try { await bundleDriver(); lines.push('driver:       bundles cleanly') } catch (error) { ok = false; lines.push(`driver:       ERROR — ${message(error)}`) }
  process.stdout.write(`${lines.join('\n')}\n${ok ? 'doctor: ready' : 'doctor: not ready'}\n`)
  return ok ? 0 : 1
}

async function report(argv: string[]): Promise<number> {
  const target = argv[0] ?? 'latest'
  const runDir = target.includes(path.sep) ? path.resolve(target) : path.join(runsDir(), target)
  await printReport(runDir)
  return 0
}

/** The morning read: the ledger, the outcome, and where the evidence is. */
async function printReport(runDir: string): Promise<void> {
  const outcome = JSON.parse(await readFile(path.join(runDir, 'outcome.json'), 'utf8')) as Record<string, unknown>
  const events = await readJsonl(path.join(runDir, 'events.jsonl'))
  const decisions = await readJsonl(path.join(runDir, 'decisions.jsonl'))
  const ledger = await readFile(path.join(runDir, 'results.tsv'), 'utf8').catch(() => '')
  const score = (outcome.score ?? {}) as Record<string, unknown>
  const headline = outcome.done ? 'DONE' : `best=${score.best ?? 'none'} last=${score.last ?? 'none'} trials=${score.trials ?? 0}`
  const reasons = Array.isArray(outcome.invalidReasons) && outcome.invalidReasons.length ? ` (${(outcome.invalidReasons as string[]).join(', ')})` : ''
  const ended: Record<string, string> = { done: 'scorer reported done', deadline: 'deadline reached', stopped: 'stopped with Ctrl-C', agent_exit: 'agent exited', agent_stream_lost: 'agent stream lost', policy_reload_failed: 'policy reload failed' }
  process.stdout.write(`run ${outcome.runId} · ${outcome.experiment}${outcome.profile ? ` (${outcome.profile})` : ''} · ${ended[String(outcome.stopReason)] ?? String(outcome.stopReason)}\n`)
  process.stdout.write(`${headline} — validRun=${outcome.validRun}${reasons}\n`)
  process.stdout.write(`events: ${events.length}  tool calls: ${events.filter((e) => e.type === 'tool.call').length}  proposals decided: ${decisions.length}  approvals applied: ${outcome.appliedApprovalCount ?? 0}\n`)
  if (ledger.trim()) process.stdout.write(`\n${ledger.trimEnd()}\n`)
  process.stdout.write(`\nevidence: ${runDir}\n`)
  if (process.env.NIGHTSHIFT_REPORT_JSON === '1') process.stdout.write(`\n${json(outcome)}\n`)
}

async function main(): Promise<void> {
  await loadEnv()
  const [command, ...rest] = process.argv.slice(2)
  const handlers: Record<string, (argv: string[]) => Promise<number>> = { init, run, report, doctor: () => doctor() }
  const handler = handlers[command ?? '']
  if (!handler) { process.stderr.write('usage: nightshift <init|run|report|doctor> ...\n'); process.exitCode = 2; return }
  process.exitCode = await handler(rest)
}

main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`); process.exitCode = 1 })
