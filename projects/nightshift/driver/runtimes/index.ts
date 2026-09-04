/**
 * The explicit runtime registry. Adding an agent runtime means writing one
 * adapter file and adding one line here. No filesystem discovery.
 */
import { claudeCodeRuntime } from './claude-code.js'
import { codexRuntime } from './codex.js'
import { responsesRuntime } from './responses.js'
import { scriptedRuntime } from './scripted.js'
import type { Runtime } from './types.js'

export const runtimes: Record<string, Runtime> = {
  scripted: scriptedRuntime,
  responses: responsesRuntime,
  codex: codexRuntime,
  'claude-code': claudeCodeRuntime,
}

export const runtimeNames = Object.keys(runtimes)

export function selectRuntime(name: string): Runtime {
  const runtime = runtimes[name]
  if (!runtime) throw new Error(`unknown runtime: ${name} (have: ${runtimeNames.join(', ')})`)
  return runtime
}
