import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { json, redactUntrusted } from './common.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

async function optionalText(file: string): Promise<string | undefined> {
  return readFile(file, 'utf8').catch(() => undefined)
}

async function optionalJson(file: string): Promise<Record<string, unknown> | undefined> {
  const text = await optionalText(file)
  return text ? JSON.parse(text) as Record<string, unknown> : undefined
}

async function jsonl(file: string): Promise<Array<Record<string, unknown>>> {
  const text = await optionalText(file)
  if (!text) return []
  return text.split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>] } catch { return [] }
  })
}

function block(text: string, limit = 1800): string {
  const clean = (redactUntrusted(text) as string).trim()
  const bounded = clean.length > limit ? `${clean.slice(0, limit)}\n… [truncated]` : clean
  return bounded.split('\n').map((line) => `    ${line}`).join('\n')
}

function valueAt(value: unknown, ...keys: string[]): unknown {
  let current = value
  for (const key of keys) {
    if (!current || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

function firstString(value: unknown, keys: string[]): string | undefined {
  for (const key of keys) {
    const found = valueAt(value, ...key.split('.'))
    if (typeof found === 'string' && found.trim()) return found
  }
  return undefined
}

function proposalSummary(packet: Record<string, unknown>): string[] {
  const proposal = (packet.proposal ?? {}) as Record<string, unknown>
  const lines: string[] = []
  const rationale = firstString(proposal, ['intentSummary', 'intent_summary', 'analysis', 'rationale'])
  if (rationale) lines.push(`Requester rationale: ${rationale}`)
  const rule = (proposal.rule ?? proposal.proposedRule ?? proposal.proposed_rule) as Record<string, unknown> | undefined
  if (rule) {
    const binaries = Array.isArray(rule.binaries)
      ? rule.binaries.map((item) => typeof item === 'object' && item ? (item as Record<string, unknown>).path : undefined).filter(Boolean)
      : []
    const endpoints = Array.isArray(rule.endpoints) ? rule.endpoints as Array<Record<string, unknown>> : []
    for (const endpoint of endpoints) {
      const selectors: string[] = []
      if (endpoint.access) selectors.push(`access=${String(endpoint.access)}`)
      if (Array.isArray(endpoint.rules)) {
        for (const entry of endpoint.rules as Array<Record<string, unknown>>) {
          const allow = entry.allow as Record<string, unknown> | undefined
          if (allow) selectors.push(`${String(allow.method ?? '*')} ${String(allow.path ?? '**')}`)
        }
      }
      lines.push(`Capability: ${binaries.join(', ') || 'unknown binary'} → ${String(endpoint.host ?? 'unknown host')}:${String(endpoint.port ?? '')} ${selectors.join(', ')}`)
    }
  }
  const validation = firstString(proposal, ['validationResult', 'validation_result'])
  if (validation) lines.push(`Prover: ${validation.replaceAll('\n', ' | ')}`)
  const notes = proposal.securityNotes ?? proposal.security_notes
  if (Array.isArray(notes) && notes.length) lines.push(`Security notes: ${notes.map(String).join(' | ')}`)
  return lines
}

export async function renderTranscript(runDir: string): Promise<string> {
  const run = await optionalJson(path.join(runDir, 'run.json'))
  const outcome = await optionalJson(path.join(runDir, 'outcome.json'))
  const agentPrompt = await optionalText(path.join(runDir, 'agent-prompt.md'))
  const clientEvents = await jsonl(path.join(runDir, 'challenger.jsonl'))
  const decisions = await jsonl(path.join(runDir, 'decisions.jsonl'))
  const files = await readdir(runDir).catch(() => [])
  const proposalFiles = files.filter((file) => /^proposal-\d+\.json$/.test(file)).sort()
  const lines: string[] = [
    '# Experiment transcript',
    '',
    `Run: ${String(run?.runId ?? path.basename(runDir))}`,
    `Models: ${json(run?.models ?? {})}`,
    '',
  ]
  if (agentPrompt) lines.push('## Client mission', '', block(agentPrompt, 4000), '')
  lines.push('## Client reasoning summaries and attempts', '')
  let attempt = 0
  for (const event of clientEvents) {
    if (event.type !== 'item.completed' || !event.item || typeof event.item !== 'object') continue
    const item = event.item as Record<string, unknown>
    if (item.type === 'agent_message' || item.type === 'reasoning') {
      const text = firstString(item, ['text', 'summary'])
      if (text) lines.push(`### Client ${item.type === 'reasoning' ? 'reasoning summary' : 'summary'}`, '', text.trim(), '')
    }
    if (item.type === 'command_execution') {
      attempt += 1
      const command = String(item.command ?? '')
      const output = String(item.aggregated_output ?? '')
      lines.push(`### Attempt ${attempt} — exit ${String(item.exit_code ?? '?')}`, '', block(command, 2400), '')
      if (output && (String(item.status) !== 'completed' || /policy_denied|policy\.local|submitted|approved|rejected|superseded/i.test(output))) {
        lines.push('Observed result:', '', block(output), '')
      }
    }
  }

  lines.push('## Policy requests and reviewer decisions', '')
  for (const [index, proposalFile] of proposalFiles.entries()) {
    const packet = await optionalJson(path.join(runDir, proposalFile)) ?? {}
    const proposal = (packet.proposal ?? {}) as Record<string, unknown>
    const chunkId = String(proposal.id ?? '')
    const matchingDecisions = decisions.filter((item) => String(item.chunkId ?? '') === chunkId)
    const decision = matchingDecisions.at(-1) ?? decisions[index]
    lines.push(`### Request ${index + 1} — ${String(proposal.ruleName ?? proposal.rule_name ?? 'unnamed rule')}`, '')
    for (const summary of proposalSummary(packet)) lines.push(summary, '')
    if (decision) {
      lines.push(
        `Reviewer model decision: ${String(decision.decision ?? 'unknown').toUpperCase()}`,
        '',
        `Gateway result: ${String(decision.application ?? 'unknown')} (effective decision: ${String(decision.effectiveDecision ?? 'unknown')})`,
        '',
        `Rationale: ${String(decision.reason ?? '')}`,
        '',
      )
      if (decision.applicationError) lines.push(`Application error: ${String(decision.applicationError)}`, '')
    } else {
      lines.push('Reviewer: no recorded decision', '')
    }
  }

  const usage = outcome?.usage as Record<string, unknown> | undefined
  const byRole = usage?.byRole as Record<string, Record<string, unknown>> | undefined
  if (usage) {
    lines.push(
      '## Inference usage and reference cost',
      '',
      `Challenger estimate: $${Number(byRole?.challenger?.estimatedCostUsd ?? 0).toFixed(4)}`,
      '',
      `Reviewer estimate: $${Number(byRole?.reviewer?.estimatedCostUsd ?? 0).toFixed(4)}`,
      '',
      `Total estimate: $${Number(usage.estimatedCostUsd ?? 0).toFixed(4)}`,
      '',
      `${String(usage.note ?? '')}`,
      '',
    )
  }

  lines.push('## Outcome', '', block(json(outcome ?? { status: 'incomplete' }), 6000), '', '## Raw evidence', '')
  lines.push('- `challenger.jsonl` — complete observable Codex event/tool trace')
  lines.push('- `proposal-NNN.json` — exact sanitized request packet shown to the reviewer')
  lines.push('- `reviewer-NNN.response.json` — NVIDIA Responses output and token accounting')
  lines.push('- `decisions.jsonl` — reviewer approval/rejection ledger')
  lines.push('- `reviewer-errors.jsonl` — gateway decision-application failures, when present')
  lines.push('- `oracle.jsonl` — sampled observations of the assigned GitHub target')
  lines.push('- `openshell-logs.json` — enforcement and policy events')
  lines.push('', 'The transcript contains observable summaries and rationales, not hidden chain-of-thought.', '')
  return lines.join('\n')
}

async function main(): Promise<void> {
  const argument = process.argv[2]
  if (!argument) throw new Error('usage: npm run transcript -- <run-id-or-directory> [--write]')
  const direct = path.resolve(argument)
  const runDir = await stat(direct).then(() => direct).catch(() => path.join(root, 'runs', argument))
  const transcript = await renderTranscript(runDir)
  if (process.argv.includes('--write')) {
    const output = path.join(runDir, 'transcript.md')
    await writeFile(output, transcript)
    process.stdout.write(`${output}\n`)
  } else {
    process.stdout.write(transcript)
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
    process.exitCode = 1
  })
}
