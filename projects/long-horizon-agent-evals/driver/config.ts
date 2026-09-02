/**
 * Configuration handed to the in-sandbox driver by the host. The host encodes
 * it as base64 JSON in `LAB_DRIVER_CONFIG_B64`; the driver never reads `.env`.
 * This file is imported by both sides so the contract cannot drift.
 */
export interface DriverConfig {
  /** Runtime name from driver/runtimes/index.ts. */
  runtime: string
  /** Fully rendered task prompt for the first thread and for rotation checkpoints. */
  prompt: string
  /** Short nudge used to resume an existing thread for the next turn. */
  resumeNudge: string
  /** Wall-clock deadline (epoch milliseconds) after which the driver exits. */
  deadlineMs: number
  model: ModelConfig
  backoff: BackoffConfig
  rotation: RotationConfig
  handoff: { maxEntries: number; maxCharacters: number }
  lull: { windowTurns: number; minIdleTurns: number; minDuplicateRate: number }
  /** Parameters for the scripted (no-model) runtime. */
  scripted?: ScriptedTask
}

export interface ModelConfig {
  /** OpenAI Responses-compatible base URL. Defaults to OpenShell's inference.local. */
  baseUrl: string
  /** Model identifier; ignored by inference.local, which pins the route's model. */
  model: string
  reasoning: string
  /** Environment variable holding the API key inside the sandbox. */
  apiKeyEnv: string
  contextWindow: number
  effectiveContextPercent: number
}

export interface BackoffConfig {
  baseSeconds: number
  maxSeconds: number
  requestTimeoutSeconds: number
}

export interface RotationConfig {
  afterConsecutiveFailures: number
  maxRotations: number
  /** Rotate after this many successful turns; 0 disables. */
  maxSuccessfulTurns: number
}

export interface ScriptedTask {
  host: string
  port: number
  path: string
  binary: string
  /** Seconds to idle between turns once the objective is reached. */
  idleSeconds: number
}

export const defaultDriverTuning = {
  backoff: { baseSeconds: 15, maxSeconds: 120, requestTimeoutSeconds: 180 },
  rotation: { afterConsecutiveFailures: 3, maxRotations: 6, maxSuccessfulTurns: 0 },
  handoff: { maxEntries: 32, maxCharacters: 24_000 },
  lull: { windowTurns: 40, minIdleTurns: 40, minDuplicateRate: 0.5 },
  model: { contextWindow: 128_000, effectiveContextPercent: 80 },
} as const

export function decodeDriverConfig(encoded: string): DriverConfig {
  const config = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')) as DriverConfig
  for (const key of ['runtime', 'prompt', 'resumeNudge', 'deadlineMs', 'model', 'backoff', 'rotation', 'handoff', 'lull'] as const) {
    if (config[key] === undefined) throw new Error(`driver config is missing ${key}`)
  }
  return config
}

export function encodeDriverConfig(config: DriverConfig): string {
  return Buffer.from(JSON.stringify(config)).toString('base64')
}
