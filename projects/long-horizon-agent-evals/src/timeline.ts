import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { redactUntrusted } from './common.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

type JsonRecord = Record<string, unknown>

export interface TimelineEvent {
  timestamp: string
  elapsedMs: number
  system: 'challenger' | 'openshell' | 'reviewer'
  event: string
  summary: string
  decisionNumber?: number
  chunkId?: string
  ruleName?: string
  decision?: string
  effectiveDecision?: string
  application?: string
}

async function optionalText(file: string): Promise<string | undefined> {
  return readFile(file, 'utf8').catch(() => undefined)
}

async function optionalJson(file: string): Promise<JsonRecord | undefined> {
  const text = await optionalText(file)
  return text ? JSON.parse(text) as JsonRecord : undefined
}

async function jsonl(file: string): Promise<JsonRecord[]> {
  const text = await optionalText(file)
  if (!text) return []
  return text.split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as JsonRecord] } catch { return [] }
  })
}

function string(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

function number(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN
  return Number.isFinite(parsed) ? parsed : undefined
}

function bounded(value: unknown, limit = 360): string {
  const text = String(redactUntrusted(value ?? '')).replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

function challengerSummary(record: JsonRecord): { event: string; summary: string } | undefined {
  if (record.type === 'thread.started') return { event: 'thread.started', summary: `Thread ${String(record.thread_id ?? 'started')}` }
  if (record.type === 'turn.completed') return { event: 'turn.completed', summary: 'Challenger turn completed' }
  if (record.type === 'lab.backoff') {
    return { event: 'model.backoff', summary: `${String(record.reason ?? 'transient error')}; delay ${String(record.delay_ms ?? '?')} ms` }
  }
  if (record.type === 'lab.unparsed_stdout') return { event: 'stdout.unparsed', summary: bounded(record.text) }
  if (record.type !== 'item.completed' || !record.item || typeof record.item !== 'object') return undefined
  const item = record.item as JsonRecord
  if (item.type === 'command_execution') {
    return {
      event: 'command.completed',
      summary: `${bounded(item.command)} [${String(item.status ?? 'unknown')}; exit ${String(item.exit_code ?? '?')}]`,
    }
  }
  if (item.type === 'agent_message') return { event: 'agent.message', summary: bounded(item.text ?? item.summary) }
  if (item.type === 'reasoning') return { event: 'reasoning.summary', summary: bounded(item.text ?? item.summary) }
  return undefined
}

function timestamp(value: unknown): string | undefined {
  const direct = string(value)
  if (direct && !/^\d+$/.test(direct) && Number.isFinite(Date.parse(direct))) return new Date(direct).toISOString()
  const milliseconds = number(value)
  return milliseconds === undefined ? undefined : new Date(milliseconds).toISOString()
}

export async function buildTimeline(runDir: string): Promise<TimelineEvent[]> {
  const run = await optionalJson(path.join(runDir, 'run.json')) ?? {}
  const deadlineMs = number(run.deadlineMs) ?? 0
  const durationMinutes = number(run.durationMinutes) ?? 0
  const startedAtMs = deadlineMs - durationMinutes * 60_000
  const files = await readdir(runDir).catch(() => [])
  const proposalFiles = files.filter((file) => /^proposal-\d+\.json$/.test(file)).sort()
  const reviewerEvents = await jsonl(path.join(runDir, 'reviewer-process.jsonl'))
  const decisions = await jsonl(path.join(runDir, 'decisions.jsonl'))
  const challengerEvents = await jsonl(path.join(runDir, 'challenger.jsonl'))
  const openshell = await optionalJson(path.join(runDir, 'openshell-logs.json'))
  const events: Array<Omit<TimelineEvent, 'elapsedMs'>> = []
  const proposalsByNumber = new Map<number, { chunkId?: string; ruleName?: string }>()

  for (const [index, proposalFile] of proposalFiles.entries()) {
    const packet = await optionalJson(path.join(runDir, proposalFile)) ?? {}
    const proposal = packet.proposal && typeof packet.proposal === 'object' ? packet.proposal as JsonRecord : {}
    const decisionNumber = index + 1
    const proposalMeta = { chunkId: string(proposal.id), ruleName: string(proposal.ruleName) }
    proposalsByNumber.set(decisionNumber, proposalMeta)
    const createdAt = timestamp(proposal.createdAtMs ?? proposal.firstSeenMs)
    if (createdAt) {
      events.push({
        timestamp: createdAt,
        system: 'openshell',
        event: 'proposal.created',
        summary: bounded(proposal.rationale ?? proposal.ruleName ?? 'Policy proposal created'),
        decisionNumber,
        ...proposalMeta,
      })
    }
  }

  const decisionsByNumber = new Map<number, JsonRecord>()
  for (const [index, decision] of decisions.entries()) {
    decisionsByNumber.set(number(decision.decisionNumber) ?? index + 1, decision)
  }

  for (const record of reviewerEvents) {
    const at = timestamp(record.timestamp)
    const decisionNumber = number(record.decisionNumber)
    if (!at || !decisionNumber || !['review_started', 'review_completed', 'review_retry', 'review_stale_retry'].includes(String(record.event))) continue
    const decision = decisionsByNumber.get(decisionNumber)
    const proposal = proposalsByNumber.get(decisionNumber)
    const completed = record.event === 'review_completed'
    events.push({
      timestamp: at,
      system: 'reviewer',
      event: String(record.event).replaceAll('_', '.'),
      summary: completed
        ? bounded(decision?.reason ?? `Review ${decisionNumber} completed`)
        : bounded(record.error ?? `Review ${decisionNumber} ${String(record.event).replace('review_', '')}`),
      decisionNumber,
      chunkId: string(record.chunkId ?? decision?.chunkId ?? proposal?.chunkId),
      ruleName: proposal?.ruleName,
      decision: completed ? string(decision?.decision) : undefined,
    })
  }

  for (const [index, record] of decisions.entries()) {
    const at = timestamp(record.timestamp)
    if (!at) continue
    const decisionNumber = number(record.decisionNumber) ?? index + 1
    const proposal = proposalsByNumber.get(decisionNumber)
    events.push({
      timestamp: at,
      system: 'openshell',
      event: 'decision.applied',
      summary: bounded(record.reason ?? record.applicationError ?? 'Decision applied'),
      decisionNumber,
      chunkId: string(record.chunkId ?? proposal?.chunkId),
      ruleName: proposal?.ruleName,
      decision: string(record.decision),
      effectiveDecision: string(record.effectiveDecision),
      application: string(record.application),
    })
  }

  for (const record of challengerEvents) {
    const at = timestamp(record.observedAt ?? record.timestamp)
    const summary = challengerSummary(record)
    if (!at || !summary) continue
    events.push({ timestamp: at, system: 'challenger', ...summary })
  }

  const logs = Array.isArray(openshell?.logs) ? openshell.logs as JsonRecord[] : []
  for (const record of logs) {
    const message = string(record.message) ?? ''
    if (!/DENIED|policy reloaded|CONFIG:(?:DETECTED|CONFIGURED|LOADED)/i.test(message)) continue
    const at = timestamp(record.timestampMs)
    if (!at) continue
    events.push({ timestamp: at, system: 'openshell', event: 'enforcement.event', summary: bounded(message) })
  }

  return events
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
    .map((event) => ({ ...event, elapsedMs: Math.max(0, Date.parse(event.timestamp) - startedAtMs) }))
}

function csvCell(value: unknown): string {
  const text = value === undefined ? '' : String(value)
  return `"${text.replaceAll('"', '""')}"`
}

export function timelineCsv(events: TimelineEvent[]): string {
  const keys: Array<keyof TimelineEvent> = [
    'timestamp', 'elapsedMs', 'system', 'event', 'decisionNumber', 'chunkId', 'ruleName',
    'decision', 'effectiveDecision', 'application', 'summary',
  ]
  return `${keys.join(',')}\n${events.map((event) => keys.map((key) => csvCell(event[key])).join(',')).join('\n')}\n`
}

function elapsed(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

export function timelineMarkdown(events: TimelineEvent[]): string {
  const rows = events.map((event) => [
    elapsed(event.elapsedMs), event.system, event.event, event.decisionNumber ?? '',
    event.decision ?? '', event.ruleName ?? '', event.summary.replaceAll('|', '\\|'),
  ])
  return [
    '# Experiment timeline', '',
    '| Elapsed | System | Event | Request | Decision | Rule | Summary |',
    '| --- | --- | --- | ---: | --- | --- | --- |',
    ...rows.map((row) => `| ${row.join(' | ')} |`),
    '',
  ].join('\n')
}

async function main(): Promise<void> {
  const argument = process.argv[2]
  if (!argument) throw new Error('usage: npm run timeline -- <run-id-or-directory> [--write]')
  const direct = path.resolve(argument)
  const runDir = await stat(direct).then(() => direct).catch(() => path.join(root, 'runs', argument))
  const events = await buildTimeline(runDir)
  const markdown = timelineMarkdown(events)
  if (process.argv.includes('--write')) {
    await writeFile(path.join(runDir, 'timeline.jsonl'), `${events.map((event) => JSON.stringify(event)).join('\n')}\n`)
    await writeFile(path.join(runDir, 'timeline.csv'), timelineCsv(events))
    await writeFile(path.join(runDir, 'timeline.md'), markdown)
    process.stdout.write(`${path.join(runDir, 'timeline.md')}\n`)
  } else {
    process.stdout.write(markdown)
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
    process.exitCode = 1
  })
}
