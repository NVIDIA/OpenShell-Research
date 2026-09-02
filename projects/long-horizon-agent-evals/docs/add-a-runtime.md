---
title: Add an agent runtime
description: Adapt an agent framework to the driver's one-turn contract and the common event vocabulary.
agent_markdown: true
---

# Add an agent runtime

An agent runtime is the part of the system that knows how to run a particular
agent: OpenAI Codex, a direct Responses API loop, a scripted stand-in, or
something else. It runs inside the sandbox as part of the bundled driver. The
harness on the host never learns which agent ran; it reads the common event
vocabulary.

A runtime is one file under `driver/runtimes/` plus one line in
`driver/runtimes/index.ts`. Four ship today:

| Runtime | What it is | Needs |
| --- | --- | --- |
| `scripted` | A deterministic agent with no model that attempts a GET, proposes the narrowest rule when blocked, and retries. | Nothing. Used by `hello-canary` and CI. |
| `responses` | A minimal agent speaking the OpenAI Responses API directly with a single `shell` tool. | `OPENAI_API_KEY`. Base sandbox image. |
| `codex` | OpenAI Codex CLI under `codex exec --json`, mapped onto the common events. | `OPENAI_API_KEY` and the pinned Codex image (`npm run image:build`), selected automatically. The base image's older Codex has an incompatible model-catalog schema. |
| `claude-code` | Anthropic Claude Code CLI under `claude -p --output-format stream-json`, mapped onto the common events. | A key for an Anthropic-format endpoint: `api.anthropic.com`, or any gateway that serves `/v1/messages`, set through `LAB_MODEL_BASE_URL` and `LAB_MODEL_API_KEY`. Runs on the base image; pin a version for evidence runs. |

A runtime whose CLI is not in the base image, or whose version must be pinned,
defines a Dockerfile under `images/<runtime>/` built on the base image and
names it in `runtimeDefaultImages` (`src/registry.ts`). The CLI selects that
image automatically; `--image` overrides it. Codex does this; `npm run
image:build` builds `images/codex`.

The `scripted` and `responses` runtimes isolate a model behind a minimal loop;
they are the reproducible baseline and the second implementation that keeps the
Runtime contract honest. The `codex` and `claude-code` runtimes are full agent
harnesses that bring their own tools, planning, and context compaction, so they
are the realistic choice for a long-horizon result. Prefer a full harness for a
headline experiment and a raw-model runtime for controlled model comparison.

A model-driven runtime declares its API family once in
`runtimeModelProfiles` (`src/registry.ts`): the environment variable holding
its key, its default endpoint and model, how the key is sent (bearer token or
header), and the binaries allowed to reach the endpoint. The CLI reads that to
add the model egress rule and to deliver the key as an OpenShell provider
credential, so the agent holds an `openshell:` placeholder and never the real
key. The binaries mirror OpenShell's own provider profile for the same harness.

## The contract

```ts
export interface Runtime {
  name: string
  setup(context: Omit<RuntimeContext, 'epoch' | 'turn'>): Promise<void>
  turn(context: RuntimeContext, request: TurnRequest): Promise<TurnResult>
}

export type TurnRequest =
  | { kind: 'start'; prompt: string }
  | { kind: 'resume'; threadId: string; nudge: string }

export interface TurnResult {
  ok: boolean
  threadId?: string
  exitCode: number | null
  transient?: boolean
  timedOut?: boolean
  error?: string
  rotate?: string
  refusal?: boolean
}
```

`setup` runs once per sandbox, for example to write a CLI configuration file.
`turn` runs one bounded unit of work: start a new thread with the full prompt,
or resume an existing thread with a short nudge. It returns whether the turn
completed, the identifier needed to resume the thread next time, and how to
interpret a failure.

`RuntimeContext` carries the driver configuration (including the model
endpoint, model name, reasoning level, and the environment variable holding
the API key), a scratch directory, the current epoch and turn numbers, an
`emit` function for events, and an `AbortSignal` that fires at the per-turn
request timeout or the run deadline. Honor the signal: pass it to child
processes and `fetch`.

## What the driver does around a turn

The runtime does one turn. The driver owns everything else, so a runtime never
implements retries, backoff, or context recovery:

- **Timeouts.** Each turn gets `min(turnTimeoutSeconds, time remaining)`. The
  default of 180 seconds is hang protection; `--turn-timeout` raises it for a
  workload whose turns legitimately run longer.
- **Transient failures.** A result with `ok: false` and `transient: true`
  triggers exponential backoff with jitter, capped, and a retry of the same
  thread. Every wait is recorded as `lab.backoff`.
- **Rotation.** The driver starts a fresh thread with a bounded checkpoint of
  recent activity when any of these hold: the runtime returns `rotate` with a
  reason; a configured number of consecutive transient failures occurred; the
  lull detector sees a window of idle, repetitive turns; an optional
  successful-turn budget is reached; the runtime returns `refusal`; or a
  non-transient exit still has recoverable progress. Rotation is capped, and
  each one is recorded as `lab.rotation` with the exact checkpoint text.
- **Handoff checkpoint.** The driver accumulates the `tool.call`, `message`,
  and `reasoning` events a runtime emits, deduplicates repeated prose, and
  trims to entry and character budgets, preferring to keep command executions.
  The checkpoint is appended to the original prompt when a new thread starts.
- **Refusals.** A result with `refusal: true` records `lab.refusal`, strips the
  refusal and any "out of options" defeatism from the checkpoint, and rotates
  to a fresh thread reseeded from factual progress only. It is never counted as
  backoff, so a recoverable refusal does not invalidate the run.
- **Fatal failures.** A result with `ok: false` that is neither transient,
  rotatable, nor a refusal rotates to a fresh thread when it still has
  recoverable progress and the rotation budget remains; otherwise it ends the
  run with `lab.error` and the given exit code.

Use `rotate` when the thread cannot usefully continue but a fresh one could.
The `responses` runtime returns `rotate: 'context_budget'` when the last
request's input tokens reach the configured effective context window, and
`rotate: 'context_length_exceeded'` when the API reports the context
exhausted. Codex compacts its own context, so its adapter never asks.

## The event vocabulary

Emit these through `context.emit`. The driver stamps a timestamp, forwards the
line to the host, and uses the same events to build handoff checkpoints and
detect lulls, so a runtime that emits nothing gets no recovery.

| Event | When |
| --- | --- |
| `turn.completed` | Once per successful turn, with `toolCalls` and optional `usage`. The host counts these; a run with none is invalid. |
| `tool.call` | Each command or tool invocation, with clipped `input`, `output`, and `exitCode`. |
| `message` | Text the agent addressed to the user. |
| `reasoning` | Reasoning summaries, when the model exposes them. |
| `proposal.submitted` | When the runtime itself submits a policy proposal and knows the accepted chunk ids. Model-driven agents usually submit with `curl`, which shows up as `tool.call`. |
| `lab.runtime` | Once from `setup`, naming the runtime, version, and model. |
| `lab.error` | A runtime-level error message worth preserving. |

The driver emits `turn.started`, `lab.backoff`, and `lab.rotation` itself.
Unparseable stdout lines are preserved by the host as `lab.unparsed`.

## Bundling constraints

The host bundles `driver/driver.ts` and everything it imports into one ES
module with esbuild and streams it into the sandbox, where it runs with the
image's `node`. There is no `npm install` in the sandbox. A runtime may import
Node built-ins, `driver/*`, and `src/events.ts`, and nothing else. Anything the
agent framework itself needs must already be in the sandbox image.

The host adds an egress rule for the configured model endpoint, allowing the
profile's binaries to reach that host and port, and attaches a provider that
sets the configured variable to a placeholder the network boundary substitutes.
A runtime that reaches the model through a binary not in its profile must add
it there.

## Walkthrough: responses

`driver/runtimes/responses.ts` is about 120 lines and is the reference for a
new adapter.

- `setup` emits `lab.runtime`.
- `turn` sends one Responses API request. For `start` the input is a developer
  message and the prompt; for `resume` it is the nudge with
  `previous_response_id` set to the thread id. It declares one strict
  `shell` function tool.
- Each `function_call` in the output runs through `/bin/bash -lc` with the
  turn's signal, is emitted as `tool.call`, and is returned as a
  `function_call_output`. The loop continues until the model stops calling
  tools or the per-turn budget of 12 calls is reached, then emits
  `turn.completed` and returns the latest response id as `threadId`.
- HTTP 429 and 5xx are `transient`. A context-length error requests rotation.
  Any other error is fatal for the run.

`driver/runtimes/claude-code.ts` is the same shape for a full harness: one
`claude -p` process per turn, stream-json parsed into the common events,
`--resume <session-id>` for continuity. Because Claude Code compacts its own
context, it never requests rotation on a context budget, exactly as Codex does
not.

## Register it

```ts
export const runtimes: Record<string, Runtime> = {
  scripted: scriptedRuntime,
  responses: responsesRuntime,
  codex: codexRuntime,
  mine: myRuntime,
}
```

The host re-exports this list, so `npm run doctor` shows the new name and
`--runtime mine` is validated before a sandbox is created.
