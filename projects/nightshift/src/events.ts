/**
 * The common event vocabulary shared by every agent runtime, the in-sandbox
 * driver, and every report. Runtime adapters translate their native output
 * (Codex JSON, Claude Code stream-json, a scripted agent) into these records,
 * so nothing downstream needs to know which agent ran.
 *
 * Every record is one JSON object per line. The driver stamps `timestamp`
 * inside the sandbox; the host adds `observedAt` when the line arrives so
 * analysis never depends on sandbox clock synchronization.
 */

export type NightshiftEvent =
  | { type: 'turn.started'; epoch: number; turn: number }
  | { type: 'turn.completed'; epoch: number; turn: number; toolCalls: number; usage?: Usage }
  | { type: 'tool.call'; epoch: number; turn: number; name: string; input: string; output?: string; exitCode?: number | null }
  | { type: 'message'; epoch: number; turn: number; text: string }
  | { type: 'reasoning'; epoch: number; turn: number; text: string }
  | { type: 'proposal.submitted'; epoch: number; turn: number; chunkIds: string[]; rejected: string[] }
  | { type: 'driver.backoff'; reason: string; attempt: number; delayMs: number }
  | { type: 'driver.rotation'; reason: string; fromEpoch: number; toEpoch: number; rotation: number; retainedCharacters: number; checkpoint: string }
  | { type: 'driver.runtime'; runtime: string; version?: string; model?: string }
  | { type: 'driver.error'; message: string; exitCode?: number | null }
  | { type: 'driver.refusal'; epoch: number; turn: number; message: string }
  | { type: 'host.unparsed'; text: string }

/** Token usage for one model request, in Responses API field names. */
export interface Usage {
  inputTokens: number
  cachedInputTokens: number
  outputTokens: number
  reasoningOutputTokens: number
}

export type TimestampedEvent = NightshiftEvent & { timestamp: string; observedAt?: string }

export function stamp(event: NightshiftEvent, timestamp = new Date().toISOString()): TimestampedEvent {
  return { timestamp, ...event }
}

export function serialize(event: NightshiftEvent): string {
  return JSON.stringify(stamp(event))
}

/**
 * Parse one line of driver stdout into a timestamped event. Unparseable
 * lines are preserved as `host.unparsed` so evidence is never discarded.
 */
export function parseEventLine(line: string, observedAt: string): TimestampedEvent {
  try {
    const parsed = JSON.parse(line) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && typeof (parsed as { type?: unknown }).type === 'string') {
      const record = parsed as TimestampedEvent
      return { ...record, timestamp: typeof record.timestamp === 'string' ? record.timestamp : observedAt, observedAt }
    }
  } catch {
    // fall through
  }
  return { type: 'host.unparsed', text: line, timestamp: observedAt, observedAt }
}

export function isEvent<T extends NightshiftEvent['type']>(event: NightshiftEvent, type: T): event is Extract<NightshiftEvent, { type: T }> {
  return event.type === type
}
