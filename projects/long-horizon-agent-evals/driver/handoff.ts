export interface ReasoningHandoffEntry {
  type: 'reasoning'
  text: string
}

export interface AgentMessageHandoffEntry {
  type: 'agent_message'
  text: string
}

export interface CommandExecutionHandoffEntry {
  type: 'command_execution'
  command: string
  output: string
  exitCode: number | null
}

export type HandoffEntry =
  | ReasoningHandoffEntry
  | AgentMessageHandoffEntry
  | CommandExecutionHandoffEntry

export interface HandoffOptions {
  maxEntries: number
  maxCharacters: number
}

function normalize(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ')
}

/** Index of the oldest entry safe to drop: prose before commands, oldest first. */
function dropIndex(entries: readonly HandoffEntry[]): number {
  const prose = entries.findIndex((entry) => entry.type !== 'command_execution')
  return prose === -1 ? 0 : prose
}

function serializedCharacters(entries: readonly HandoffEntry[]): number {
  return entries.reduce((total, entry) => total + JSON.stringify(entry).length + 1, 0)
}

/**
 * Build a bounded thread-recovery checkpoint from accumulated observable activity.
 */
export function trimHandoff(entries: readonly HandoffEntry[], options: HandoffOptions): HandoffEntry[] {
  const seenProse = new Set<string>()
  const retained: HandoffEntry[] = []

  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]!
    if (entry.type === 'command_execution') {
      retained.push(entry)
      continue
    }

    const normalized = normalize(entry.text)
    if (normalized && seenProse.has(normalized)) continue
    if (normalized) seenProse.add(normalized)
    retained.push(entry)
  }
  retained.reverse()

  while (
    retained.length > options.maxEntries
    || serializedCharacters(retained) > options.maxCharacters
  ) {
    retained.splice(dropIndex(retained), 1)
  }

  return retained
}
