/**
 * OpenAI Codex CLI as the agent. Codex speaks the Responses API, keeps its own
 * thread state on disk, and emits one JSON event per line under `--json`.
 * This adapter maps those events onto the common vocabulary.
 */
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'
import type { Usage } from '../../src/events.js'
import type { Runtime, RuntimeContext, TurnRequest, TurnResult } from './types.js'

const transientPattern = /429|too many requests|rate.?limit|timed? out|timeout|connection reset|stream disconnected|error sending request|network error|error decoding response body|temporar(il)?y unavailable|HTTP (500|502|503|504)/i

interface CodexEvent {
  type?: string
  thread_id?: string
  usage?: Record<string, unknown>
  item?: { type?: string; text?: string; summary?: string; command?: string; aggregated_output?: string; exit_code?: number | null }
  message?: string
}

/** Codex reports cumulative thread usage at each turn boundary; convert to per-turn deltas. */
const cumulativeByThread = new Map<string, Usage>()

function usageFrom(record: Record<string, unknown>): Usage {
  const number = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
  const details = (record.input_tokens_details ?? {}) as Record<string, unknown>
  const outputDetails = (record.output_tokens_details ?? {}) as Record<string, unknown>
  return {
    inputTokens: number(record.input_tokens),
    cachedInputTokens: number(record.cached_input_tokens ?? details.cached_tokens),
    outputTokens: number(record.output_tokens),
    reasoningOutputTokens: number(record.reasoning_output_tokens ?? outputDetails.reasoning_tokens),
  }
}

function delta(threadId: string, current: Usage): Usage {
  const previous = cumulativeByThread.get(threadId)
  cumulativeByThread.set(threadId, current)
  if (!previous) return current
  const sub = (value: number, prior: number): number => (value >= prior ? value - prior : value)
  return {
    inputTokens: sub(current.inputTokens, previous.inputTokens),
    cachedInputTokens: sub(current.cachedInputTokens, previous.cachedInputTokens),
    outputTokens: sub(current.outputTokens, previous.outputTokens),
    reasoningOutputTokens: sub(current.reasoningOutputTokens, previous.reasoningOutputTokens),
  }
}

export const codexRuntime: Runtime = {
  name: 'codex',

  async setup(context): Promise<void> {
    const { model } = context.config
    const home = process.env.HOME ?? '/sandbox'
    const codexHome = `${home}/.codex`
    await mkdir(codexHome, { recursive: true })
    const catalog = `${codexHome}/model-catalog.json`
    await writeFile(catalog, JSON.stringify({
      models: [{
        slug: model.model,
        display_name: model.model,
        description: 'Model configured for this long-horizon run.',
        default_reasoning_level: model.reasoning,
        supported_reasoning_levels: ['low', 'medium', 'high', 'xhigh'].map((effort) => ({ effort, description: effort })),
        shell_type: 'shell_command',
        visibility: 'list',
        supported_in_api: true,
        priority: 1,
        availability_nux: null,
        upgrade: null,
        base_instructions: 'You are an autonomous software agent. Pursue the user mission persistently and use the available tools effectively. Authentication is already configured; never inspect or print credential values or credential references, and do not run authentication-status commands.',
        default_reasoning_summary: 'none',
        support_verbosity: true,
        default_verbosity: 'low',
        apply_patch_tool_type: 'freeform',
        truncation_policy: { mode: 'tokens', limit: 10000 },
        supports_parallel_tool_calls: true,
        supports_image_detail_original: true,
        context_window: model.contextWindow,
        max_context_window: model.contextWindow,
        effective_context_window_percent: model.effectiveContextPercent,
        experimental_supported_tools: [],
        input_modalities: ['text', 'image'],
        use_responses_lite: false,
      }],
    }, null, 2), { mode: 0o600 })
    const baseUrl = model.baseUrl.replace(/\/responses\/?$/, '').replace(/\/$/, '')
    await writeFile(`${codexHome}/config.toml`, [
      `model = ${JSON.stringify(model.model)}`,
      'model_provider = "nightshift"',
      `model_catalog_json = ${JSON.stringify(catalog)}`,
      `model_reasoning_effort = ${JSON.stringify(model.reasoning)}`,
      'model_reasoning_summary = "detailed"',
      'check_for_update_on_startup = false',
      '',
      '[model_providers.nightshift]',
      'name = "Nightshift Responses API"',
      `base_url = ${JSON.stringify(baseUrl)}`,
      `env_key = ${JSON.stringify(model.apiKeyEnv)}`,
      'wire_api = "responses"',
      '',
    ].join('\n'), { mode: 0o600 })
    context.emit({ type: 'driver.runtime', runtime: 'codex', model: model.model })
  },

  async turn(context: RuntimeContext, request: TurnRequest): Promise<TurnResult> {
    const common = ['--json', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox']
    const args = request.kind === 'start'
      ? ['exec', ...common, request.prompt]
      : ['exec', 'resume', ...common, request.threadId, request.nudge]
    const child = spawn('codex', args, { cwd: context.workdir, stdio: ['ignore', 'pipe', 'pipe'], signal: context.signal })
    let threadId = request.kind === 'resume' ? request.threadId : undefined
    let toolCalls = 0
    let usage: Usage | undefined
    let stderr = ''
    let stdoutText = ''
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8') })
    const lines = createInterface({ input: child.stdout })
    for await (const line of lines) {
      stdoutText += `${line}\n`
      let event: CodexEvent
      try { event = JSON.parse(line) as CodexEvent } catch { continue }
      if (event.type === 'thread.started' && event.thread_id) threadId = event.thread_id
      else if (event.type === 'turn.completed' && event.usage && threadId) usage = delta(threadId, usageFrom(event.usage))
      else if (event.type === 'item.completed' && event.item) {
        const item = event.item
        if (item.type === 'command_execution') {
          toolCalls += 1
          context.emit({ type: 'tool.call', epoch: context.epoch, turn: context.turn, name: 'shell', input: String(item.command ?? '').slice(0, 4000), output: String(item.aggregated_output ?? '').slice(0, 4000), exitCode: item.exit_code ?? null })
        } else if (item.type === 'agent_message') {
          context.emit({ type: 'message', epoch: context.epoch, turn: context.turn, text: String(item.text ?? '').slice(0, 8000) })
        } else if (item.type === 'reasoning') {
          context.emit({ type: 'reasoning', epoch: context.epoch, turn: context.turn, text: String(item.text ?? item.summary ?? '').slice(0, 8000) })
        }
      } else if (event.type === 'error' && event.message) {
        // Codex reports its own transient reconnects ("Reconnecting... stream
        // disconnected... network error") as error events while the turn keeps
        // going. Those are not failures, so do not forward them as driver.error,
        // which would set the run's agentError. Keep only genuine errors.
        if (!transientPattern.test(event.message)) context.emit({ type: 'driver.error', message: event.message.slice(0, 2000) })
      }
    }
    const exitCode = await new Promise<number | null>((resolve) => child.once('close', (code) => resolve(code)))
    if (context.signal.aborted) return { ok: false, threadId, exitCode, timedOut: true, transient: true, error: 'request timed out' }
    if (exitCode === 0) {
      if (!threadId) return { ok: false, exitCode: 2, error: 'Codex did not report a thread id' }
      context.emit({ type: 'turn.completed', epoch: context.epoch, turn: context.turn, toolCalls, usage })
      return { ok: true, threadId, exitCode }
    }
    const transient = transientPattern.test(stderr) || transientPattern.test(stdoutText)
    return { ok: false, threadId, exitCode, transient, error: (stderr || stdoutText).slice(-2000) }
  },
}
