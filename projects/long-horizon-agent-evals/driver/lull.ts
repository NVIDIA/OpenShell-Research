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

function normalize(message: string): string {
  return message.trim().toLowerCase().replace(/\s+/g, ' ')
}

/**
 * Detect a degenerate no-progress loop.
 *
 * A stalled agent keeps completing turns successfully, so consecutive-failure
 * rotation never fires. What distinguishes a genuine strategic pause from a
 * repetition trap is that the trap emits near-identical text with no tool use:
 * across the observed corpus, healthy runs stayed at or below a 22% duplicate
 * rate inside their longest idle stretch, while stalled runs reached 72-86%.
 *
 * Both conditions are required. Idleness alone is not sufficient: short runs
 * are legitimately idle near their deadline while still reasoning.
 */
export function detectLull(recent: readonly TurnObservation[], options: LullOptions): LullVerdict {
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
