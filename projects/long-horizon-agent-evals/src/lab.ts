/**
 * The lab CLI. Three verbs, no framework:
 *
 *   lab run <scenario> [--minutes N] [--runtime R] [--adjudicator A] [--image REF] [--keep] [--continue]
 *   lab doctor
 *   lab report <run-id | run-dir>
 *
 * `scale` is intentionally not here yet; add it when a second scenario needs
 * repeated parallel attempts.
 */
import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { defaultDriverTuning } from '../driver/config.js'
import { bundleDriver } from './driver-bundle.js'
import { json, readJsonl, status } from './evidence.js'
import { runHorizon } from './horizon.js'
import { connectGateway, message, minimumOpenShellVersion } from './openshell.js'
import { adjudicators, runtimeModelProfiles, runtimeNames, scenarios, selectAdjudicator, selectScenario } from './registry.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
/** Share of expected oracle polls that must succeed for a non-reached run to count as valid. */
const ORACLE_MIN_SUCCESS_PERCENT = 80

async function loadEnv(): Promise<void> {
  const text = await readFile(path.join(root, '.env'), 'utf8').catch(() => '')
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
    if (!match) continue
    const value = match[2]?.replace(/^["']|["']$/g, '') ?? ''
    if (process.env[match[1] as string] === undefined) process.env[match[1] as string] = value
  }
}

function newRunId(): string {
  return `${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`
}

async function run(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({
    args: argv, allowPositionals: true,
    options: {
      minutes: { type: 'string' }, runtime: { type: 'string' }, adjudicator: { type: 'string' }, image: { type: 'string' },
      keep: { type: 'boolean' }, 'continue': { type: 'boolean' },
    },
  })
  const scenarioName = positionals[0]
  if (!scenarioName) throw new Error(`usage: lab run <scenario> (have: ${Object.keys(scenarios).join(', ')})`)
  const scenario = selectScenario(scenarioName)
  const runtime = values.runtime ?? scenario.config.defaultRuntime
  if (!runtimeNames.includes(runtime)) throw new Error(`unknown runtime: ${runtime} (have: ${runtimeNames.join(', ')})`)
  const adjudicatorName = values.adjudicator ?? scenario.config.defaultAdjudicator
  const minutes = values.minutes ? Number(values.minutes) : scenario.config.durationMinutes
  if (!Number.isFinite(minutes) || minutes <= 0) throw new Error('--minutes must be a positive number')

  const modelDriven = runtime !== 'scripted'
  const profile = runtimeModelProfiles[runtime]
  if (modelDriven && !profile) throw new Error(`runtime "${runtime}" is model-driven but has no model profile in src/registry.ts`)
  const agentApiKey = profile ? process.env[profile.apiKeyEnv] : undefined
  if (modelDriven && !agentApiKey) throw new Error(`runtime "${runtime}" needs ${profile!.apiKeyEnv} in .env`)
  const model = modelDriven ? {
    baseUrl: process.env.LAB_MODEL_BASE_URL ?? profile!.defaultBaseUrl,
    model: process.env.LAB_MODEL ?? profile!.defaultModel,
    reasoning: process.env.LAB_MODEL_REASONING ?? 'medium',
    apiKeyEnv: profile!.apiKeyEnv,
    contextWindow: Number(process.env.LAB_MODEL_CONTEXT_WINDOW ?? defaultDriverTuning.model.contextWindow),
    effectiveContextPercent: defaultDriverTuning.model.effectiveContextPercent,
  } : undefined
  if (model && (!Number.isFinite(model.contextWindow) || model.contextWindow <= 0)) throw new Error('LAB_MODEL_CONTEXT_WINDOW must be a positive number')

  const gateway = await connectGateway()
  status('gateway.connected', { version: gateway.version, sdkVersion: gateway.sdkVersion })
  const driverBundle = await bundleDriver()

  const result = await runHorizon(gateway, {
    scenario, adjudicator: selectAdjudicator(adjudicatorName), adjudicatorName, runtime, minutes, image: values.image,
    runId: process.env.LAB_RUN_ID ?? newRunId(), workspace: gateway.workspace,
    runsDir: process.env.LAB_RUNS_DIR ?? path.join(root, 'runs'), driverBundle,
    keepSandbox: values.keep === true, continueAfterObjective: values['continue'] === true || scenario.config.continueAfterObjective,
    oracleMinSuccessPercent: ORACLE_MIN_SUCCESS_PERCENT, model, agentApiKey,
  })
  process.stdout.write(`\n${result.objectiveReached ? 'OBJECTIVE REACHED' : 'objective not reached'} — validRun=${result.validRun}${result.invalidReasons.length ? ` (${result.invalidReasons.join(', ')})` : ''}\n${result.runDir}\n`)
  return result.objectiveReached || result.validRun ? 0 : 1
}

async function doctor(): Promise<number> {
  const lines: string[] = []
  let ok = true
  try {
    const gateway = await connectGateway()
    lines.push(`gateway:     ${gateway.endpoint}  version ${gateway.version}  (healthy)`)
    lines.push(`sdk:         @nvidia/openshell-sdk ${gateway.sdkVersion}  (matches gateway, min ${minimumOpenShellVersion})`)
  } catch (error) {
    ok = false
    lines.push(`gateway:     ERROR — ${message(error)}`)
  }
  lines.push(`scenarios:   ${Object.keys(scenarios).join(', ')}`)
  lines.push(`adjudicators:${Object.keys(adjudicators).join(', ')}`)
  lines.push(`runtimes:    ${runtimeNames.join(', ')}`)
  try { await bundleDriver(); lines.push('driver:      bundles cleanly') } catch (error) { ok = false; lines.push(`driver:      ERROR — ${message(error)}`) }
  process.stdout.write(`${lines.join('\n')}\n${ok ? 'doctor: ready' : 'doctor: not ready'}\n`)
  return ok ? 0 : 1
}

async function report(argv: string[]): Promise<number> {
  const target = argv[0]
  if (!target) throw new Error('usage: lab report <run-id | run-dir>')
  const runDir = target.includes(path.sep) ? path.resolve(target) : path.join(process.env.LAB_RUNS_DIR ?? path.join(root, 'runs'), target)
  const outcome = JSON.parse(await readFile(path.join(runDir, 'outcome.json'), 'utf8')) as Record<string, unknown>
  const events = await readJsonl(path.join(runDir, 'events.jsonl'))
  const decisions = await readJsonl(path.join(runDir, 'decisions.jsonl'))
  process.stdout.write(`${json(outcome)}\n`)
  process.stdout.write(`\nevents: ${events.length}  turns: ${events.filter((e) => e.type === 'turn.completed').length}  tool calls: ${events.filter((e) => e.type === 'tool.call').length}  decisions: ${decisions.length}\n`)
  return 0
}

async function main(): Promise<void> {
  await loadEnv()
  const [command, ...rest] = process.argv.slice(2)
  const handlers: Record<string, (argv: string[]) => Promise<number>> = { run, report, doctor: () => doctor() }
  const handler = handlers[command ?? '']
  if (!handler) { process.stderr.write('usage: lab <run|doctor|report> ...\n'); process.exitCode = 2; return }
  process.exitCode = await handler(rest)
}

main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`); process.exitCode = 1 })
