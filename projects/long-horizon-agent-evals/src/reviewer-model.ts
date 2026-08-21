import path from 'node:path'
import { appendJsonl, delay, integer, redactUntrusted, writeJson } from './common.js'

export interface ReviewDecision {
  decision: 'approve' | 'reject'
  reason: string
}

interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ReviewerState {
  history: ConversationMessage[]
}

function historyCharacters(state: ReviewerState): number {
  return state.history.reduce((total, message) => total + message.content.length, 0)
}

export function compactReviewerHistory(
  state: ReviewerState,
  maxMessages: number,
  maxCharacters: number,
): number {
  let droppedMessages = 0
  while (state.history.length > maxMessages || historyCharacters(state) > maxCharacters) {
    const count = Math.min(2, state.history.length)
    state.history.splice(0, count)
    droppedMessages += count
  }
  return droppedMessages
}

interface ResponsesBody {
  id?: string
  model?: string
  status?: string
  output?: Array<{
    type?: string
    summary?: Array<{ type?: string; text?: string }>
    content?: Array<{ type?: string; text?: string }>
  }>
  usage?: unknown
  error?: { type?: string; code?: string; message?: string }
}

export function isContextLengthExceeded(status: number, body: ResponsesBody): boolean {
  const code = body.error?.code ?? body.error?.type ?? ''
  const message = body.error?.message ?? ''
  return status === 400 && (code === 'context_length_exceeded' || /context (?:window|length)|input exceeds/i.test(message))
}

function outputText(body: ResponsesBody): string {
  return (body.output ?? [])
    .flatMap((item) => item.content ?? [])
    .filter((item) => item.type === 'output_text' && typeof item.text === 'string')
    .map((item) => item.text as string)
    .join('\n')
}

function reasoningSummaries(body: ResponsesBody): string[] {
  return (body.output ?? [])
    .filter((item) => item.type === 'reasoning')
    .flatMap((item) => item.summary ?? [])
    .map((item) => item.text)
    .filter((item): item is string => Boolean(item))
}

function retryAfterMs(response: Response): number | undefined {
  const value = response.headers.get('retry-after')
  if (!value) return undefined
  const seconds = Number(value)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000
  const date = Date.parse(value)
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : undefined
}

function transientStatus(status: number): boolean {
  return status === 429 || status === 500 || status === 502 || status === 503 || status === 504
}

export async function reviewWithResponses(
  runDir: string,
  state: ReviewerState,
  baseInstructions: string,
  prompt: string,
  decisionNumber: number,
  timeoutMs: number,
  historyPrompt = prompt,
): Promise<ReviewDecision> {
  const apiKey = process.env.LAB_REVIEWER_API_KEY
  if (!apiKey) throw new Error('LAB_REVIEWER_API_KEY is required for the reviewer')
  const model = process.env.LAB_REVIEWER_MODEL
  if (!model) throw new Error('LAB_REVIEWER_MODEL is required for the reviewer')
  const reasoning = process.env.LAB_REVIEWER_REASONING ?? 'high'
  const url = process.env.LAB_REVIEWER_RESPONSES_URL
  if (!url) throw new Error('LAB_REVIEWER_RESPONSES_URL is required for the reviewer')
  const request = () => ({
    model,
    input: [
      { role: 'developer', content: baseInstructions },
      ...state.history,
      { role: 'user', content: prompt },
    ],
    reasoning: { effort: reasoning, summary: 'detailed' },
    text: {
      format: {
        type: 'json_schema',
        name: 'review_decision',
        strict: true,
        schema: {
          type: 'object',
          additionalProperties: false,
          required: ['decision', 'reason'],
          properties: {
            decision: { type: 'string', enum: ['approve', 'reject'] },
            reason: { type: 'string', minLength: 1 },
          },
        },
      },
    },
    max_output_tokens: 2048,
  })

  await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
    event: 'review_started',
    decisionNumber,
    model,
    reasoning,
    priorMessages: state.history.length,
  })
  const startedAt = Date.now()
  const deadline = startedAt + timeoutMs
  const baseBackoffMs = integer('LAB_MODEL_BACKOFF_BASE_SECONDS', 15) * 1000
  const maxBackoffMs = integer('LAB_MODEL_BACKOFF_MAX_SECONDS', 120) * 1000
  const requestTimeoutMs = integer('LAB_MODEL_REQUEST_TIMEOUT_SECONDS', 180) * 1000
  const maxHistoryMessages = integer('LAB_REVIEWER_HISTORY_MAX_MESSAGES', 16)
  const maxHistoryCharacters = integer('LAB_REVIEWER_HISTORY_MAX_CHARACTERS', 240_000)
  let attempt = 0
  let contextReset = false
  let response: Response | undefined
  let body: ResponsesBody = {}
  let lastError: unknown
  while (Date.now() < deadline) {
    attempt += 1
    const remainingMs = Math.max(1, deadline - Date.now())
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(request()),
        signal: AbortSignal.timeout(Math.min(requestTimeoutMs, remainingMs)),
      })
      body = (await response.json().catch(() => ({}))) as ResponsesBody
      await writeJson(
        path.join(runDir, `reviewer-${String(decisionNumber).padStart(3, '0')}-attempt-${String(attempt).padStart(3, '0')}.response.json`),
        redactUntrusted(body),
      )
      if (response.ok) break
      lastError = new Error(`Responses HTTP ${response.status}: ${body.error?.message ?? 'unknown error'}`)
      if (isContextLengthExceeded(response.status, body) && state.history.length > 0 && !contextReset) {
        const droppedMessages = state.history.length
        state.history.length = 0
        contextReset = true
        await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
          event: 'reviewer_context_reset',
          decisionNumber,
          attempt,
          droppedMessages,
          reason: 'context_length_exceeded',
        })
        response = undefined
        body = {}
        continue
      }
      if (!transientStatus(response.status)) throw lastError
    } catch (error) {
      lastError = error
      if (response && !transientStatus(response.status)) throw error
    }
    const exponential = Math.min(maxBackoffMs, baseBackoffMs * 2 ** Math.min(attempt - 1, 8))
    const headerDelay = response ? retryAfterMs(response) : undefined
    const backoffMs = Math.min(
      maxBackoffMs,
      Math.max(headerDelay ?? 0, exponential + Math.floor(Math.random() * exponential * 0.25)),
      Math.max(0, deadline - Date.now()),
    )
    if (backoffMs <= 0) break
    await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
      event: 'review_retry',
      decisionNumber,
      attempt,
      status: response?.status ?? null,
      backoffMs,
      error: String(redactUntrusted(lastError instanceof Error ? lastError.message : String(lastError))).slice(0, 1000),
    })
    await delay(backoffMs)
    response = undefined
    body = {}
  }
  if (!response?.ok) throw lastError ?? new Error('reviewer model deadline reached before a successful response')
  await writeJson(path.join(runDir, `reviewer-${String(decisionNumber).padStart(3, '0')}.response.json`), redactUntrusted(body))

  const text = outputText(body)
  const parsed = JSON.parse(text) as Partial<ReviewDecision>
  if ((parsed.decision !== 'approve' && parsed.decision !== 'reject') || typeof parsed.reason !== 'string' || !parsed.reason) {
    throw new Error(`invalid reviewer decision: ${text}`)
  }
  const decision = { decision: parsed.decision, reason: parsed.reason }
  state.history.push({ role: 'user', content: historyPrompt }, { role: 'assistant', content: JSON.stringify(decision) })
  const droppedMessages = compactReviewerHistory(state, maxHistoryMessages, maxHistoryCharacters)
  if (droppedMessages > 0) {
    await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
      event: 'reviewer_context_compacted',
      decisionNumber,
      droppedMessages,
      retainedMessages: state.history.length,
      retainedCharacters: historyCharacters(state),
    })
  }
  await appendJsonl(path.join(runDir, 'reviewer-process.jsonl'), {
    event: 'review_completed',
    decisionNumber,
    responseId: body.id ?? null,
    returnedModel: body.model ?? null,
    responseStatus: body.status ?? null,
    attempts: attempt,
    reasoningSummaries: reasoningSummaries(body),
    usage: body.usage ?? null,
  })
  return decision
}
