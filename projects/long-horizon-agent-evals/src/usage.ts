import { readFile } from 'node:fs/promises'
import path from 'node:path'

interface UsageRecord {
  input_tokens?: number
  cached_input_tokens?: number
  cache_write_input_tokens?: number
  output_tokens?: number
  reasoning_output_tokens?: number
  input_tokens_details?: { cached_tokens?: number | null; cache_write_tokens?: number | null }
  output_tokens_details?: { reasoning_tokens?: number | null }
  cost?: number | null
}

interface UsageSample {
  source: 'challenger' | 'reviewer'
  inputTokens: number
  cachedInputTokens: number
  cacheWriteInputTokens: number
  outputTokens: number
  reasoningOutputTokens: number
  providerReportedCostUsd: number | null
  longContext: boolean
}

const pricing = {
  model: 'gpt-5.6-sol',
  currency: 'USD',
  unit: 'per 1M tokens',
  effectiveDate: '2026-08-18',
  source: 'https://developers.openai.com/api/docs/models/gpt-5.6-sol',
  longContextThresholdInputTokens: 272_000,
  shortContext: { input: 5, cachedInput: 0.5, cacheWriteInput: 6.25, output: 30 },
  longContext: { input: 10, cachedInput: 1, cacheWriteInput: 12.5, output: 45 },
} as const

async function jsonl(file: string): Promise<Array<Record<string, unknown>>> {
  const text = await readFile(file, 'utf8').catch(() => '')
  return text.split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>] } catch { return [] }
  })
}

function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function sample(source: UsageSample['source'], usage: UsageRecord, longContext?: boolean): UsageSample {
  const inputTokens = number(usage.input_tokens)
  return {
    source,
    inputTokens,
    cachedInputTokens: number(usage.cached_input_tokens ?? usage.input_tokens_details?.cached_tokens),
    cacheWriteInputTokens: number(usage.cache_write_input_tokens ?? usage.input_tokens_details?.cache_write_tokens),
    outputTokens: number(usage.output_tokens),
    reasoningOutputTokens: number(usage.reasoning_output_tokens ?? usage.output_tokens_details?.reasoning_tokens),
    providerReportedCostUsd: typeof usage.cost === 'number' ? usage.cost : null,
    longContext: longContext ?? inputTokens > pricing.longContextThresholdInputTokens,
  }
}

function delta(current: UsageSample, previous?: UsageSample): UsageSample {
  if (!previous) return current
  // Codex emits cumulative thread usage at every turn boundary. A reset is
  // treated as a new cumulative sequence instead of producing negative usage.
  const subtract = (value: number, prior: number): number => value >= prior ? value - prior : value
  return {
    ...current,
    inputTokens: subtract(current.inputTokens, previous.inputTokens),
    cachedInputTokens: subtract(current.cachedInputTokens, previous.cachedInputTokens),
    cacheWriteInputTokens: subtract(current.cacheWriteInputTokens, previous.cacheWriteInputTokens),
    outputTokens: subtract(current.outputTokens, previous.outputTokens),
    reasoningOutputTokens: subtract(current.reasoningOutputTokens, previous.reasoningOutputTokens),
    providerReportedCostUsd: current.providerReportedCostUsd === null || previous.providerReportedCostUsd === null
      ? current.providerReportedCostUsd
      : subtract(current.providerReportedCostUsd, previous.providerReportedCostUsd),
  }
}

function estimate(sample: UsageSample): number {
  const rates = sample.longContext
    ? pricing.longContext
    : pricing.shortContext
  const uncached = Math.max(0, sample.inputTokens - sample.cachedInputTokens - sample.cacheWriteInputTokens)
  return (
    uncached * rates.input
    + sample.cachedInputTokens * rates.cachedInput
    + sample.cacheWriteInputTokens * rates.cacheWriteInput
    + sample.outputTokens * rates.output
  ) / 1_000_000
}

export interface UsageSummary {
  referencePricing: typeof pricing
  note: string
  coverage: {
    challengerComplete: boolean
    reviewerComplete: boolean
    estimateIsLowerBound: boolean
    unpricedPartialChallengerTurn: boolean
  }
  requests: { challenger: number; reviewer: number; longContext: number }
  tokens: {
    input: number
    uncachedInput: number
    cachedInput: number
    cacheWriteInput: number
    output: number
    reasoningOutput: number
  }
  byRole: Record<UsageSample['source'], {
    requests: number
    inputTokens: number
    cachedInputTokens: number
    cacheWriteInputTokens: number
    outputTokens: number
    estimatedCostUsd: number
  }>
  estimatedCostUsd: number
  providerReportedCostUsd: number | null
}

export async function summarizeUsage(runDir: string): Promise<UsageSummary> {
  const challengerEvents = await jsonl(path.join(runDir, 'challenger.jsonl'))
  const reviewerEvents = await jsonl(path.join(runDir, 'reviewer-process.jsonl'))
  const challengerCumulative = challengerEvents.flatMap((event) => event.type === 'turn.completed' && event.usage && typeof event.usage === 'object'
    // The Codex catalog used by this lab caps context at 272K. The cumulative
    // turn record may exceed that, but it is not one long-context API request.
    ? [sample('challenger', event.usage as UsageRecord, false)]
    : [])
  const challengerSamples = challengerCumulative.map((item, index) => delta(item, challengerCumulative[index - 1]))
  const lastCompletedTurnIndex = challengerEvents.findLastIndex((event) => event.type === 'turn.completed')
  const unpricedPartialChallengerTurn = challengerEvents.some((event, index) =>
    index > lastCompletedTurnIndex && (event.type === 'turn.started' || event.type === 'item.started' || event.type === 'item.completed'))
  const samples: UsageSample[] = [
    ...challengerSamples,
    ...reviewerEvents.flatMap((event) => event.event === 'review_completed' && event.usage && typeof event.usage === 'object'
      ? [sample('reviewer', event.usage as UsageRecord)]
      : []),
  ]
  const byRole = Object.fromEntries((['challenger', 'reviewer'] as const).map((role) => {
    const roleSamples = samples.filter((item) => item.source === role)
    return [role, {
      requests: roleSamples.length,
      inputTokens: roleSamples.reduce((sum, item) => sum + item.inputTokens, 0),
      cachedInputTokens: roleSamples.reduce((sum, item) => sum + item.cachedInputTokens, 0),
      cacheWriteInputTokens: roleSamples.reduce((sum, item) => sum + item.cacheWriteInputTokens, 0),
      outputTokens: roleSamples.reduce((sum, item) => sum + item.outputTokens, 0),
      estimatedCostUsd: roleSamples.reduce((sum, item) => sum + estimate(item), 0),
    }]
  })) as UsageSummary['byRole']
  const providerCosts = samples.flatMap((item) => item.providerReportedCostUsd === null ? [] : [item.providerReportedCostUsd])
  const cachedInput = samples.reduce((sum, item) => sum + item.cachedInputTokens, 0)
  const cacheWriteInput = samples.reduce((sum, item) => sum + item.cacheWriteInputTokens, 0)
  const input = samples.reduce((sum, item) => sum + item.inputTokens, 0)
  return {
    referencePricing: pricing,
    note: unpricedPartialChallengerTurn
      ? 'Observable public API equivalent lower bound; the deadline interrupted an unpriced challenger turn, and configured provider billing may differ.'
      : 'Public API equivalent estimate; configured provider billing may differ.',
    coverage: {
      challengerComplete: !unpricedPartialChallengerTurn,
      reviewerComplete: true,
      estimateIsLowerBound: unpricedPartialChallengerTurn,
      unpricedPartialChallengerTurn,
    },
    requests: {
      challenger: byRole.challenger.requests,
      reviewer: byRole.reviewer.requests,
      longContext: samples.filter((item) => item.longContext).length,
    },
    tokens: {
      input,
      uncachedInput: Math.max(0, input - cachedInput - cacheWriteInput),
      cachedInput,
      cacheWriteInput,
      output: samples.reduce((sum, item) => sum + item.outputTokens, 0),
      reasoningOutput: samples.reduce((sum, item) => sum + item.reasoningOutputTokens, 0),
    },
    byRole,
    estimatedCostUsd: samples.reduce((sum, item) => sum + estimate(item), 0),
    providerReportedCostUsd: providerCosts.length === samples.length && samples.length > 0
      ? providerCosts.reduce((sum, item) => sum + item, 0)
      : null,
  }
}
