import type { NightshiftEvent } from '../../src/events.js'
import type { DriverConfig } from '../config.js'

export interface RuntimeContext {
  config: DriverConfig
  /** Writable scratch directory inside the sandbox. */
  workdir: string
  epoch: number
  turn: number
  /** Emit one event to the host. */
  emit: (event: NightshiftEvent) => void
  /** Aborts when the driver's deadline or request timeout is reached. */
  signal: AbortSignal
}

export type TurnRequest =
  | { kind: 'start'; prompt: string }
  | { kind: 'resume'; threadId: string; nudge: string }

export interface TurnResult {
  ok: boolean
  /** Identifier the runtime needs to resume this thread next turn. */
  threadId?: string
  exitCode: number | null
  /** The failure looks like a transient model or network problem worth retrying. */
  transient?: boolean
  timedOut?: boolean
  error?: string
  /**
   * Ask the driver to checkpoint and start a fresh thread before the next turn,
   * for example because this thread's context budget is spent. Valid with or
   * without `ok`; the reason is recorded on the `driver.rotation` event.
   */
  rotate?: string
  /**
   * The model refused the task or declared itself out of options. The driver
   * records `driver.refusal`, strips the refusal and defeatist prose from the
   * checkpoint, and rotates to a fresh thread reseeded from factual progress
   * only. Never counted as backoff.
   */
  refusal?: boolean
}

/**
 * An agent runtime is two things: how to prepare the sandbox once, and how to
 * run one bounded turn, emitting common events while it does. Everything else
 * (retries, backoff, context handoff, lull detection, rotation) is the driver.
 */
export interface Runtime {
  name: string
  setup(context: Omit<RuntimeContext, 'epoch' | 'turn'>): Promise<void>
  turn(context: RuntimeContext, request: TurnRequest): Promise<TurnResult>
}
