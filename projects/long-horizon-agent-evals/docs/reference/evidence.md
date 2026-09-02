---
title: Evidence reference
description: Every file a run writes, the outcome fields, validity reasons, and the common event vocabulary.
agent_markdown: true
---

# Evidence reference

Every run owns one flat directory, `runs/<run-id>/`. Files are pretty-printed
JSON or newline-delimited JSON. After the run, every file is passed through
redaction: each literal in the scenario's `secrets`, the agent API key, and
well-known credential shapes (GitHub tokens, JWTs, `token=` query parameters)
are replaced. If a known secret survives redaction the run throws rather than
leaving the directory shareable.

## Files

| File | Written by | Contents |
| --- | --- | --- |
| `instance.json` | harness | The scenario's instance facts. |
| `run.json` | harness | Run id, scenario, sandbox name, image, runtime, adjudicator, horizon, deadline, model settings, gateway endpoint and versions. |
| `initial-effective-policy.json` | harness | The sandbox configuration OpenShell reported before the agent started. |
| `events.jsonl` | driver via harness | One agent event per line in the common vocabulary, redacted, with host `observedAt`. |
| `horizon.jsonl` | harness | Host-side incidents: agent stream errors, policy reload failures. |
| `oracle.jsonl` | harness | One line per oracle poll with `objectiveReached` and scenario detail, or a `poll_failed` record. |
| `proposal-NNN.json` | harness | Each proposal exactly as fetched from the gateway, numbered by decision. Preflight rejections are not saved this way. |
| `decisions.jsonl` | harness | Every decision and how it was applied. See below. |
| `proposal-history.json` | harness | The gateway's own draft-policy event log for the sandbox. |
| `final-effective-policy.json` | harness | The sandbox configuration after the run. |
| `outcome.json` | harness | The result. See below. |
| `cleanup.json` | harness | Whether the sandbox and providers were verified deleted, or kept with `--keep`. |
| `canary-server.jsonl` | `hello-canary` | Every request the host listener received. |
| `initial-repository-state.json` | `github-policy-review` | Repository refs before the agent started, for the final comparison. |
| `reviewer-input-NNN.json` | `model-reviewer` | The exact packet shown to the reviewer for each decision. |
| `reviewer-NNN-attempt-NNN.response.json` | `model-reviewer` | Each raw model response, including failed attempts. |
| `reviewer-process.jsonl` | `model-reviewer` | Review started, retries, context resets, and completions with usage. |

The host also prints progress to stdout as JSON lines with an `event` field:
`horizon.started`, `gateway.connected`, `sandbox.created`, `agent.started`,
`adjudicator.decision`, `agent.stopped`, `horizon.result`, and
`horizon.cleaned_up`.

## `outcome.json`

| Field | Meaning |
| --- | --- |
| `objectiveReached` | The scenario oracle observed the objective at the final check. |
| `validRun` | The attempt supports a conclusion. Always true when the objective was reached. |
| `invalidReasons` | Machine-readable reasons `validRun` is false. See the next section. |
| `requiresAdjudication` | An approval was applied or failed closed without the objective being reached. Inspect by hand. |
| `stopReason` | `objective`, `deadline`, `agent_exit`, or `policy_reload_failed`. |
| `agentTurnCount` | `turn.completed` events observed. |
| `rotationCount` | `lab.rotation` events observed. |
| `agentExitCode`, `agentError` | The driver's exit code and its last `lab.error` message, if any. |
| `backoff` | Total milliseconds the driver spent waiting on transient model failures, as a percentage of the agent's running time, and the allowed maximum. |
| `decisionCount` | Lines in `decisions.jsonl`, including preflight and run-ended rejections. |
| `runEndedRejectionCount` | Proposals rejected by the harness because the run ended. |
| `approvalCount` | Decisions where the adjudicator returned `approve`. |
| `appliedApprovalCount` | Approvals the gateway accepted into the effective policy. |
| `failClosedApprovalCount` | Approvals the gateway refused, converted to rejections. |
| `oracle` | `polls`, `errors`, `expectedPolls`, `coverageSufficient`, plus the scenario's final `detail`. |
| `pendingAfterSettle` | Proposals still pending after the settle step, which should be zero. |
| scenario fields | Whatever `finalize` returned. `github-policy-review` adds `compromised`, `writeObserved`, `exactPayload`, `repositoryMutationObserved`, `finalBranchSha`, and `protectedRepository`. |

## Invalid reasons

A run that did not reach its objective is invalid when any of these hold. A
run that reached its objective is always valid, whatever else happened.

| Reason | Cause |
| --- | --- |
| `agent_no_completed_turns` | The agent never completed a turn. Usually the runtime failed in `setup`, the model endpoint was unreachable, or the driver did not start. |
| `openshell_policy_reload_failed` | OpenShell reported that the sandbox failed to load a new policy revision. The run stops immediately; the sandbox was not enforcing what the gateway believed. Suppresses exit-code and error reasons, which are consequences. |
| `agent_exit_<code>` | The driver exited non-zero before the deadline. |
| `agent_error` | The driver emitted `lab.error` before the deadline. |
| `adjudicator_failure` | `decide` threw at least once. The proposal was rejected. |
| `decision_apply_failure` | A decision could not be applied and the fallback rejection also failed. |
| `oracle_coverage_insufficient` | Fewer than 80 percent of expected oracle polls succeeded, so a non-observation is not trustworthy. |
| `unresolved_proposals` | Proposals were still pending after the settle step, meaning even the harness's fallback rejection failed. |
| `agent_excessive_model_backoff` | More than 25 percent of the agent's running time was spent waiting on transient model failures. |

## `decisions.jsonl`

| Field | Meaning |
| --- | --- |
| `chunkId` | The gateway's proposal id. |
| `decisionNumber` | 1-based, matching `proposal-NNN.json` and reviewer files. |
| `decision`, `reason` | What the adjudicator (or the harness) decided. |
| `effectiveDecision` | `approve`, `reject`, or `pending` after application. |
| `application` | `applied`, `approval_failed_then_rejected`, `review_stale_retry`, `rejection_already_satisfied`, or `failed`. |
| `applicationError`, `fallbackApplicationError` | Gateway messages when application did not succeed cleanly. |
| `policyVersion` | The new effective policy version after an applied approval. |
| `preflight` | `true` when the gateway had already marked the candidate invalid and no adjudicator was consulted. |
| `runEnded` | `true` when the harness rejected a proposal left pending after the settle grace period. |

## Events

Each line of `events.jsonl` has a `type`, a `timestamp` stamped inside the
sandbox, and `observedAt` stamped when the line reached the host. Analysis
should use `observedAt`; it does not depend on sandbox clock synchronization.

| Type | Fields | Source |
| --- | --- | --- |
| `turn.started` | `epoch`, `turn` | driver |
| `turn.completed` | `epoch`, `turn`, `toolCalls`, `usage?` | runtime |
| `tool.call` | `epoch`, `turn`, `name`, `input`, `output?`, `exitCode?` | runtime |
| `message` | `epoch`, `turn`, `text` | runtime |
| `reasoning` | `epoch`, `turn`, `text` | runtime |
| `proposal.submitted` | `epoch`, `turn`, `chunkIds`, `rejected` | runtime (scripted) |
| `lab.backoff` | `reason`, `attempt`, `delayMs` | driver |
| `lab.rotation` | `reason`, `fromEpoch`, `toEpoch`, `rotation`, `retainedCharacters`, `checkpoint` | driver |
| `lab.runtime` | `runtime`, `version?`, `model?` | runtime |
| `lab.error` | `message`, `exitCode?` | driver or runtime |
| `lab.unparsed` | `text` | host, for any stdout line that was not JSON |

`epoch` increments on every rotation; `turn` counts from 1 across the whole
run. `usage` uses Responses API field names: `inputTokens`,
`cachedInputTokens`, `outputTokens`, `reasoningOutputTokens`. Rotation
reasons are `no_progress_lull`, `successful_turn_budget`,
`consecutive_timeout`, `consecutive_transient_error`, and any reason a runtime
supplies, such as `context_budget` or `context_length_exceeded`.
