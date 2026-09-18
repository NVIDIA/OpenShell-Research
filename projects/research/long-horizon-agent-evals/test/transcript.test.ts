import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { renderTranscript } from '../src/transcript.js'

test('transcript matches decisions by decision number', async () => {
  const runDir = await mkdtemp(path.join(tmpdir(), 'long-horizon-transcript-'))
  try {
    await writeFile(path.join(runDir, 'proposal-001.json'), JSON.stringify({ proposal: { ruleName: 'first' } }))
    await writeFile(path.join(runDir, 'proposal-002.json'), JSON.stringify({ proposal: { ruleName: 'second' } }))
    await writeFile(path.join(runDir, 'decisions.jsonl'), [
      { decisionNumber: 2, decision: 'approve', application: 'applied', effectiveDecision: 'approve', reason: 'second reason' },
      { decisionNumber: 1, decision: 'reject', application: 'applied', effectiveDecision: 'reject', reason: 'first reason' },
    ].map(JSON.stringify).join('\n'))

    const transcript = await renderTranscript(runDir)
    assert.match(transcript, /Request 1 — first[\s\S]*REJECT[\s\S]*first reason/)
    assert.match(transcript, /Request 2 — second[\s\S]*APPROVE[\s\S]*second reason/)
  } finally {
    await rm(runDir, { recursive: true, force: true })
  }
})
