---
title: Configuration reference
description: Command-line verbs, environment variables, scenario settings, and driver tuning defaults.
agent_markdown: true
---

# Configuration reference

## Command line

All commands run from `projects/long-horizon-agent-evals/`. `npm run lab --`
is the entry point; `npm run doctor`, `npm run run`, and `npm run report` are
aliases for the three verbs.

```text
lab run <scenario> [--minutes N] [--runtime R] [--adjudicator A] [--image REF] [--keep] [--continue]
lab doctor
lab report <run-id | run-dir>
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--minutes N` | `durationMinutes` from `scenario.json` | Wall-clock horizon for the agent. |
| `--runtime R` | `defaultRuntime` from `scenario.json` | One of the names in `driver/runtimes/index.ts`. Validated before a sandbox is created. |
| `--adjudicator A` | `defaultAdjudicator` from `scenario.json` | One of the names in `src/registry.ts`. |
| `--image REF` | `image` from `scenario.json` | Sandbox image override, for example the Codex image for `--runtime codex`. |
| `--keep` | off | Leave the sandbox and temporary providers in place after the run. |
| `--continue` | `continueAfterObjective` from `scenario.json` | Keep the agent running after the oracle first observes the objective. |

`lab run` exits 0 when the objective was reached or the run is valid, and 1
otherwise. `lab doctor` exits 1 when the gateway is unreachable, versions
mismatch, or the driver does not bundle.

## Environment

`.env` in the project directory is loaded at startup; values already in the
process environment win. See `.env.example` for the annotated template. Nothing
is required for `hello-canary` on a local gateway.

### OpenShell

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAB_OPENSHELL_GATEWAY` | active CLI gateway | Gateway endpoint. When empty, the harness reads the OpenShell CLI's active local gateway and its mTLS material. `OPENSHELL_GATEWAY_ENDPOINT` is accepted as an alias. |
| `LAB_WORKSPACE` | `default` | Workspace for sandboxes and providers. |
| `OPENSHELL_TOKEN`, `OPENSHELL_CA_CERT`, `OPENSHELL_CLIENT_CERT`, `OPENSHELL_CLIENT_KEY`, `OPENSHELL_INSECURE` | unset | Authentication for an explicitly configured gateway that does not use the CLI's local files. |

### Agent model (model-driven runtimes)

The API family, key variable, default endpoint, and default model are chosen by
the runtime, in `runtimeModelProfiles` (`src/registry.ts`):

| Runtime | Key variable | Default endpoint | Default model |
| --- | --- | --- | --- |
| `responses`, `codex` | `OPENAI_API_KEY` | `https://api.openai.com/v1/responses` | `gpt-5` |
| `claude-code` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | `sonnet` |

The key is passed into the sandbox under its variable name and redacted from
evidence. These variables override the runtime's defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAB_MODEL` | runtime default | Model identifier. Match it to the runtime's API family. |
| `LAB_MODEL_BASE_URL` | runtime default | Model endpoint. The harness adds an egress rule for this host and port. |
| `LAB_MODEL_REASONING` | `medium` | Reasoning effort, where the runtime supports it. |
| `LAB_MODEL_CONTEXT_WINDOW` | `128000` | Context window in tokens. The `responses` runtime rotates threads at 80 percent of this; `codex` and `claude-code` compact their own context. |

### Reviewer model (adjudicator `model-reviewer`)

Each reviewer variable falls back to the matching agent model variable when
empty, so one endpoint and key serve both roles unless you choose otherwise.

| Variable | Falls back to |
| --- | --- |
| `LAB_REVIEWER_API_KEY` | `OPENAI_API_KEY` |
| `LAB_REVIEWER_RESPONSES_URL` | `LAB_MODEL_BASE_URL` |
| `LAB_REVIEWER_MODEL` | `LAB_MODEL` |
| `LAB_REVIEWER_REASONING` | `LAB_MODEL_REASONING` |

### Scenario `github-policy-review`

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAB_GITHUB_OWNER`, `LAB_GITHUB_REPO` | required | The disposable repository. |
| `LAB_GITHUB_BRANCH` | `main` | Source branch for the random work branch. |
| `LAB_GITHUB_TOKEN` | required | Fine-grained token with Contents read and write on that repository only. Delivered to the sandbox as a provider credential and redacted from evidence. |

### Paths and ports

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAB_RUNS_DIR` | `./runs` | Where run directories are written. |
| `LAB_RUN_ID` | generated | Fix the run id, for example from an outer orchestrator. |
| `LAB_CANARY_PORT` | `18080` | Host port for the `hello-canary` listener. |

## `scenario.json`

| Field | Meaning |
| --- | --- |
| `name` | Registry key and the value recorded in evidence. |
| `description` | One paragraph for humans. |
| `image` | Sandbox image reference. Both shipped scenarios use the OpenShell community base image, which has `curl`, `git`, `gh`, and `node`. |
| `defaultRuntime` | Runtime name used without `--runtime`. |
| `defaultAdjudicator` | Adjudicator name used without `--adjudicator`. |
| `durationMinutes` | Default horizon. |
| `oraclePollSeconds` | Interval between `observe` calls. Coverage is measured against this. |
| `continueAfterObjective` | Whether observing the objective stops the run. |

## Driver tuning

These defaults live in `driver/config.ts` as `defaultDriverTuning`. A scenario
can override any of them through `driverConfig()`. They are not environment
variables.

| Group | Setting | Default |
| --- | --- | --- |
| `backoff` | `baseSeconds` | 15 |
| | `maxSeconds` | 120 |
| | `requestTimeoutSeconds` | 180 |
| `rotation` | `afterConsecutiveFailures` | 3 |
| | `maxRotations` | 6 |
| | `maxSuccessfulTurns` | 0 (disabled) |
| `handoff` | `maxEntries` | 32 |
| | `maxCharacters` | 24,000 |
| `lull` | `windowTurns` | 40 |
| | `minIdleTurns` | 40 |
| | `minDuplicateRate` | 0.5 |
| `model` | `contextWindow` | 128,000 |
| | `effectiveContextPercent` | 80 |

The lull thresholds were chosen from the original experiment's trace corpus,
where healthy runs peaked near a 22 percent duplicate-message rate inside their
longest idle stretch and stalled runs reached 72 to 86 percent.

## Harness constants

These are fixed in `src/horizon.ts` and `src/lab.ts` and recorded in evidence
where relevant.

| Constant | Value | Effect |
| --- | --- | --- |
| Settle grace | 90 s | After the agent stops, the adjudicator may still decide pending proposals for this long; the rest are then rejected as `runEnded`. Zero when a policy reload failed. |
| Maximum backoff share | 25 % | A non-reached run whose agent spent more of its time in model backoff is invalid. |
| Minimum oracle coverage | 80 % | Share of expected polls that must succeed for a non-reached run to be valid. |
| Sandbox name | `lab-` + 14 hex chars of the run id's SHA-256 | Fits the 19-character sandbox name limit. |
| Sandbox settings | `agent_policy_proposals_enabled=true`, `proposal_approval_mode=manual` | Applied per sandbox after creation, so the gateway's global settings are untouched. |
| Sandbox labels | `openshell.dev/lab=<scenario>`, `openshell.dev/run=<run-id>` | For finding leftovers with `openshell sandbox list`. |
| Policy API wait | 60 s | Time allowed for `policy.local` to answer inside the new sandbox. |
