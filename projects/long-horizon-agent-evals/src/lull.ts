type Environment = Record<string, string | undefined>

export interface TurnObservation {
  /** Number of command_execution items the turn completed. */
  commands: number
  /** Concatenated agent_message text for the turn. */
  message: string
}

export interface LullOptions {
  /** Sliding window of most recent turns considered. */
  windowTurns: number
  /** Trailing turns with no command execution required to arm the detector. */
  minIdleTurns: number
  /** Fraction of the window that must be repeated message text, 0..1. */
  minDuplicateRate: number
}

export interface LullVerdict {
  stalled: boolean
  idleTurns: number
  duplicateRate: number
  distinctMessages: number
  windowTurns: number
}

export const defaultLullOptions: LullOptions = {
  windowTurns: 40,
  minIdleTurns: 40,
  minDuplicateRate: 0.5,
}

function normalize(message: string): string {
  return message.trim().toLowerCase().replace(/\s+/g, ' ')
}

function positiveInteger(name: string, raw: string): number {
  if (!/^[1-9]\d*$/.test(raw)) throw new Error(`${name} must be a positive integer`)
  return Number(raw)
}

function rate(name: string, raw: string): number {
  const value = Number(raw)
  if (!Number.isFinite(value) || value <= 0 || value > 1) throw new Error(`${name} must be between 0 and 1`)
  return value
}

export function lullOptions(env: Environment = process.env): LullOptions {
  return {
    windowTurns: positiveInteger(
      'LAB_CHALLENGER_LULL_WINDOW_TURNS',
      env.LAB_CHALLENGER_LULL_WINDOW_TURNS ?? String(defaultLullOptions.windowTurns),
    ),
    minIdleTurns: positiveInteger(
      'LAB_CHALLENGER_LULL_MIN_IDLE_TURNS',
      env.LAB_CHALLENGER_LULL_MIN_IDLE_TURNS ?? String(defaultLullOptions.minIdleTurns),
    ),
    minDuplicateRate: rate(
      'LAB_CHALLENGER_LULL_MIN_DUPLICATE_RATE',
      env.LAB_CHALLENGER_LULL_MIN_DUPLICATE_RATE ?? String(defaultLullOptions.minDuplicateRate),
    ),
  }
}

/**
 * Detect a degenerate no-progress loop.
 *
 * A stalled challenger keeps completing turns successfully, so consecutive-failure
 * rotation never fires. What distinguishes a genuine strategic pause from a
 * repetition trap is that the trap emits near-identical text with no tool use:
 * across the observed corpus, healthy runs stayed at or below a 22% duplicate
 * rate inside their longest idle stretch, while stalled runs reached 72-86%.
 *
 * Both conditions are required. Idleness alone is not sufficient — short runs
 * are legitimately idle near their deadline while still reasoning.
 */
export function detectLull(
  recent: readonly TurnObservation[],
  options: LullOptions = defaultLullOptions,
): LullVerdict {
  const window = recent.slice(-options.windowTurns)

  let idleTurns = 0
  for (let index = recent.length - 1; index >= 0; index -= 1) {
    if (recent[index]!.commands > 0) break
    idleTurns += 1
  }

  const messages = window.map((turn) => normalize(turn.message)).filter((text) => text.length > 0)
  const distinctMessages = new Set(messages).size
  const duplicateRate = messages.length > 0 ? 1 - distinctMessages / messages.length : 0

  const stalled = window.length >= options.windowTurns
    && idleTurns >= options.minIdleTurns
    && duplicateRate >= options.minDuplicateRate

  return { stalled, idleTurns, duplicateRate, distinctMessages, windowTurns: window.length }
}
