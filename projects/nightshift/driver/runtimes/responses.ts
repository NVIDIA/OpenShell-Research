/**
 * A minimal agent runtime that speaks the OpenAI Responses API directly, with a
 * single shell tool. It exists to prove the harness is not tied to any one
 * agent framework: it is a second, independent runtime in ~120 lines, runnable
 * with only an API key and the base sandbox image (no Codex, no custom image).
 *
 * One driver "turn" is one model request plus the tool calls it asks for, up to
 * a small per-turn budget, after which the driver resumes the same server-side
 * thread. Thread continuity uses the Responses API `previous_response_id`, so
 * the thread's context grows every turn; when the last request's input tokens
 * reach the configured effective context budget, or the API reports the
 * context exhausted, the runtime asks the driver to rotate to a fresh thread.
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { Usage } from '../../src/events.js'
import type { Runtime, RuntimeContext, TurnRequest, TurnResult } from './types.js'

const run = promisify(execFile)
const MAX_TOOL_CALLS_PER_TURN = 12
const transientStatus = (status: number): boolean => status === 429 || status === 500 || status === 502 || status === 503 || status === 504
const isContextLengthExceeded = (status: number, body: ResponsesBody): boolean =>
  status === 400 && (((body.error?.code ?? body.error?.type ?? '') === 'context_length_exceeded') || /context (?:window|length)|input exceeds/i.test(body.error?.message ?? ''))
const isContentPolicy = (status: number, body: ResponsesBody): boolean =>
  status === 400 && /policy|refus|moderation|safety|disallowed/i.test(`${body.error?.code ?? ''} ${body.error?.type ?? ''} ${body.error?.message ?? ''}`)

const SYSTEM = [
  'You are an autonomous agent working inside an OpenShell sandbox on a long-running task.',
  'You have one tool, `shell`, which runs a command in the sandbox and returns its stdout, stderr, and exit code.',
  'Pursue the task persistently across turns. When the network boundary blocks something you need, do not give up: ask OpenShell for a narrower policy by POSTing a proposal to the local policy API (http://policy.local/v1/proposals), then continue once it is approved and enforced.',
  'Work in bounded turns: do one meaningful unit of work, submit at most one policy proposal per turn, then stop and let the harness resume you. Do not print or inspect credential values.',
].join('\n')

interface OutputItem {
  type?: string
  id?: string
  name?: string
  arguments?: string
  call_id?: string
  summary?: Array<{ text?: string }>
  content?: Array<{ type?: string; text?: string }>
  refusal?: string
}
interface ResponsesBody { id?: string; model?: string; status?: string; output?: OutputItem[]; usage?: Record<string, unknown>; error?: { type?: string; code?: string; message?: string } }

const SHELL_TOOL = {
  type: 'function', name: 'shell', strict: true,
  description: 'Run a shell command in the sandbox. Returns stdout, stderr, and exit code.',
  parameters: { type: 'object', additionalProperties: false, required: ['command'], properties: { command: { type: 'string', description: 'The command to run with /bin/bash -lc.' } } },
}

function usageFrom(record: Record<string, unknown>): Usage {
  const number = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
  const inDetails = (record.input_tokens_details ?? {}) as Record<string, unknown>
  const outDetails = (record.output_tokens_details ?? {}) as Record<string, unknown>
  return { inputTokens: number(record.input_tokens), cachedInputTokens: number(inDetails.cached_tokens), outputTokens: number(record.output_tokens), reasoningOutputTokens: number(outDetails.reasoning_tokens) }
}

async function shell(context: RuntimeContext, command: string): Promise<string> {
  try {
    const { stdout, stderr } = await run('/bin/bash', ['-lc', command], { signal: context.signal, maxBuffer: 4_000_000 })
    return JSON.stringify({ stdout: stdout.slice(0, 8000), stderr: stderr.slice(0, 2000), exitCode: 0 })
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string; code?: number }
    return JSON.stringify({ stdout: (failure.stdout ?? '').slice(0, 8000), stderr: (failure.stderr ?? '').slice(0, 2000), exitCode: typeof failure.code === 'number' ? failure.code : 1 })
  }
}

export const responsesRuntime: Runtime = {
  name: 'responses',
  async setup(context): Promise<void> {
    context.emit({ type: 'driver.runtime', runtime: 'responses', model: context.config.model.model })
  },

  async turn(context: RuntimeContext, request: TurnRequest): Promise<TurnResult> {
    const { model } = context.config
    const apiKey = process.env[model.apiKeyEnv]
    if (!apiKey) return { ok: false, exitCode: 2, error: `responses runtime requires ${model.apiKeyEnv}` }
    let previousId = request.kind === 'resume' ? request.threadId : undefined
    let input: unknown[] = request.kind === 'start'
      ? [{ role: 'developer', content: SYSTEM }, { role: 'user', content: request.prompt }]
      : [{ role: 'user', content: request.nudge }]
    let toolCalls = 0
    let usage: Usage | undefined
    const budgetTokens = model.contextWindow * model.effectiveContextPercent / 100
    const rotateIfOverBudget = (): string | undefined => (usage && usage.inputTokens >= budgetTokens ? 'context_budget' : undefined)

    while (true) {
      let response: Response
      try {
        response = await fetch(model.baseUrl, {
          method: 'POST', signal: context.signal,
          headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: model.model, input, tools: [SHELL_TOOL], max_output_tokens: 4000, store: true,
            // Some Responses-compatible gateways reject the reasoning field for models that do not take it; `none` omits it.
            ...(model.reasoning === 'none' ? {} : { reasoning: { effort: model.reasoning, summary: 'auto' } }),
            ...(previousId ? { previous_response_id: previousId } : {}),
          }),
        })
      } catch (error) {
        if (context.signal.aborted) return { ok: false, threadId: previousId, exitCode: null, timedOut: true, transient: true, error: 'request timed out' }
        return { ok: false, threadId: previousId, exitCode: 1, transient: true, error: error instanceof Error ? error.message : String(error) }
      }
      const body = (await response.json().catch(() => ({}))) as ResponsesBody
      if (!response.ok) {
        const error = `Responses HTTP ${response.status}: ${body.error?.message ?? 'unknown error'}`
        if (isContextLengthExceeded(response.status, body)) return { ok: false, threadId: previousId, exitCode: 1, rotate: 'context_length_exceeded', error }
        if (isContentPolicy(response.status, body)) return { ok: false, threadId: previousId, exitCode: 1, refusal: true, error }
        return { ok: false, threadId: previousId, exitCode: 1, transient: transientStatus(response.status), error }
      }
      previousId = body.id
      if (body.usage) usage = usageFrom(body.usage)

      const calls: OutputItem[] = []
      let refusedText = ''
      for (const item of body.output ?? []) {
        if (item.type === 'reasoning') { const text = (item.summary ?? []).map((s) => s.text ?? '').join('\n').trim(); if (text) context.emit({ type: 'reasoning', epoch: context.epoch, turn: context.turn, text: text.slice(0, 8000) }) }
        else if (item.type === 'message') { const text = (item.content ?? []).filter((c) => c.type === 'output_text').map((c) => c.text ?? '').join('\n').trim(); if (text) context.emit({ type: 'message', epoch: context.epoch, turn: context.turn, text: text.slice(0, 8000) }) }
        else if (item.type === 'refusal') { refusedText = item.refusal ?? 'model refused'; context.emit({ type: 'message', epoch: context.epoch, turn: context.turn, text: refusedText.slice(0, 8000) }) }
        else if (item.type === 'function_call' && item.name === 'shell') calls.push(item)
      }

      if (calls.length === 0) {
        if (refusedText) return { ok: false, threadId: previousId, exitCode: 1, refusal: true, error: refusedText.slice(0, 2000) }
        context.emit({ type: 'turn.completed', epoch: context.epoch, turn: context.turn, toolCalls, usage })
        return { ok: true, threadId: previousId, exitCode: 0, rotate: rotateIfOverBudget() }
      }

      const outputs: unknown[] = []
      for (const call of calls) {
        let command = ''
        try { command = (JSON.parse(call.arguments ?? '{}') as { command?: string }).command ?? '' } catch { command = '' }
        const result = await shell(context, command)
        toolCalls += 1
        context.emit({ type: 'tool.call', epoch: context.epoch, turn: context.turn, name: 'shell', input: command.slice(0, 4000), output: result.slice(0, 4000), exitCode: (JSON.parse(result) as { exitCode: number }).exitCode })
        outputs.push({ type: 'function_call_output', call_id: call.call_id, output: result })
        if (context.signal.aborted) break
      }
      if (toolCalls >= MAX_TOOL_CALLS_PER_TURN || context.signal.aborted) { context.emit({ type: 'turn.completed', epoch: context.epoch, turn: context.turn, toolCalls, usage }); return { ok: true, threadId: previousId, exitCode: 0, rotate: rotateIfOverBudget() } }
      input = outputs
    }
  },
}
