/**
 * A deterministic agent that needs no model.
 *
 * It performs the canonical long-horizon loop: attempt the objective, and when
 * the network boundary blocks it, propose the narrowest policy rule that would
 * allow it, then retry until the (host-side) reviewer approves it and
 * OpenShell enforces the new policy. It exists so the whole harness can be
 * exercised with zero credentials and so CI can prove the pipeline without an
 * LLM. It never parses the optional policy.local `/wait` long-poll; it just
 * retries, which keeps its behavior obvious.
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { Runtime, RuntimeContext, TurnRequest, TurnResult } from './types.js'

const run = promisify(execFile)

interface ScriptedState {
  phase: 'attempt' | 'propose' | 'poll' | 'hold'
  polls: number
  proposed: boolean
}

const state: ScriptedState = { phase: 'attempt', polls: 0, proposed: false }
const MAX_POLLS = 60

async function curl(context: RuntimeContext, args: string[]): Promise<{ stdout: string; exitCode: number }> {
  try {
    const { stdout } = await run('/usr/bin/curl', ['-sS', '-m', '30', ...args], { signal: context.signal, maxBuffer: 4_000_000 })
    return { stdout, exitCode: 0 }
  } catch (error) {
    const failure = error as { stdout?: string; code?: number }
    return { stdout: failure.stdout ?? '', exitCode: typeof failure.code === 'number' ? failure.code : 1 }
  }
}

function tool(context: RuntimeContext, name: string, input: string, output: string, exitCode: number): void {
  context.emit({ type: 'tool.call', epoch: context.epoch, turn: context.turn, name, input, output: output.slice(0, 4000), exitCode })
}

function say(context: RuntimeContext, text: string): void {
  context.emit({ type: 'message', epoch: context.epoch, turn: context.turn, text })
}

const reached = (stdout: string): boolean => /http=2\d\d$/.test(stdout.trim())
const sleep = (context: RuntimeContext, seconds: number): Promise<void> =>
  new Promise((resolve) => {
    const timer = setTimeout(resolve, seconds * 1000)
    context.signal.addEventListener('abort', () => { clearTimeout(timer); resolve() }, { once: true })
  })

export const scriptedRuntime: Runtime = {
  name: 'scripted',
  async setup(): Promise<void> {},

  async turn(context: RuntimeContext, _request: TurnRequest): Promise<TurnResult> {
    const task = context.config.scripted
    if (!task) return { ok: false, exitCode: 2, error: 'scripted runtime requires config.scripted' }
    const target = `http://${task.host}:${task.port}${task.path}`
    let toolCalls = 0

    switch (state.phase) {
      case 'attempt': {
        const result = await curl(context, ['-o', '/dev/null', '-w', 'http=%{http_code}', target]); toolCalls += 1
        tool(context, 'curl', `GET ${target}`, result.stdout, result.exitCode)
        if (reached(result.stdout)) { say(context, 'objective reached without a policy change'); state.phase = 'hold' }
        else { say(context, 'request blocked by policy; proposing the narrowest rule'); state.phase = 'propose' }
        break
      }
      case 'propose': {
        const proposal = {
          intent_summary: `Allow ${task.binary} to GET ${task.path} on ${task.host}:${task.port} for the assigned task.`,
          operations: [{ addRule: { ruleName: 'scripted_task_get', rule: {
            name: 'scripted_task_get',
            endpoints: [{ host: task.host, port: task.port, protocol: 'rest', enforcement: 'enforce', rules: [{ allow: { method: 'GET', path: task.path } }] }],
            binaries: [{ path: task.binary }],
          } } }],
        }
        const result = await curl(context, ['-X', 'POST', '-H', 'Content-Type: application/json', '--data-binary', JSON.stringify(proposal), 'http://policy.local/v1/proposals']); toolCalls += 1
        tool(context, 'curl', 'POST http://policy.local/v1/proposals', result.stdout, result.exitCode)
        let accepted: string[] = []
        let rejected: string[] = []
        try { const body = JSON.parse(result.stdout) as { accepted_chunk_ids?: string[]; rejection_reasons?: string[] }; accepted = body.accepted_chunk_ids ?? []; rejected = body.rejection_reasons ?? [] }
        catch { rejected = [result.stdout.slice(0, 200)] }
        context.emit({ type: 'proposal.submitted', epoch: context.epoch, turn: context.turn, chunkIds: accepted, rejected })
        if (accepted.length > 0) { state.proposed = true; state.phase = 'poll'; say(context, `proposal ${accepted[0]} submitted; waiting for it to be approved and enforced`) }
        else { say(context, `proposal was not accepted: ${rejected.join('; ')}`); state.phase = 'hold' }
        break
      }
      case 'poll': {
        await sleep(context, 3)
        const result = await curl(context, ['-o', '/dev/null', '-w', 'http=%{http_code}', target]); toolCalls += 1
        state.polls += 1
        tool(context, 'curl', `GET ${target}`, result.stdout, result.exitCode)
        if (reached(result.stdout)) { say(context, 'objective reached: policy approved and enforced'); state.phase = 'hold' }
        else if (state.polls >= MAX_POLLS) { say(context, 'objective still blocked after retrying; holding'); state.phase = 'hold' }
        else say(context, `still blocked (${result.stdout.trim()}); will retry`)
        break
      }
      case 'hold': { await sleep(context, task.idleSeconds); say(context, 'holding until the deadline'); break }
    }

    context.emit({ type: 'turn.completed', epoch: context.epoch, turn: context.turn, toolCalls })
    return { ok: true, threadId: 'scripted', exitCode: 0 }
  },
}
