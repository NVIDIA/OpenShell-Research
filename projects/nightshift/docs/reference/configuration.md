---
title: Configuration reference
description: Command-line verbs, environment variables, experiment.json fields, profiles, and driver tuning defaults.
agent_markdown: true
---

# Configuration reference

## Command line

`nightshift` is the command. In a source checkout, `npm run nightshift --` is
the same thing, and `npm run doctor`, `npm run run`, and `npm run report` are
aliases. Runs are written under `./runs` in the current directory.

```text
nightshift init [experiment] [directory]
nightshift run <experiment> [--profile P] [--minutes N] [--runtime R] [--model ID] [--reviewer A]
                            [--image REF] [--turn-timeout S] [--keep] [--continue]
nightshift report [run-id | run-dir]
nightshift doctor
```

`init` copies a bundled experiment folder into a directory you own, so you can
edit it and run it from there; with no arguments it lists the bundled
experiments. `<experiment>` for `run` is a folder path, or the name of a
bundled folder. `report` with no argument reads the latest run.

| Flag | Default | Effect |
| --- | --- | --- |
| `--profile P` | `defaultProfile` from `experiment.json`, else none | Hardware profile: image, environment, per-turn timeout, GPU. |
| `--minutes N` | `durationMinutes` from `experiment.json`, else none | Wall-clock horizon. With no horizon the run continues until you press Ctrl-C once, which ends it the same way a deadline does: pending proposals are settled, a final score is taken, the evidence is written, and the report is printed. A second Ctrl-C exits immediately without cleanup. |
| `--runtime R` | `defaultRuntime` from `experiment.json` | One of the names in `driver/runtimes/index.ts`. Validated before a sandbox is created. |
| `--model ID` | `NIGHTSHIFT_MODEL`, else the runtime's default | Model identifier for the agent. |
| `--reviewer A` | `defaultReviewer` from `experiment.json` | One of the names in `src/registry.ts`. |
| `--image REF` | the profile's image, else the runtime's pinned image, else `image` from `experiment.json` | Sandbox image override. |
| `--turn-timeout S` | `NIGHTSHIFT_TURN_TIMEOUT_SECONDS`, else the profile, else `experiment.json`, else 180 | Per-turn hang protection. |
| `--keep` | off | Leave the sandbox and temporary providers in place after the run. |
| `--continue` | `continueAfterDone` from `experiment.json` | Keep the agent running after the scorer first reports `done`. |

`nightshift run` prints the report when the run ends and exits 0 when the
scorer reported done or the run is valid, and 1 otherwise. `runs/latest` always
points at the most recent run. `nightshift doctor` exits 1 when the gateway is
unreachable, versions mismatch, or the driver does not bundle.

## Environment

`.env` in the current directory, then in the package directory, is loaded at startup; values already in the
process environment win, and an empty assignment is treated as unset. See
`.env.example` for the annotated template. Nothing is required for
`hello-canary` on a local gateway.

### OpenShell

| Variable | Default | Purpose |
| --- | --- | --- |
| `NIGHTSHIFT_OPENSHELL_GATEWAY` | active CLI gateway | A gateway URL, used with the `OPENSHELL_*` certificate variables below, or the name of a gateway registered with the OpenShell CLI (`openshell gateway list`), whose endpoint and mTLS files are read from the CLI's config. When empty, the CLI's active gateway is used. `OPENSHELL_GATEWAY_ENDPOINT` is accepted as an alias. |
| `NIGHTSHIFT_WORKSPACE` | `default` | Workspace for sandboxes and providers. |
| `OPENSHELL_TOKEN`, `OPENSHELL_CA_CERT`, `OPENSHELL_CLIENT_CERT`, `OPENSHELL_CLIENT_KEY`, `OPENSHELL_INSECURE` | unset | Authentication for an explicitly configured gateway that does not use the CLI's local files. |

A gateway on another machine, for example a DGX Station, works over an ssh
tunnel without Nightshift knowing anything about ssh. Forward the gateway port
to a free local port, copy that gateway's `mtls` folder from
`~/.config/openshell/gateways/<name>/` on the remote machine, and point the
harness at the tunnel:

```bash
NIGHTSHIFT_OPENSHELL_GATEWAY=https://localhost:17671 \
OPENSHELL_CA_CERT=~/station-mtls/ca.crt OPENSHELL_CLIENT_CERT=~/station-mtls/tls.crt OPENSHELL_CLIENT_KEY=~/station-mtls/tls.key \
npm run nightshift -- run autoresearch --profile station
```

Sandboxes then run on the remote machine, so the profile's image must exist in
that machine's Docker, and a `module` scorer that listens on the host (such as
`hello-canary`) cannot be reached from there.

### Agent model (model-driven runtimes)

The API family, key variable, default endpoint, default model, how the key is
sent, and the binaries allowed to reach the endpoint are chosen by the runtime,
in `runtimeModelProfiles` (`src/registry.ts`):

| Runtime | Key variable | Default endpoint | Default model | Binaries |
| --- | --- | --- | --- | --- |
| `responses` | `OPENAI_API_KEY` | `https://api.openai.com/v1/responses` | `gpt-5` | node |
| `codex` | `OPENAI_API_KEY` | `https://api.openai.com/v1/responses` | `gpt-5` | node, the Codex CLI |
| `claude-code` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | `sonnet` | node, the Claude Code CLI |

The key reaches the sandbox as an OpenShell provider credential: the harness
imports a per-run provider profile for the endpoint host, creates a provider
holding the key, and attaches it to the sandbox. The agent sees the variable
set to an `openshell:` placeholder, and the real value is attached only at the
network boundary for that host. The key is also redacted from evidence. These
variables override the runtime's defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NIGHTSHIFT_MODEL` | runtime default | Model identifier. Match it to the runtime's API family. |
| `NIGHTSHIFT_MODEL_API_KEY` | the runtime's key variable | Key for an endpoint that is not the family's usual one, for example an OpenAI-compatible inference gateway. Delivered under the runtime's variable name. |
| `NIGHTSHIFT_MODEL_CREDENTIAL` | `provider` | `env` injects the raw key as a sandbox environment variable instead of a provider placeholder. For debugging only; the agent can then read the key. |
| `NIGHTSHIFT_MODEL_BASE_URL` | runtime default | Model endpoint. The harness adds an egress rule for this host and port. |
| `NIGHTSHIFT_MODEL_REASONING` | `medium` | Reasoning effort, where the runtime supports it. `none` omits the field entirely. |
| `NIGHTSHIFT_MODEL_CONTEXT_WINDOW` | `128000` | Context window in tokens. The `responses` runtime rotates threads at 80 percent of this; `codex` and `claude-code` compact their own context. |

### Reviewer model (reviewer `model-reviewer`)

The reviewer's settings are independent of the agent's, because the agent may
run on a different API family. The reviewer speaks the OpenAI Responses API and
reads the experiment folder's `reviewer.md` for what it may grant.

| Variable | Default |
| --- | --- |
| `NIGHTSHIFT_REVIEWER_API_KEY` | `OPENAI_API_KEY` |
| `NIGHTSHIFT_REVIEWER_RESPONSES_URL` | `https://api.openai.com/v1/responses` |
| `NIGHTSHIFT_REVIEWER_MODEL` | `gpt-5` |
| `NIGHTSHIFT_REVIEWER_REASONING` | `medium`; `none` omits the field |

### Experiment `github-policy-review`

| Variable | Default | Purpose |
| --- | --- | --- |
| `NIGHTSHIFT_GITHUB_OWNER`, `NIGHTSHIFT_GITHUB_REPO` | required | The disposable repository. |
| `NIGHTSHIFT_GITHUB_BRANCH` | `main` | Source branch for the random work branch. |
| `NIGHTSHIFT_GITHUB_TOKEN` | required | Fine-grained token with Contents read and write on that repository only. Delivered to the sandbox as a provider credential and redacted from evidence. |

### Paths and ports

| Variable | Default | Purpose |
| --- | --- | --- |
| `NIGHTSHIFT_RUNS_DIR` | `./runs` in the current directory | Where run directories are written. `runs/latest` links to the most recent. |
| `NIGHTSHIFT_RUN_ID` | generated | Fix the run id, for example from an outer orchestrator. |
| `NIGHTSHIFT_CANARY_PORT` | `18080` | Host port for the `hello-canary` listener. |

## `experiment.json`

| Field | Meaning |
| --- | --- |
| `name` | Recorded in evidence. |
| `description` | One paragraph for humans. |
| `image` | Sandbox image when no profile or runtime image applies. |
| `defaultRuntime`, `defaultReviewer` | Used without `--runtime` / `--reviewer`. |
| `durationMinutes` | Default horizon. Omit it for an experiment that runs until stopped, as `autoresearch` does. |
| `scorePollSeconds` | Interval between score checks. Coverage is measured against this. |
| `continueAfterDone` | Whether a `done` score stops the run. |
| `score` | `{ "kind": "command", "command": [...], "direction": "min" \| "max" }` or `{ "kind": "module", "direction": ... }`. See [Add an experiment](../add-an-experiment.md). |
| `profiles`, `defaultProfile` | Named hardware variants: `image`, `env`, `turnTimeoutSeconds`, `gpu`. Profile `env` reaches the agent process and the score command, and fills `{{NAME}}` in templates. |
| `providers` | `[{ "type": "github", "credentials": { "GITHUB_TOKEN": "$NIGHTSHIFT_GITHUB_TOKEN" } }]`. Values are `$ENV` references resolved on the host. |
| `driver` | Overrides for the driver tuning below, plus `resumeNudge`, the text the driver uses to resume the agent each turn. |

## Driver tuning

These defaults live in `driver/config.ts` as `defaultDriverTuning`. Precedence
is defaults, then `experiment.json` `driver`, then the profile's
`turnTimeoutSeconds`, then the experiment's `driverConfig` hook, then the
command line.

| Group | Setting | Default |
| --- | --- | --- |
| top level | `turnTimeoutSeconds` | 180 |
| `backoff` | `baseSeconds` | 15 |
| | `maxSeconds` | 120 |
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

`turnTimeoutSeconds` is hang protection, not a work budget: a turn that has not
returned after that long is aborted and treated as a transient failure, and
repeated hangs rotate the thread. A full agent harness on an open-ended task
may legitimately work inside one turn for a long time, so `autoresearch` sets
it to two hours in its profiles. The whole driver configuration is saved as
`driver-config.json` in the run directory.

## Harness constants

These are fixed in `src/horizon.ts` and `src/nightshift.ts` and recorded in
evidence where relevant.

| Constant | Value | Effect |
| --- | --- | --- |
| Sandbox working directory | `/sandbox/work` | Where `workdir/` lands; the agent's cwd and git repository. |
| Score command timeout | 300 s | A score command still running after this is one failed poll. |
| Settle grace | 90 s | After the agent stops, the reviewer may still decide pending proposals for this long; the rest are then rejected as `runEnded`. Zero when a policy reload failed. |
| Maximum backoff share | 25 % | A run that never reached done and whose agent spent more of its time in model backoff is invalid. |
| Minimum score coverage | 80 % | Share of expected score polls that must succeed for a run that never reached done to be valid. |
| Sandbox name | `ns-` + 14 hex chars of the run id's SHA-256 | Fits the 19-character sandbox name limit. |
| Sandbox settings | `agent_policy_proposals_enabled=true`, `proposal_approval_mode=manual` | Applied per sandbox after creation. |
| Sandbox labels | `openshell.dev/nightshift=<experiment>`, `openshell.dev/run=<run-id>` | For finding leftovers with `openshell sandbox list`. |
| Policy API wait | 60 s | Time allowed for `policy.local` to answer inside the new sandbox. |
