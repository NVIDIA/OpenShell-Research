import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { summarizeUsage } from '../src/usage.js'

test('reviewer requests without usage make the estimate a lower bound', async () => {
  const runDir = await mkdtemp(path.join(tmpdir(), 'long-horizon-usage-'))
  try {
    await writeFile(path.join(runDir, 'challenger.jsonl'), '')
    await writeFile(path.join(runDir, 'reviewer-process.jsonl'), [
      { event: 'review_started', decisionNumber: 1 },
      { event: 'review_completed', decisionNumber: 1, usage: null },
    ].map(JSON.stringify).join('\n'))
    const summary = await summarizeUsage(runDir)
    assert.equal(summary.coverage.reviewerComplete, false)
    assert.equal(summary.coverage.missingReviewerUsageRequests, 1)
    assert.equal(summary.coverage.estimateIsLowerBound, true)
  } finally {
    await rm(runDir, { recursive: true, force: true })
  }
})

test('malformed usage evidence fails with file and line context', async () => {
  const runDir = await mkdtemp(path.join(tmpdir(), 'long-horizon-usage-'))
  try {
    await writeFile(path.join(runDir, 'challenger.jsonl'), '{}\nnot-json\n')
    await writeFile(path.join(runDir, 'reviewer-process.jsonl'), '')
    await assert.rejects(summarizeUsage(runDir), /challenger\.jsonl:2: invalid JSONL/)
  } finally {
    await rm(runDir, { recursive: true, force: true })
  }
})
