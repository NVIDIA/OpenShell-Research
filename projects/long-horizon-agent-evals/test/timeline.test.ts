import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { buildTimeline, timelineCsv, timelineMarkdown } from '../src/timeline.js'

test('builds a chronological cross-system policy timeline', async () => {
  const runDir = await mkdtemp(path.join(tmpdir(), 'long-horizon-timeline-'))
  try {
    await writeFile(path.join(runDir, 'run.json'), JSON.stringify({ deadlineMs: 3_600_000, durationMinutes: 60 }))
    await writeFile(path.join(runDir, 'proposal-001.json'), JSON.stringify({
      proposal: { id: 'chunk-1', ruleName: 'github_write', rationale: 'request write', createdAtMs: '1000' },
    }))
    await writeFile(path.join(runDir, 'reviewer-process.jsonl'), [
      { timestamp: '1970-01-01T00:00:02.000Z', event: 'review_started', decisionNumber: 1 },
      { timestamp: '1970-01-01T00:00:03.000Z', event: 'review_completed', decisionNumber: 1 },
    ].map(JSON.stringify).join('\n'))
    await writeFile(path.join(runDir, 'decisions.jsonl'), JSON.stringify({
      timestamp: '1970-01-01T00:00:04.000Z', decisionNumber: 1, chunkId: 'chunk-1',
      decision: 'reject', effectiveDecision: 'reject', application: 'applied', reason: 'unsafe write',
    }))
    await writeFile(path.join(runDir, 'challenger.jsonl'), JSON.stringify({
      timestamp: '1970-01-01T00:00:00.500Z', observedAt: '1970-01-01T00:00:00.500Z', type: 'turn.completed',
    }))

    const events = await buildTimeline(runDir)
    assert.deepEqual(events.map((event) => event.event), [
      'turn.completed', 'proposal.created', 'review.started', 'review.completed', 'decision.applied',
    ])
    assert.equal(events.at(-1)?.decision, 'reject')
    assert.match(timelineCsv(events), /decision\.applied/)
    assert.match(timelineMarkdown(events), /unsafe write/)
  } finally {
    await rm(runDir, { recursive: true, force: true })
  }
})
