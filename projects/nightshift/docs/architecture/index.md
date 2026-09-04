---
title: Architecture
description: What runs on the trusted host, what runs in the untrusted sandbox, and how a score and a policy proposal travel between them.
agent_markdown: true
---

# Architecture

Nightshift is split by trust. The host process is trusted: it holds the
credentials, talks to the OpenShell gateway through the SDK, decides proposals,
scores progress, writes the ledger, and saves evidence. The sandbox is
untrusted: it holds the agent, whatever tools the image provides, and a
placeholder for any credential the experiment grants. OpenShell sits between
them and is the only path from the sandbox to the outside.

<figure class="documentation-figure documentation-figure--wide">
  <img src="../assets/diagrams/system-overview.svg" alt="The trusted host runs the horizon loop, reviewer, and scorer. OpenShell's gateway and sandbox supervisor hold proposals and enforce policy. The untrusted agent runs inside the sandbox through the bundled driver and a runtime adapter. The scorer observes the world or a read-only command directly.">
  <figcaption>Everything the agent does to reach the outside passes through OpenShell enforcement. The scorer never asks the agent what happened.</figcaption>
</figure>

## Module map

| Location | Role | Notes |
| --- | --- | --- |
| `src/nightshift.ts` | CLI | `run`, `doctor`, `report`. Loads `.env`, resolves the experiment folder and profile, builds the model configuration, bundles the driver. |
| `src/horizon.ts` | Harness core | The whole run lifecycle. Knows nothing about training, GitHub, canaries, or any model. |
| `src/experiment.ts` | Experiment contract | Folder loader, templating, profiles, providers, the `Score` shape, and the optional hooks. |
| `src/openshell.ts` | OpenShell boundary | The only module that imports `@nvidia/openshell-sdk`. Connect, sandboxes, exec, providers, proposals, decisions, cleanup. |
| `src/reviewer.ts` | Reviewer contract | The host-side interface for deciding proposals. |
| `src/registry.ts` | Registry | Explicit map of reviewers; runtime model profiles and pinned images; re-exports the driver's runtime names. |
| `src/events.ts` | Vocabulary | The event types shared by driver, runtimes, and host. |
| `src/evidence.ts`, `src/validity.ts` | Evidence | File writing, redaction, and the experiment-agnostic validity classifier. |
| `src/driver-bundle.ts` | Bundler | esbuild wrapper that turns `driver/` into one ES module. |
| `src/github.ts` | Helper | Self-contained GitHub REST calls for `github-policy-review`. |
| `driver/driver.ts` | In-sandbox loop | Turns, backoff, handoff, lull detection, rotation. |
| `driver/config.ts` | Contract | `DriverConfig`, imported by both sides so it cannot drift. |
| `driver/runtimes/` | Runtime adapters | `types.ts` contract, `index.ts` registry, one file per runtime. |
| `reviewers/` | Reviewers | One file each. |
| `experiments/<name>/` | Experiments | `experiment.json`, `program.md`, `policy.json`, optional `reviewer.md`, `workdir/`, `image/`, `score.ts`. |
| `images/codex/` | Runtime image | The pinned Codex CLI on the base image. |

## The horizon loop

`runHorizon` in `src/horizon.ts` is the entire run. In order:

1. **Prepare.** If the experiment has a `prepare` hook it runs now: random
   identifiers, external setup, facts to record, secrets to redact. Template
   values are the profile's environment, then the facts, then the run id.
2. **Host infrastructure.** If the experiment has `setup`, it runs and returns
   a teardown that is guaranteed to run at the end.
3. **Providers.** The experiment's credentials, and the agent's model API key,
   become OpenShell providers so the sandbox receives placeholders and the real
   values are substituted only at the network boundary. The model key uses a
   per-run provider profile for the configured endpoint host.
4. **Sandbox.** The sandbox is created from the resolved image with the
   rendered `policy.json`, plus an egress rule for the agent's model endpoint
   from the runtime's declared binaries. Attaching a provider does not add its
   endpoints to the policy. Proposals are enabled and set to manual approval as
   sandbox-scoped settings. The harness waits for `policy.local` to answer and
   saves the initial effective policy.
5. **Workdir.** If the folder has `workdir/`, it is streamed into
   `/sandbox/work` as a tar archive and committed as the first revision of a
   fresh git repository. The agent's working directory is set there.
6. **Driver.** The driver configuration (rendered prompt, resume nudge,
   deadline, model settings, tuning, working directory) is encoded into an
   environment variable alongside the profile's environment. The bundled driver
   is streamed in over the exec API and started with the image's `node`. Every
   stdout line is parsed as an event, redacted, stamped with the host arrival
   time, and appended to `events.jsonl`.
7. **Three concurrent loops** run alongside the agent:
    - The **review loop** polls for pending proposals, rejects any the
      gateway already marked invalid, and sends the rest to the reviewer with
      the experiment's `reviewer.md`. Decisions are applied through the SDK and
      fail closed.
    - The **score loop** produces a score on the experiment's interval, either
      by running the score command inside the sandbox or by calling the
      `score` hook. Every poll goes to `scores.jsonl`; a new trial adds a row to
      `results.tsv` with the head commit of `/sandbox/work`. A `done` score
      stops the run unless configured to continue.
    - The **reload monitor** polls the sandbox policy status once a second. A
      failed reload means the sandbox is not enforcing what the gateway
      believes, so the run stops immediately and is marked invalid.
8. **Stop.** The agent stops at the deadline, when the scorer reports done,
   when the driver exits, or when enforcement fails.
9. **Settle.** The reviewer keeps deciding anything still pending for a
   grace period, then the harness rejects the remainder with an explicit
   reason. No proposal is left undecided.
10. **Final score and classification.** One more score is taken and
    `finalize` runs. The validity classifier turns operational signals into
    `validRun` and `invalidReasons`. The final effective policy and the
    gateway's proposal history are saved with `outcome.json`.
11. **Redact and clean up.** Every file in the run directory is redacted, and
    the run throws if a known secret survives. The sandbox and providers are
    deleted and their deletion verified; anything that remains is recorded in
    `cleanup.json`.

## A score's path

1. The agent changes its working directory and runs whatever the task calls
   for. For `autoresearch` that is a training run that ends by writing
   `out/result.json` with the fixed evaluator's number.
2. On the next poll the harness runs the score command inside the sandbox, or
   calls the `score` hook on the host. A `command` scorer must live where the
   agent cannot write; `autoresearch` bakes it into the image under `/opt`,
   which `policy.json` makes read-only.
3. The scorer returns `value`, `done`, and a `trial` id. When the trial id
   differs from the last row, the harness reads the head commit and message
   from the working directory's git log and appends a ledger row.
4. `outcome.json` records the best and last values and the trial count. The
   agent never writes to any of this.

## A proposal's path

1. The agent's request is denied at the network boundary by the sandbox
   supervisor.
2. The agent submits a proposal to `http://policy.local/v1/proposals` from
   inside the sandbox. The supervisor validates it and the gateway holds it as
   a pending chunk with a review token. OpenShell's mechanistic mapper also
   generates proposals of its own from denied connections; the reviewer decides
   those on their merits too, and the harness records each proposal's `origin`.
3. The host's review loop fetches pending chunks, saves the full proposal as
   `proposal-NNN.json`, and calls the reviewer with the proposal, the current
   effective policy, the experiment's facts, and the time remaining.
4. The decision is applied with the review token. If the gateway refuses an
   approval, the harness rejects the proposal instead and records the approval
   as failed closed. If the proposal changed since it was fetched, the harness
   refetches and asks again.
5. On approval the gateway publishes a new policy version. The supervisor loads
   it within a few seconds; the reload monitor would catch a failed load.
6. The agent's next attempt runs against the new policy.

## The driver loop

The driver runs inside the sandbox and never reads `.env`. Each iteration takes
one turn from the runtime, either starting a thread with the full prompt or
resuming with a short nudge, with a per-turn timeout. It watches the events the
runtime emits to build a bounded handoff checkpoint and to observe activity.

A full agent harness such as Claude Code or Codex decides for itself when a
turn ends. On an open-ended task it tends to keep working inside one turn for
a long time, which is fine: the per-turn timeout is hang protection, and an
experiment whose turns are legitimately long sets it high in its profile.

On a transient failure the driver backs off exponentially with jitter and
retries the same thread. It rotates to a fresh thread, reseeded with the prompt
and the checkpoint, when the runtime asks, after repeated consecutive failures,
when the lull detector sees a long idle and repetitive window, or when an
optional successful-turn budget is reached. Rotations are capped. The same
sandbox, filesystem, working directory, effective policy, and deadline persist
across rotations; only the model's context is new.

## Why the SDK is pinned to the gateway

The SDK's generated RPC types track one OpenShell release. `src/openshell.ts`
reads the gateway's version and the installed SDK version at connect time and
refuses to run unless they match and both meet the project's minimum. This
trades convenience for evidence integrity: a silent contract drift between the
two could record a policy or proposal shape that is not what the gateway
actually enforced.
