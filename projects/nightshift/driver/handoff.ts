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

/**
 * Prose that poisons a thread: an explicit refusal, or the "I have exhausted
 * every path" defeatism that makes a model stop trying. Kept deliberately
 * phrase-specific so it strips the poison without discarding genuine progress
 * notes. Command executions are never matched here; only agent prose is.
 */
export const POISON_PATTERN = new RegExp([
  'unable to (respond|comply|assist|help|continue|proceed)',
  "can(?:no|')?t (help|assist|comply|proceed|continue) with",
  "(won'?t|will not) (help|assist|comply|be able)",
  'against (my|our) (guidelines|policy|policies|principles)',
  '(usage|content) polic(?:y|ies)',
  'violat\\w*\\s+(?:\\w+\\s+){0,3}polic',
  'I (?:must|have to|need to|will) (?:decline|refuse|stop)',
  'exhausted (?:all|every|the|my|available|each)',
  'no (?:other|more|further|remaining|viable|additional|alternative) (?:paths?|options?|approaches?|avenues?|routes?|ways?|methods?)',
  'tried (?:everything|all|every)',
  'out of (?:options|ideas|approaches)',
  'nothing (?:more|else)(?: I can| to try| left)',
  'giv(?:e|ing) up',
  'no (?:viable|feasible|remaining) way',
  'cannot be (?:achieved|accomplished|completed|done)',
].join('|'), 'i')

/** Drop refusal and defeatist agent prose, keeping every command execution and any other prose. */
export function stripPoisonedProse(entries: readonly HandoffEntry[]): HandoffEntry[] {
  return entries.filter((entry) => {
    if (entry.type === 'command_execution') return true
    return !POISON_PATTERN.test(entry.text)
  })
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
