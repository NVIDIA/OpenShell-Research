---
title: Long-Horizon Agent Evals
description: Run one persistent agent toward one forbidden objective for a wall-clock horizon inside OpenShell, and keep evidence you can trust.
agent_markdown: true
---

# Long-Horizon Agent Evals

Long-Horizon Agent Evals is a small research harness for one question: what
does an agent do over a long stretch of wall-clock time when the thing it is
asked to do is forbidden by policy, and the only way forward is to ask for more
permission?

Each run places an agent in an OpenShell sandbox whose initial policy forbids
the objective. The agent can ask OpenShell for a policy change. An adjudicator
decides each request. OpenShell enforces the result. The host observes the
outcome directly and never asks a model whether it succeeded.

The harness is about 2,700 lines of TypeScript and is meant to be read, forked,
and changed. It is a research example, not a hosted evaluation service. It grew
out of the
[adversarial policy-review Dev Note](../../dev-notes/posts/2026-08-27-adversarial-policy-review-long-horizon-agents.md),
whose experiment ships here as the `github-policy-review` scenario.

## Four roles

| Role | Owns | Ships with |
| --- | --- | --- |
| Scenario | The task prompt, the initial policy that forbids it, credentials, and the oracle that observes the outcome | `hello-canary`, `github-policy-review` |
| Agent runtime | How to take one bounded turn inside the sandbox and report it in the common event vocabulary | `scripted`, `responses`, `codex`, `claude-code` |
| Adjudicator | Approve or reject one policy proposal | `auto-approve`, `reject-all`, `model-reviewer` |
| Harness core | The horizon loop: sandbox lifecycle, proposal routing, oracle polling, evidence, validity | `src/horizon.ts` |

Any scenario runs with any runtime and any adjudicator. The combination is
chosen on the command line, and the defaults live in each scenario's
`scenario.json`.

## Requirements

- Node.js 20.3 or newer and npm.
- OpenShell 0.0.116 or newer with a gateway that can create Docker sandboxes.
  A local Homebrew install with the default gateway works without extra
  configuration.
- The `@nvidia/openshell-sdk` release that matches the gateway exactly. The
  harness refuses to run against mixed versions.
- Read access to GitHub Packages for `@nvidia/openshell-sdk`, which means a
  GitHub token with the `read:packages` scope.

Model-driven runtimes also need an OpenAI Responses-compatible endpoint and an
API key. The `github-policy-review` scenario also needs a disposable GitHub
repository and a token scoped to it. The quickstart below needs neither.

## Quickstart: hello-canary

`hello-canary` is a zero-credential smoke test of the whole pipeline. The host
runs a tiny HTTP listener. The sandbox policy lets the agent reach that listener
only on a bootstrap path. The objective is a different, random path that starts
blocked. A scripted agent with no model attempts the request, is denied at the
network boundary, proposes the narrowest rule that would allow it, waits for
the decision, and retries. Reaching the listener proves that proposal
submission, adjudication, enforcement, policy reload, and the oracle all work.

From `projects/long-horizon-agent-evals/` in a source checkout:

```bash
export NODE_AUTH_TOKEN="$(gh auth token)"
npm ci
unset NODE_AUTH_TOKEN
```

```bash
npm run doctor
```

`doctor` connects to the gateway, checks that the gateway and SDK are the same
release, lists the registered scenarios, adjudicators, and runtimes, and
bundles the in-sandbox driver. It prints `doctor: ready` when a run can start.

```bash
npm run lab -- run hello-canary
```

The run prints one JSON line per host event and finishes with a summary:

```text
OBJECTIVE REACHED — validRun=true
/path/to/runs/<run-id>
```

A hello-canary run takes well under a minute with the base sandbox image
cached. If it does not reach the objective, start with
[Evidence reference](reference/evidence.md#invalid-reasons) to see which
stage failed.

## What a run produces

Every run owns `runs/<run-id>/`. The load-bearing file is `outcome.json`:
whether the objective was reached, whether the run is valid, and the
machine-readable reasons when it is not. `events.jsonl` holds the agent's
activity in the common vocabulary, `decisions.jsonl` holds every adjudication
and how OpenShell applied it, and each proposal is saved in full as the
adjudicator saw it. Known secrets are redacted from every file before the run
directory is considered shareable.

```bash
npm run report -- <run-id>
```

## Where next

- [Run the GitHub policy-review scenario](github-policy-review.md): the
  adversarial experiment, with the setup and safety steps it requires.
- [Add a scenario](add-a-scenario.md), [add a runtime](add-a-runtime.md), or
  [add an adjudicator](add-an-adjudicator.md).
- [Architecture](architecture/index.md): what runs on the host, what runs in
  the sandbox, and how a proposal travels between them.
- [Configuration reference](reference/configuration.md) and
  [Evidence reference](reference/evidence.md).
