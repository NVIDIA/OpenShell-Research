import { access, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { appendJsonl, connect, delay, json, loadEnv, redactUntrusted, required, status, writeJson } from './common.js'
import { reviewWithResponses, type ReviewerState } from './reviewer-model.js'

export function isProposalReviewStaleError(error: unknown): boolean {
  const text = error instanceof Error ? error.message : String(error)
  return [
    'proposal inputs changed; evaluation refreshed, refetch and review again',
    'review token does not match the fetched proposal; refetch and review again',
    'proposal inputs changed before persistence; refetch and review again',
  ].some((message) => text.includes(message))
}

export function reviewerModelPacket(
  sandbox: string,
  protectedRepository: string,
  chunk: Record<string, unknown>,
): Record<string, unknown> {
  return redactUntrusted({
    sandbox,
    protectedRepository,
    proposal: {
      id: chunk.id,
      ruleName: chunk.ruleName,
      rationale: chunk.rationale,
      securityNotes: chunk.securityNotes,
      confidence: chunk.confidence,
      stage: chunk.stage,
      validationResult: chunk.validationResult,
      currentEffectivePolicyHash: chunk.currentEffectivePolicyHash,
      candidateEffectivePolicyHash: chunk.candidateEffectivePolicyHash,
      proposedRule: chunk.proposedRule,
      candidateEffectivePolicy: chunk.candidateEffectivePolicy,
    },
  }) as Record<string, unknown>
}

export function reviewerHistoryPacket(packet: Record<string, unknown>): Record<string, unknown> {
  const proposal = packet.proposal && typeof packet.proposal === 'object'
    ? packet.proposal as Record<string, unknown>
    : {}
  const { candidateEffectivePolicy: _supersededPolicy, ...request } = proposal
  return { proposal: request }
}

async function main(): Promise<void> {
  await loadEnv()
  const sandbox = required('LAB_SANDBOX')
  const owner = required('LAB_GITHUB_OWNER')
  const repo = required('LAB_GITHUB_REPO')
  const runDir = required('LAB_RUN_DIR')
  const stopFile = process.env.LAB_REVIEWER_STOP_FILE
  const workspace = process.env.LAB_WORKSPACE ?? 'default'
  const deadlineMs = Number(process.env.LAB_DEADLINE_MS ?? Date.now() + 30 * 60_000)
  const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
  const instructionsTemplate = await readFile(path.join(root, 'experiments', 'github-policy-review', 'reviewer.md'), 'utf8')
  const protectedRepository = `${owner}/${repo}`
  const targetPlaceholder = '{{PROTECTED_REPOSITORY}}'
  if (!instructionsTemplate.includes(targetPlaceholder)) {
    throw new Error(`reviewer prompt is missing required placeholder ${targetPlaceholder}`)
  }
  const baseInstructions = instructionsTemplate.replaceAll(targetPlaceholder, protectedRepository)
  await writeFile(path.join(runDir, 'reviewer-prompt.md'), baseInstructions)
  const client = await connect()
  const processed = new Set<string>()
  const state: ReviewerState = { history: [] }
  let decisionNumber = 0
  let stopReason = 'deadline'

  const errorText = (error: unknown): string => {
    const text = error instanceof Error ? error.message : String(error)
    return String(redactUntrusted(text)).slice(0, 2000)
  }

  await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), { event: 'reviewer_ready', sandbox })
  await writeJson(path.join(runDir, 'reviewer-ready.json'), { sandbox, readyAt: new Date().toISOString() })
  status('reviewer.ready', { sandbox, deadlineMs })

  reviewLoop: while (Date.now() < deadlineMs) {
      if (stopFile && await access(stopFile).then(() => true).catch(() => false)) {
        stopReason = 'requested'
        break
      }
      const inbox = await client.raw.getDraftPolicy({ name: sandbox, statusFilter: 'pending', workspace })
      const chunks = inbox.chunks.filter((chunk) => !processed.has(chunk.id)).sort((a, b) => Number(a.createdAtMs - b.createdAtMs))
      if (chunks.length === 0) {
        await delay(750)
        continue
      }

      for (const chunk of chunks) {
        if (Date.now() >= deadlineMs) break
        processed.add(chunk.id)
        decisionNumber += 1
        const current = await client.sandbox.getConfig(sandbox)
        const evidencePacket = redactUntrusted({
          sandbox,
          protectedRepository,
          proposal: chunk,
          currentPolicy: current.policy,
        })
        const packet = reviewerModelPacket(sandbox, protectedRepository, chunk as unknown as Record<string, unknown>)
        const historyPacket = reviewerHistoryPacket(packet)
        status('reviewer.proposal', { sandbox, decisionNumber, ruleName: chunk.ruleName, stage: chunk.stage })
        await writeJson(path.join(runDir, `proposal-${String(decisionNumber).padStart(3, '0')}.json`), packet)
        await writeJson(path.join(runDir, `proposal-${String(decisionNumber).padStart(3, '0')}-evidence.json`), evidencePacket)

        let decision: { decision: 'approve' | 'reject'; reason: string }
        try {
          decision = await reviewWithResponses(
            runDir,
            state,
            baseInstructions,
            `Review this pending request:\n${json(packet)}`,
            decisionNumber,
            Math.max(1, deadlineMs - Date.now()),
            `Prior reviewed request (the current request contains the authoritative policy):\n${json(historyPacket)}`,
          )
        } catch (error) {
          decision = { decision: 'reject', reason: `Reviewer failed closed: ${errorText(error)}` }
        }

        status('reviewer.decision', { sandbox, decisionNumber, decision: decision.decision, reason: decision.reason })
        if (decision.decision === 'approve') {
          try {
            const reviewToken = (chunk as { reviewToken?: string }).reviewToken
            await client.raw.approveDraftChunk({
              name: sandbox,
              chunkId: chunk.id,
              workspace,
              ...(reviewToken ? { reviewToken } : {}),
            } as Parameters<typeof client.raw.approveDraftChunk>[0])
            await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
              chunkId: chunk.id,
              decisionNumber,
              ...decision,
              effectiveDecision: 'approve',
              application: 'applied',
            })
            status('reviewer.applied', { sandbox, decisionNumber, decision: 'approve' })
            // Applying an approval changes the policy inputs used to evaluate
            // every other pending request. Refetch before reviewing the rest
            // of this batch so each decision sees the current candidate.
            continue reviewLoop
          } catch (error) {
            const applicationError = errorText(error)
            if (isProposalReviewStaleError(error)) {
              await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
                chunkId: chunk.id,
                decisionNumber,
                ...decision,
                effectiveDecision: 'pending',
                application: 'review_stale_retry',
                applicationError,
              })
              await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
                event: 'review_stale_retry',
                chunkId: chunk.id,
                decisionNumber,
                error: applicationError,
              })
              status('reviewer.review_stale', { sandbox, decisionNumber, decision: 'approve', error: applicationError })
              processed.delete(chunk.id)
              await delay(100)
              continue reviewLoop
            }
            await appendJsonl(path.join(runDir, 'reviewer-errors.jsonl'), {
              event: 'approval_apply_failed',
              chunkId: chunk.id,
              decisionNumber,
              error: applicationError,
            })
            status('reviewer.apply_failed', { sandbox, decisionNumber, decision: 'approve', error: applicationError })
            try {
              await client.raw.rejectDraftChunk({
                name: sandbox,
                chunkId: chunk.id,
                workspace,
                reason: `Approval could not be applied by gateway validation; failed closed. ${applicationError}`,
              })
              await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
                chunkId: chunk.id,
                decisionNumber,
                ...decision,
                effectiveDecision: 'reject',
                application: 'approval_failed_then_rejected',
                applicationError,
              })
              status('reviewer.applied', { sandbox, decisionNumber, decision: 'reject_after_approval_failure' })
            } catch (fallbackError) {
              const fallbackApplicationError = errorText(fallbackError)
              await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
                chunkId: chunk.id,
                decisionNumber,
                ...decision,
                effectiveDecision: 'pending',
                application: 'failed',
                applicationError,
                fallbackApplicationError,
              })
              await appendJsonl(path.join(runDir, 'reviewer-errors.jsonl'), {
                event: 'fallback_rejection_failed',
                chunkId: chunk.id,
                decisionNumber,
                error: fallbackApplicationError,
              })
              status('reviewer.apply_failed', { sandbox, decisionNumber, decision: 'fallback_reject', error: fallbackApplicationError })
            }
          }
        } else {
          try {
            await client.raw.rejectDraftChunk({ name: sandbox, chunkId: chunk.id, workspace, reason: decision.reason })
            await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
              chunkId: chunk.id,
              decisionNumber,
              ...decision,
              effectiveDecision: 'reject',
              application: 'applied',
            })
            status('reviewer.applied', { sandbox, decisionNumber, decision: 'reject' })
          } catch (error) {
            const applicationError = errorText(error)
            await appendJsonl(path.join(runDir, 'decisions.jsonl'), {
              chunkId: chunk.id,
              decisionNumber,
              ...decision,
              effectiveDecision: 'pending',
              application: 'failed',
              applicationError,
            })
            await appendJsonl(path.join(runDir, 'reviewer-errors.jsonl'), {
              event: 'rejection_apply_failed',
              chunkId: chunk.id,
              decisionNumber,
              error: applicationError,
            })
            status('reviewer.apply_failed', { sandbox, decisionNumber, decision: 'reject', error: applicationError })
          }
        }
      }
    }

  await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
    event: 'reviewer_stopped',
    decisions: decisionNumber,
    reason: stopReason,
  })
  status('reviewer.stopped', { sandbox, decisions: decisionNumber, reason: stopReason })
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
    process.exitCode = 1
  })
}
