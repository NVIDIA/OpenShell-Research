/**
 * The in-sandbox driver: the keepalive loop that keeps one agent working
 * toward its objective for a wall-clock horizon.
 *
 * This is the long-horizon machinery that used to live in a 393-line bash
 * script with five embedded Node programs. It runs *inside* the sandbox: the
 * host bundles this file to a single script, streams it in, and reads the
 * events it prints. It is deliberately runtime-agnostic — it asks a Runtime to
 * take one bounded turn, and owns everything around that: retry with capped
 * backoff, a bounded context handoff, no-progress lull detection, and
 * checkpointed thread rotation.
 *
 * Configuration arrives as base64 JSON in NIGHTSHIFT_DRIVER_CONFIG_B64. Every line it
 * writes to stdout is one NightshiftEvent (see src/events.ts).
 */
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { serialize, type NightshiftEvent } from '../src/events.js'
import { decodeDriverConfig, type DriverConfig } from './config.js'
import { stripPoisonedProse, trimHandoff, type HandoffEntry } from './handoff.js'
import { detectLull, type TurnObservation } from './lull.js'
import { selectRuntime } from './runtimes/index.js'
import type { Runtime, TurnRequest } from './runtimes/types.js'

function emit(event: NightshiftEvent): void {
  process.stdout.write(`${serialize(event)}\n`)
}

const clip = (value: unknown, length: number): string => String(value ?? '').slice(0, length)

/** Per-turn accumulator: the driver watches the same events it forwards. */
class TurnTally {
  commands = 0
  message = ''
  entries: HandoffEntry[] = []

  record(event: NightshiftEvent): void {
    if (event.type === 'tool.call') {
      this.commands += 1
      this.entries.push({ type: 'command_execution', command: clip(event.input, 1200), output: clip(event.output, 1800), exitCode: event.exitCode ?? null })
    } else if (event.type === 'message') {
      this.message += event.text
      const text = clip(event.text, 2400)
      if (text) this.entries.push({ type: 'agent_message', text })
    } else if (event.type === 'reasoning') {
      const text = clip(event.text, 2400)
      if (text) this.entries.push({ type: 'reasoning', text })
    }
  }
}

function backoffMs(config: DriverConfig, attempt: number, remainingMs: number): number {
  const base = config.backoff.baseSeconds * 1000
  const max = config.backoff.maxSeconds * 1000
  const exponential = Math.min(max, base * 2 ** Math.min(attempt - 1, 8))
  const jitter = Math.floor(Math.random() * exponential * 0.25)
  return Math.max(0, Math.min(max, exponential + jitter, remainingMs))
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

async function main(): Promise<void> {
  const encoded = process.env.NIGHTSHIFT_DRIVER_CONFIG_B64
  if (!encoded) throw new Error('NIGHTSHIFT_DRIVER_CONFIG_B64 is required')
  const config = decodeDriverConfig(encoded)
  const runtime: Runtime = selectRuntime(config.runtime)
  const workdir = config.cwd ?? await mkdtemp(path.join(tmpdir(), 'nightshift-driver-'))

  await runtime.setup({ config, workdir, emit, signal: AbortSignal.timeout(60_000) })

  let epoch = 1
  let turn = 0
  let threadId: string | undefined
  let consecutiveFailures = 0
  let rotations = 0
  let successfulTurns = 0
  let startingPrompt = config.prompt
  const handoff: HandoffEntry[] = []
  const observations: TurnObservation[] = []

  const rotate = (reason: string): void => {
    const checkpoint = handoff.map((entry) => JSON.stringify(entry)).join('\n')
    emit({ type: 'driver.rotation', reason, fromEpoch: epoch, toEpoch: epoch + 1, rotation: rotations + 1, retainedCharacters: checkpoint.length, checkpoint })
    rotations += 1
    epoch += 1
    threadId = undefined
    successfulTurns = 0
    consecutiveFailures = 0
    observations.length = 0
    startingPrompt = [
      config.prompt,
      '',
      `Thread recovery checkpoint: this is agent epoch ${epoch} after ${reason}.`,
      'The same sandbox, filesystem, effective policy, and deadline persist. Inspect current state before acting and avoid repeating prior approaches without new evidence.',
      '',
      'Recent observable activity from the previous thread (bounded JSONL):',
      checkpoint,
    ].join('\n')
  }

  while (Date.now() < config.deadlineMs) {
    turn += 1
    const remainingMs = config.deadlineMs - Date.now()
    const turnTimeoutMs = Math.min(config.turnTimeoutSeconds * 1000, remainingMs)
    const signal = AbortSignal.timeout(Math.max(1, turnTimeoutMs))
    const tally = new TurnTally()
    const context = {
      config,
      workdir,
      epoch,
      turn,
      signal,
      emit: (event: NightshiftEvent) => { tally.record(event); emit(event) },
    }
    emit({ type: 'turn.started', epoch, turn })
    const request: TurnRequest = threadId
      ? { kind: 'resume', threadId, nudge: config.resumeNudge }
      : { kind: 'start', prompt: startingPrompt }

    const result = await runtime.turn(context, request)

    for (const entry of tally.entries) handoff.push(entry)
    const trimmed = trimHandoff(handoff, config.handoff)
    handoff.length = 0
    handoff.push(...trimmed)

    if (result.ok) {
      threadId = result.threadId ?? threadId
      consecutiveFailures = 0
      successfulTurns += 1
      observations.push({ commands: tally.commands, message: tally.message })
      const mayRotate = rotations < config.rotation.maxRotations
      if (result.rotate && mayRotate) {
        rotate(result.rotate)
      } else if (mayRotate && handoff.length > 0 && detectLull(observations, config.lull).stalled) {
        rotate('no_progress_lull')
      } else if (mayRotate && config.rotation.maxSuccessfulTurns > 0 && successfulTurns >= config.rotation.maxSuccessfulTurns) {
        rotate('successful_turn_budget')
      }
      continue
    }

    if (result.rotate && rotations < config.rotation.maxRotations) {
      // This thread cannot continue (for example, its context is exhausted) but a fresh one can.
      rotate(result.rotate)
      continue
    }

    if (result.refusal) {
      // A hard refusal or "out of options" declaration. Record it, scrub the
      // poison from the checkpoint so it does not follow the agent, and restart
      // a fresh thread from factual progress. Never backoff. Bounded by the cap.
      emit({ type: 'driver.refusal', epoch, turn, message: clip(result.error, 2000) })
      if (rotations < config.rotation.maxRotations) {
        const cleaned = stripPoisonedProse(handoff)
        handoff.length = 0
        handoff.push(...cleaned)
        rotate('model_refusal')
        continue
      }
      emit({ type: 'driver.error', message: 'model refused and the rotation budget is exhausted', exitCode: result.exitCode })
      process.exitCode = result.exitCode ?? 1
      return
    }

    if (result.transient) {
      consecutiveFailures += 1
      const wait = backoffMs(config, consecutiveFailures, Math.max(0, config.deadlineMs - Date.now()))
      emit({ type: 'driver.backoff', reason: result.timedOut ? 'request_timeout' : 'transient_error', attempt: consecutiveFailures, delayMs: wait })
      if (consecutiveFailures >= config.rotation.afterConsecutiveFailures && rotations < config.rotation.maxRotations && handoff.length > 0) {
        rotate(`consecutive_${result.timedOut ? 'timeout' : 'transient_error'}`)
      }
      if (wait > 0) await delay(wait)
      continue
    }

    // A full agent harness can exit non-zero mid-run (for example a CLI stumble
    // on resume). When it has already produced recoverable work and rotations
    // remain, restart a fresh thread from the checkpoint instead of ending the
    // whole run; only give up when there is nothing to recover or the cap is hit.
    emit({ type: 'driver.error', message: clip(result.error, 2000), exitCode: result.exitCode })
    if (rotations < config.rotation.maxRotations && handoff.length > 0) {
      rotate(`runtime_exit${typeof result.exitCode === 'number' ? `_${result.exitCode}` : ''}`)
      continue
    }
    process.exitCode = result.exitCode ?? 1
    return
  }
}

main().catch((error) => {
  emit({ type: 'driver.error', message: error instanceof Error ? error.message : String(error) })
  process.exitCode = 1
})
