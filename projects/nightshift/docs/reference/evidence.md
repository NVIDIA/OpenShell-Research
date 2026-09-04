---
title: Evidence reference
description: Every file a run writes, the ledger, the outcome fields, validity reasons, and the common event vocabulary.
agent_markdown: true
---

# Evidence reference

Every run owns one flat directory, `runs/<run-id>/`. Files are pretty-printed
JSON, newline-delimited JSON, or the tab-separated ledger. After the run, every
file is passed through redaction: each literal in the experiment's `secrets`,
every provider credential, the agent API key, and well-known credential shapes
(GitHub tokens, JWTs, `token=` query parameters) are replaced. If a known
secret survives redaction the run throws rather than leaving the directory
shareable.

## Files

| File | Written by | Contents |
| --- | --- | --- |
| `results.tsv` | harness | The ledger: one row per distinct trial. See below. |
| `scores.jsonl` | harness | Every score poll: `value`, `done`, `trial`, `notes`, scorer detail, or a `poll_failed` record. |
| `instance.json` | harness | The experiment's instance facts. |
| `run.json` | harness | Run id, experiment, profile and its environment, sandbox name, image, runtime, reviewer, horizon and deadline (null when running until stopped), scorer, working directory, model settings, gateway endpoint and versions. |
| `driver-config.json` | harness | The exact configuration the in-sandbox driver ran with: prompt, resume nudge, per-turn timeout, working directory, backoff, rotation, handoff, and lull tuning. |
| `initial-effective-policy.json` | harness | The sandbox configuration OpenShell reported before the agent started. |
| `events.jsonl` | driver via harness | One agent event per line in the common vocabulary, redacted, with host `observedAt`. |
| `horizon.jsonl` | harness | Host-side incidents: the agent's exec stream lost before the deadline, policy reload failures. |
| `proposal-NNN.json` | harness | Each proposal exactly as fetched from the gateway, numbered by decision. |
| `decisions.jsonl` | harness | Every decision and how it was applied. See below. |
| `proposal-history.json` | harness | The gateway's own draft-policy event log for the sandbox. |
| `final-effective-policy.json` | harness | The sandbox configuration after the run. |
| `outcome.json` | harness | The result. See below. |
| `cleanup.json` | harness | Whether the sandbox and providers were verified deleted, or kept with `--keep`. |
| `canary-server.jsonl` | `hello-canary` | Every request the host listener received. |
| `initial-repository-state.json` | `github-policy-review` | Repository refs before the agent started. |
| `reviewer-input-NNN.json` | `model-reviewer` | The exact packet shown to the reviewer for each decision. |
| `reviewer-NNN-attempt-NNN.response.json` | `model-reviewer` | Each raw model response, including failed attempts. |
| `reviewer-process.jsonl` | `model-reviewer` | Review started, retries, context resets, and completions with usage. |

The host also prints progress to stdout as JSON lines with an `event` field:
`horizon.started`, `gateway.connected`, `sandbox.created`, `workdir.uploaded`,
`agent.started`, `score.recorded`, `reviewer.decision`, `agent.stopped`,
`horizon.result`, and `horizon.cleaned_up`.

## `results.tsv`

```text
time	turn	commit	value	done	description
```

| Column | Meaning |
| --- | --- |
| `time` | Host time the score was recorded. |
| `turn` | The agent's turn number when the score was taken, counted from `turn.started` events. |
| `commit` | Short hash at the head of `/sandbox/work` when the experiment has a `workdir/`; empty otherwise. |
| `value` | The score. |
| `done` | Whether the scorer reported the task complete. |
| `description` | The head commit's message, or the scorer's notes when there is no working directory. |

A row is appended when the scorer's `trial` id changes and the value is not
null. The agent cannot write this file; it lives on the host.

## `outcome.json`

| Field | Meaning |
| --- | --- |
| `done` | The scorer reported the task complete at the final check. |
| `validRun` | The attempt supports a conclusion. Always true when `done`. |
| `invalidReasons` | Machine-readable reasons `validRun` is false. See the next section. |
| `requiresReview` | An approval was applied or failed closed and the scorer never reported done. Inspect what the approval enabled. |
| `score` | `direction`, `best`, `last`, `trials` (ledger rows), `polls`, `errors`, `expectedPolls`, `coverageSufficient`, plus the scorer's final `detail`. |
| `stopReason` | `done`, `deadline`, `stopped` (Ctrl-C), `agent_exit`, `agent_stream_lost`, or `policy_reload_failed`. |
| `agentTurnCount` | `turn.completed` events observed. A turn cut off by the deadline does not complete; a full harness that keeps working inside one turn all run reports 0 here and its activity in `toolCallCount`. |
| `agentTurnsStarted` | `turn.started` events observed. |
| `toolCallCount` | `tool.call` events observed. |
| `usage` | Token usage summed over completed turns, in the Responses API field names. Zero when no turn completed or the runtime reports none. |
| `rotationCount` | `driver.rotation` events observed. |
| `refusalCount` | `driver.refusal` events observed: a model refusal that triggered a fresh-thread restart with the poisoned prose stripped. |
| `agentExitCode`, `agentError` | The driver's exit code and its last `driver.error` message, if any. |
| `backoff` | Total milliseconds the driver spent waiting on transient model failures, as a percentage of the agent's running time, and the allowed maximum. |
| `decisionCount` | Decisions in `decisions.jsonl`, including preflight and run-ended rejections, excluding stale-token retries. |
| `staleRetryCount` | Reviews that could not be applied because the proposal changed underneath the reviewer and had to be reviewed again. |
| `runEndedRejectionCount` | Proposals rejected by the harness because the run ended. |
| `proposalsByOrigin` | Distinct proposals decided, split into `agentAuthored` and `mechanistic` (generated by OpenShell's mechanistic mapper from denied connections). |
| `originReclassified` | Decisions whose recorded origin changed once the agent's submission event arrived after the decision. |
| `approvalCount` | Decisions where the reviewer returned `approve`. |
| `appliedApprovalCount` | Approvals the gateway accepted into the effective policy. |
| `appliedApprovalsByOrigin` | The same, split by proposal origin. |
| `failClosedApprovalCount` | Approvals the gateway refused, converted to rejections. |
| `pendingAfterSettle` | Proposals still pending after the settle step, which should be zero. |
| experiment fields | Whatever `finalize` returned. `github-policy-review` adds `compromised`, `writeObserved`, `exactPayload`, `repositoryMutationObserved`, `finalBranchSha`, and `protectedRepository`. |

## Invalid reasons

A run that never reached `done` is invalid when any of these hold. A run that
reached `done` is always valid, whatever else happened.

| Reason | Cause |
| --- | --- |
| `agent_no_activity` | The agent never completed a turn and never made a tool call. Usually the runtime failed in `setup`, the model endpoint was unreachable, or the driver did not start. |
| `agent_stream_lost` | The host lost the agent's exec stream before the deadline for a reason other than its own stop. |
| `openshell_policy_reload_failed` | OpenShell reported that the sandbox failed to load a new policy revision. The run stops immediately. Suppresses exit-code and error reasons, which are consequences. |
| `agent_exit_<code>` | The driver exited non-zero before the deadline. |
| `agent_error` | The driver emitted `driver.error` before the deadline. |
| `reviewer_failure` | `decide` threw at least once. The proposal was rejected. |
| `decision_apply_failure` | A decision could not be applied and the fallback rejection also failed. |
| `score_coverage_insufficient` | Fewer than 80 percent of expected score polls succeeded, so the ledger may have missed trials. |
| `unresolved_proposals` | Proposals were still pending after the settle step. |
| `agent_excessive_model_backoff` | More than 25 percent of the agent's running time was spent waiting on transient model failures. |

## `decisions.jsonl`

| Field | Meaning |
| --- | --- |
| `chunkId` | The gateway's proposal id. |
| `ruleName`, `binary` | The proposed rule's name and the binary whose denied connection or submission produced it. |
| `origin`, `originSource` | `agent_authored` when an in-sandbox agent submitted the proposal through policy.local; `mechanistic` when OpenShell's mechanistic mapper generated it from denied connections. `originSource` says how the host knew: `submission` (the chunk id appeared in the agent's own events), `rationale_template`, or `default`. |
| `decisionNumber` | 1-based, matching `proposal-NNN.json` and reviewer files. |
| `decision`, `reason` | What the reviewer (or the harness) decided. |
| `effectiveDecision` | `approve`, `reject`, or `pending` after application. |
| `application` | `applied`, `approval_failed_then_rejected`, `review_stale_retry`, `rejection_already_satisfied`, or `failed`. |
| `applicationError`, `fallbackApplicationError` | Gateway messages when application did not succeed cleanly. |
| `policyVersion` | The new effective policy version after an applied approval. |
| `preflight` | `true` when the gateway had already marked the candidate invalid and no reviewer was consulted. |
| `runEnded` | `true` when the harness rejected a proposal left pending after the settle grace period. |

## Events

Each line of `events.jsonl` has a `type`, a `timestamp` stamped inside the
sandbox, and `observedAt` stamped when the line reached the host. Analysis
should use `observedAt`.

| Type | Fields | Source |
| --- | --- | --- |
| `turn.started` | `epoch`, `turn` | driver |
| `turn.completed` | `epoch`, `turn`, `toolCalls`, `usage?` | runtime |
| `tool.call` | `epoch`, `turn`, `name`, `input`, `output?`, `exitCode?` | runtime |
| `message` | `epoch`, `turn`, `text` | runtime |
| `reasoning` | `epoch`, `turn`, `text` | runtime |
| `proposal.submitted` | `epoch`, `turn`, `chunkIds`, `rejected` | runtime (scripted) |
| `driver.backoff` | `reason`, `attempt`, `delayMs` | driver |
| `driver.rotation` | `reason`, `fromEpoch`, `toEpoch`, `rotation`, `retainedCharacters`, `checkpoint` | driver |
| `driver.runtime` | `runtime`, `version?`, `model?` | runtime |
| `driver.error` | `message`, `exitCode?` | driver or runtime |
| `driver.refusal` | `epoch`, `turn`, `message` | driver, when a runtime reports a refusal |
| `host.unparsed` | `text` | host, for any stdout line that was not JSON |

`epoch` increments on every rotation; `turn` counts from 1 across the whole
run. Rotation reasons are `no_progress_lull`, `successful_turn_budget`,
`consecutive_timeout`, `consecutive_transient_error`, `runtime_exit_<code>`,
`model_refusal`, and any reason a runtime supplies, such as `context_budget` or
`context_length_exceeded`.
