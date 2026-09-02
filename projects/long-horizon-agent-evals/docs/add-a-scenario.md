---
title: Add a scenario
description: Package a new long-horizon task as one folder with a JSON config, a prompt, and about 100 lines of TypeScript.
agent_markdown: true
---

# Add a scenario

A scenario is one long-horizon task: what the agent is asked to do, the policy
that initially forbids it, the credentials it may use, and how the host observes
whether the objective was reached. Everything that is not specific to the task,
such as the sandbox lifecycle, proposal routing, evidence, and validity, lives
in the harness and is not repeated per scenario.

A scenario is one folder under `scenarios/`:

| File | Purpose |
| --- | --- |
| `scenario.json` | Static, human-editable settings: name, description, image, defaults, durations. |
| `task.md` | The agent prompt with `{{PLACEHOLDER}}` tokens for instance facts. |
| `scenario.ts` | The `Scenario` implementation, ideally around 100 lines. |

Additional prompt files, such as `reviewer.md` for a model adjudicator, sit in
the same folder.

## The contract

```ts
export interface Scenario {
  config: ScenarioConfig
  prompt(instance: Instance): Promise<string>
  prepare(context: ScenarioContext): Promise<Instance>
  policy(instance: Instance): Policy
  providers(instance: Instance): ProviderSpec[]
  driverConfig(instance: Instance): Partial<DriverConfig>
  setup?(context: ScenarioContext, instance: Instance): Promise<() => Promise<void>>
  observe(context: ScenarioContext, instance: Instance): Promise<OracleResult>
  finalize?(context: ScenarioContext, instance: Instance): Promise<Record<string, unknown>>
}
```

`Instance` is one prepared attempt: `facts`, recorded verbatim in
`instance.json` and passed to adjudicators, and `secrets`, the literal strings
redacted from every saved artifact. `ScenarioContext` gives the connected
gateway, the run directory, and the run id.

The harness calls the methods in this order:

1. `prepare` once, before any sandbox exists. Create external resources here
   (a branch, a marker) or nothing at all.
2. `setup` once, if present, to start host-side infrastructure such as a
   listener. Return a teardown; it runs even when the run fails.
3. `providers`, `policy`, `prompt`, and `driverConfig` while creating the
   sandbox and the driver configuration.
4. `observe` on every oracle interval during the run and once more after the
   agent stops. `objectiveReached` is the only load-bearing field; `detail`
   is merged into `oracle.jsonl` and the final `outcome.json`.
5. `finalize` once, if present. Its result is merged verbatim into
   `outcome.json`.

## Walkthrough: hello-canary

`scenarios/hello-canary/` is the smallest complete scenario and needs no
credentials.

**`scenario.json`** declares the base sandbox image, the `scripted` runtime and
`auto-approve` adjudicator as defaults, a 5-minute horizon, and a 3-second
oracle interval.

**`prepare`** draws a random canary path and a random marker. The marker is
listed in `secrets` so the redaction pass proves it never leaks into evidence.

**`policy`** is the create-time policy that forbids the objective. It allows
`/usr/bin/curl` to reach the host listener only on the bootstrap path. Two
details matter for any scenario that targets the host:

- The host is `host.openshell.internal`, which the sandbox proxy resolves to a
  private Docker address. OpenShell's SSRF protection blocks private addresses
  unless the endpoint is declared at create time with `allowedIps`, so the
  initial policy names the host with private CIDR ranges even though it allows
  only the bootstrap path. A proposal from inside the sandbox cannot add IP
  trust; it can only add rules under an endpoint the author already trusted.
- The binary that will make the request must be listed under `binaries`.

**`providers`** returns an empty list. **`driverConfig`** hands the scripted
runtime its target and binary; a model-driven scenario returns `{}`.

**`setup`** starts the listener on `LAB_CANARY_PORT` and appends every request
to `canary-server.jsonl`. **`observe`** reads that file and reports whether the
objective path was hit. The oracle is the host's own record, never the agent's
claim.

**`task.md`** tells the agent the exact request to make, that the first attempt
will be blocked, and how to submit a proposal to `http://policy.local/v1/proposals`.

## Register it

Add one import and one map entry in `src/registry.ts`:

```ts
export const scenarios: Record<string, Scenario> = {
  'hello-canary': helloCanary,
  'github-policy-review': githubPolicyReview,
  'my-scenario': myScenario,
}
```

`npm run doctor` lists it, and `npm run lab -- run my-scenario` runs it.

## Rules of thumb

- The oracle must observe the world, not the agent. Read a listener log, an
  API, or a filesystem the agent cannot forge.
- The initial policy must genuinely forbid the objective, and only the
  objective's path to success should need a proposal. Do not add the agent's
  model endpoint; the harness adds egress for the configured model when a
  model-driven runtime is selected.
- Anything that identifies the attempt belongs in `facts`; anything that must
  never appear in shared evidence belongs in `secrets`. Adjudicators receive
  `facts`, so a model adjudicator's instructions can be supplied there (see
  how `github-policy-review` passes `reviewerInstructions`).
- Read secrets from the environment inside `prepare` and fail fast with the
  variable name when they are missing.
- Keep `scenario.ts` around 100 lines. If it grows, the extra logic usually
  belongs in a helper module (as `src/github.ts` does for GitHub REST calls),
  not in the harness.
