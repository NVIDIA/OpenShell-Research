---
title: Add an experiment
description: "Package a long task as one folder: a JSON config, a prompt, an opening policy, and a scorer."
agent_markdown: true
---

# Add an experiment

An experiment is one long task: what the agent is asked to do, the OpenShell
policy it starts with, the credentials it may use, and how the host scores its
progress. Everything that is not specific to the task, such as the sandbox
lifecycle, proposal routing, the ledger, evidence, and validity, lives in the
harness and is not repeated per experiment.

An experiment is one folder. Point the CLI at it by path, or put it under
`experiments/` and use its name.

| File | Purpose |
| --- | --- |
| `experiment.json` | Static settings: name, image, default runtime and reviewer, duration, score poll interval, the scorer, hardware profiles, providers, driver tuning. |
| `program.md` | The agent prompt, with `{{PLACEHOLDER}}` tokens. |
| `policy.json` | The opening OpenShell policy, as templated JSON. |
| `reviewer.md` | Optional. What capability expansion the model reviewer may grant. |
| `workdir/` | Optional. Files uploaded to `/sandbox/work` and made a git repository; the agent's working directory. |
| `image/` | Optional. A Dockerfile for fixed code and data the agent must not alter. |
| `score.ts` | Optional. Host-side hooks, only when a command inside the sandbox cannot produce the score. |

## Templating

`{{NAME}}` in `program.md`, `policy.json`, and `reviewer.md` is filled from the
active profile's `env`, then the instance facts returned by `prepare`, then
`RUN_ID`. Every placeholder must resolve; a typo fails before a sandbox exists.
Numbers work inside JSON: `"port": {{PORT}}`.

## The scorer

The score is the one thing the harness insists on producing from the trusted
side. `experiment.json` picks one of two kinds:

```json
"score": { "kind": "command", "command": ["/sandbox/.venv/bin/python", "/opt/autoresearch/score.py"], "direction": "min" }
```

A `command` scorer runs inside the sandbox on every poll and prints one JSON
line: `{"value": <number or null>, "done": <bool>, "trial": "<id>", "notes": "..."}`.
The command should live somewhere the agent cannot write, such as a read-only
path baked into the image. `value: null` means nothing to score yet.

```json
"score": { "kind": "module", "direction": "max" }
```

A `module` scorer is `hooks.score` in `score.ts`, a host function that checks
the world directly: a listener log, an API, a repository. Use it when the truth
is outside the sandbox.

`direction` says which way is better. `trial` identifies the artifact scored;
the ledger gets a new row when it changes, and the row records the commit at
the head of `/sandbox/work` when the experiment has a `workdir/`. `done` ends
the run unless `continueAfterDone` is set.

A squishy objective becomes a scorer by writing it as a ladder of observable
milestones before the run starts and returning the highest rung reached.

## Host-side hooks

`score.ts` exports `hooks` with any of:

```ts
export interface ExperimentHooks {
  prepare?(context): Promise<Instance>            // random ids, external resources, facts, secrets
  setup?(context, instance): Promise<() => Promise<void>>   // start a listener; return a teardown
  score?(context, instance): Promise<Score>       // required for a module scorer
  finalize?(context, instance): Promise<Record<string, unknown>>  // extra outcome fields
  driverConfig?(instance): Partial<DriverConfig>  // runtime-specific settings (the scripted runtime's target)
}
```

`Instance.facts` is recorded in `instance.json`, passed to the reviewer, and
available to templates. `Instance.secrets` lists literal strings redacted from
every saved artifact. Read secrets from the environment inside `prepare` and
fail fast with the variable name when they are missing.

## Profiles

```json
"defaultProfile": "laptop",
"profiles": {
  "laptop":  { "image": "nightshift/autoresearch:laptop",  "env": { "AUTORESEARCH_TIME_BUDGET": "90" },  "turnTimeoutSeconds": 7200 },
  "station": { "image": "nightshift/autoresearch:station", "env": { "AUTORESEARCH_TIME_BUDGET": "300" }, "turnTimeoutSeconds": 7200, "gpu": true }
}
```

A profile is a named hardware variant: an image, environment for the agent and
the score command, a per-turn timeout, and whether the sandbox gets a GPU. Anything the agent must not be able
to change, such as a time budget, belongs in profile `env` or in the image, not
in a file the agent edits.

## Providers

```json
"providers": [{ "type": "github", "credentials": { "GITHUB_TOKEN": "$NIGHTSHIFT_GITHUB_TOKEN" } }]
```

Each entry becomes an OpenShell provider for the run. The sandbox sees an
`openshell:` placeholder and the real value is attached only at the network
boundary. `$NAME` references are resolved from `.env` on the host and the
values are redacted from evidence. The agent's model key is delivered the same
way by the harness; do not list it here.

## Walkthrough: hello-canary

`experiments/hello-canary/` is the smallest complete experiment.

- `experiment.json` picks the base image, the `scripted` runtime and
  `auto-approve` reviewer, a 5-minute horizon, a 3-second score interval, and a
  `module` scorer.
- `score.ts` draws a random path and marker in `prepare`, starts the listener in
  `setup`, hands the scripted runtime its target in `driverConfig`, and reads
  the listener's log in `score`.
- `policy.json` allows `/usr/bin/curl` to reach the host listener only on the
  bootstrap path. The host is `host.openshell.internal`, which resolves to a
  private Docker address, so the endpoint carries `allowedIps` with the private
  CIDR ranges; a proposal from inside the sandbox cannot add IP trust.
- `program.md` tells the agent the exact request, that the first attempt will
  be blocked, and how to submit a proposal.

## Walkthrough: autoresearch

`experiments/autoresearch/` has no `score.ts` at all. Its `workdir/` holds the
one file the agent edits, its `image/` bakes the fixed code and data read-only
under `/opt/autoresearch`, `policy.json` makes `/opt` read-only and grants no
network, and the scorer is a command in the image. See
[Run autoresearch](autoresearch.md).

## Rules of thumb

- Score the world or a read-only command, never the agent's prose.
- Put what the agent must not change in the image or the profile, and make it
  read-only in `policy.json`.
- A GPU experiment needs `/sys` in the read-only paths; CUDA initialization
  reads CPU topology from sysfs and crashes without it.
- The opening policy should grant what the task needs and nothing else. Do not
  add the agent's model endpoint; the harness adds it for the selected runtime.
- Keep `score.ts` around 100 lines. If it grows, the extra logic belongs in a
  helper module, as `src/github.ts` does for GitHub calls.
