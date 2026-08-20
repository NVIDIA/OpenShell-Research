import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { isDeepStrictEqual } from 'node:util'
import { delay, integer, loadEnv, required, writeJson } from './common.js'
import { createGithubBranch, getGithubRepositoryState } from './github.js'
import { scaleRuntimeOptions } from './runtime-options.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

interface CampaignResult {
  runId: string
  attemptIndex?: number
  logicalRun?: number
  exitCode: number | null
  runDir?: string
  compromised?: boolean
  requiresAdjudication?: boolean
  validRun?: boolean
  invalidReasons?: string[]
  estimatedCostUsd?: number
  estimatedCostIsLowerBound?: boolean
  error?: string
}

interface CampaignPlan {
  runId: string
  branch: string
  target: string
}

async function runCampaign(plan: CampaignPlan): Promise<CampaignResult> {
  const { runId } = plan
  const tsx = path.join(root, 'node_modules', '.bin', 'tsx')
  return new Promise((resolve) => {
    let stdout = ''
    let stderr = ''
    const child = spawn(tsx, [path.join(root, 'src', 'campaign.ts')], {
      cwd: root,
      env: {
        ...process.env,
        LAB_RUN_ID: runId,
        LAB_ASSIGNED_BRANCH: plan.branch,
        LAB_ASSIGNED_TARGET: plan.target,
        LAB_BRANCH_PREPARED: '1',
        LAB_SHARED_REPOSITORY: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk
      process.stdout.write(chunk)
    })
    child.stderr.on('data', (chunk: string) => (stderr += chunk))
    child.once('error', (error) => resolve({ runId, exitCode: null, error: error.message }))
    child.once('close', async (exitCode) => {
      const resultEvent = stdout.trim().split('\n').flatMap((line) => {
        try {
          const parsed = JSON.parse(line) as { event?: string; runDir?: string }
          return parsed.event === 'campaign.result' && parsed.runDir ? [parsed] : []
        } catch {
          return []
        }
      }).at(-1)
      const runDir = resultEvent?.runDir
      if (!runDir) {
        resolve({ runId, exitCode, error: stderr.trim() || 'campaign did not report a run directory' })
        return
      }
      try {
        const outcome = JSON.parse(await readFile(path.join(runDir, 'outcome.json'), 'utf8')) as {
          compromised?: boolean
          validRun?: boolean
          invalidReasons?: string[]
          requiresAdjudication?: boolean
          usage?: { estimatedCostUsd?: number; coverage?: { estimateIsLowerBound?: boolean } }
        }
        resolve({
          runId,
          exitCode,
          runDir,
          compromised: outcome.compromised === true,
          validRun: outcome.validRun === true,
          invalidReasons: outcome.invalidReasons,
          requiresAdjudication: outcome.requiresAdjudication === true,
          estimatedCostUsd: outcome.usage?.estimatedCostUsd,
          estimatedCostIsLowerBound: outcome.usage?.coverage?.estimateIsLowerBound === true,
          ...(exitCode === 0 ? {} : { error: stderr.trim() || `campaign exited ${exitCode}` }),
        })
      } catch (error) {
        resolve({ runId, exitCode, runDir, error: error instanceof Error ? error.message : String(error) })
      }
    })
  })
}

async function main(): Promise<void> {
  await loadEnv()
  const runtime = scaleRuntimeOptions(process.argv.slice(2))
  process.env.LAB_DURATION_MINUTES = String(runtime.minutes)
  const startedAt = new Date().toISOString()
  const runs = runtime.runs
  const concurrency = runtime.concurrency
  const maxAttempts = process.env.LAB_MAX_ATTEMPTS
    ? integer('LAB_MAX_ATTEMPTS', runs)
    : runs + Math.max(10, Math.ceil(runs / 2))
  if (maxAttempts < runs) throw new Error('LAB_MAX_ATTEMPTS must be at least LAB_RUNS')
  const retryDelaySeconds = integer('LAB_RETRY_DELAY_SECONDS', 10)
  const rateLimitCooldownSeconds = integer('LAB_RATE_LIMIT_COOLDOWN_SECONDS', 300)
  const owner = required('LAB_GITHUB_OWNER')
  const repo = required('LAB_GITHUB_REPO')
  const githubToken = required('LAB_GITHUB_TOKEN')
  const sourceBranch = process.env.LAB_GITHUB_BRANCH ?? 'main'
  const summaryDir = path.resolve(process.env.LAB_RUNS_DIR ?? path.join(root, 'runs'))
  await mkdir(summaryDir, { recursive: true })
  const prefix = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)
  const plans: CampaignPlan[] = Array.from({ length: maxAttempts }, (_item, index) => ({
    runId: `${prefix}-${String(index + 1).padStart(4, '0')}-${randomBytes(3).toString('hex')}`,
    branch: `work/${randomBytes(10).toString('hex')}`,
    target: `artifacts/${randomBytes(16).toString('hex')}.txt`,
  }))
  const repositoryStateBeforeSetup = await getGithubRepositoryState(githubToken, owner, repo)
  process.stdout.write(`preparing ${maxAttempts} isolated branches for up to ${maxAttempts} attempts\n`)
  for (const plan of plans) {
    await createGithubBranch(githubToken, owner, repo, plan.branch, sourceBranch)
  }
  const initialRepositoryState = await getGithubRepositoryState(githubToken, owner, repo)
  const planFile = path.join(summaryDir, `scale-${prefix}-plan.json`)
  await writeJson(planFile, {
    startedAt,
    owner,
    repo,
    sourceBranch,
    durationMinutes: runtime.minutes,
    runs,
    targetValidRuns: runs,
    maxAttempts,
    concurrency,
    retryDelaySeconds,
    rateLimitCooldownSeconds,
    repositoryStateBeforeSetup,
    initialRepositoryState,
    plans,
  })
  process.stdout.write(`${planFile}\n`)
  const results: CampaignResult[] = []
  const acceptedResults: CampaignResult[] = new Array(runs)
  let nextAttempt = 0
  let nextLogicalRun = 0

  await Promise.all(Array.from({ length: concurrency }, async () => {
    while (true) {
      const logicalRun = nextLogicalRun++
      if (logicalRun >= runs) return
      while (true) {
        const attemptIndex = nextAttempt++
        const plan = plans[attemptIndex]
        if (!plan) return
        process.stdout.write(`starting logical run ${logicalRun + 1}/${runs}, attempt ${attemptIndex + 1}/${maxAttempts}\n`)
        const result = {
          ...await runCampaign(plan),
          logicalRun: logicalRun + 1,
          attemptIndex: attemptIndex + 1,
        }
        results[attemptIndex] = result
        process.stdout.write(`finished logical run ${logicalRun + 1}/${runs}, attempt ${attemptIndex + 1}/${maxAttempts}: ${JSON.stringify(result)}\n`)
        if (result.validRun) {
          acceptedResults[logicalRun] = result
          break
        }
        const rateLimited = result.invalidReasons?.some((reason) => reason.includes('model_backoff') || reason === 'reviewer_model_failure') ?? false
        const cooldownSeconds = rateLimited ? rateLimitCooldownSeconds : retryDelaySeconds
        process.stdout.write(`excluding attempt ${attemptIndex + 1}; retrying logical run ${logicalRun + 1} after ${cooldownSeconds}s\n`)
        await delay(cooldownSeconds * 1000)
      }
    }
  }))

  const completedResults = results.filter((result): result is CampaignResult => Boolean(result))
  const completedAcceptedResults = acceptedResults.filter((result): result is CampaignResult => Boolean(result))
  const finalRepositoryState = await getGithubRepositoryState(githubToken, owner, repo)
  const globalRepositoryMutationObserved = !isDeepStrictEqual(initialRepositoryState, finalRepositoryState)
  const compromises = completedAcceptedResults.filter((result) => result.compromised).length
  const estimatedCostUsd = completedResults.reduce((sum, result) => sum + (result.estimatedCostUsd ?? 0), 0)
  const targetReached = completedAcceptedResults.length === runs
  const summary = {
    startedAt,
    finishedAt: new Date().toISOString(),
    owner,
    repo,
    sourceBranch,
    durationMinutes: runtime.minutes,
    runs,
    targetValidRuns: runs,
    maxAttempts,
    concurrency,
    targetReached,
    attempts: completedResults.length,
    excludedInvalidAttempts: completedResults.length - completedAcceptedResults.length,
    globalRepositoryMutationObserved,
    initialRepositoryState,
    finalRepositoryState,
    compromises,
    securityFailureObserved: globalRepositoryMutationObserved || compromises > 0,
    validRuns: completedAcceptedResults.length,
    invalidRuns: completedResults.filter((result) => !result.validRun).length,
    requiresAdjudicationRuns: completedAcceptedResults.filter((result) => result.requiresAdjudication).length,
    cleanValidRuns: globalRepositoryMutationObserved
      ? 0
      : completedAcceptedResults.filter((result) => !result.compromised && !result.requiresAdjudication).length,
    failures: completedResults.filter((result) => result.error).length,
    estimatedCostUsd,
    estimatedCostIsLowerBound: completedResults.some((result) => result.estimatedCostIsLowerBound),
    lowerBoundCostAttempts: completedResults.filter((result) => result.estimatedCostIsLowerBound).length,
    estimatedAcceptedRunsCostUsd: completedAcceptedResults.reduce((sum, result) => sum + (result.estimatedCostUsd ?? 0), 0),
    acceptedResults: completedAcceptedResults,
    results: completedResults,
  }
  const file = path.join(summaryDir, `scale-${prefix}-summary.json`)
  await writeJson(file, summary)
  process.stdout.write(`${file}\n`)
  if (!targetReached) process.exitCode = 1
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
    process.exitCode = 1
  })
}
