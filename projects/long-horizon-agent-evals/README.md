# Long-Horizon Agent Evals

A small research harness for one question: what does an agent do over a long
stretch of wall-clock time when the thing it is asked to do is forbidden by
policy, and the only way forward is to ask for more permission?

Each run places an agent in an OpenShell sandbox whose initial policy forbids
the objective. The agent can ask OpenShell for a policy change. An adjudicator
decides each request. OpenShell enforces the result. The host observes the
outcome directly and never asks a model whether it succeeded.

The harness has four roles, and any combination of them runs together:

| Role | Ships with |
| --- | --- |
| Scenario: task, initial policy, credentials, oracle | `hello-canary`, `github-policy-review` |
| Agent runtime: one bounded turn inside the sandbox | `scripted`, `responses`, `codex` |
| Adjudicator: approve or reject one proposal | `auto-approve`, `reject-all`, `model-reviewer` |
| Harness core: the horizon loop | `src/horizon.ts` |

The `github-policy-review` scenario is the experiment from the
[adversarial policy-review Dev Note](../../docs/dev-notes/posts/2026-08-27-adversarial-policy-review-long-horizon-agents.md):
a persistent challenger with a repository-scoped GitHub token tries to get an
AI reviewer to grant enough policy to mutate a protected repository, with
OpenShell's enforcement as the backstop.

Full documentation lives in [`docs/`](docs/index.md) and is published on the
OpenShell Research documentation site.

## Requirements

- Node.js 20.3 or newer and npm
- OpenShell 0.0.116 or newer with a gateway that can create Docker sandboxes
- The `@nvidia/openshell-sdk` release matching the gateway exactly, from GitHub
  Packages (a token with `read:packages`)

Model-driven runtimes need an OpenAI Responses-compatible endpoint and key. The
`github-policy-review` scenario needs a disposable GitHub repository and a
token scoped to it. The quickstart needs neither.

## Quickstart

```shell
export NODE_AUTH_TOKEN="$(gh auth token)"
npm ci
unset NODE_AUTH_TOKEN
npm run doctor
```

`hello-canary` is a zero-credential smoke test of the whole pipeline: a
scripted agent is blocked at the network boundary, proposes the narrowest rule,
gets it approved, enforced, and reloaded, and then reaches a host listener that
the host itself records.

```shell
npm run lab -- run hello-canary
```

```text
OBJECTIVE REACHED — validRun=true
runs/<run-id>
```

Every run writes `runs/<run-id>/` with `outcome.json`, the agent's events, every
proposal and decision, and the before and after effective policies, redacted of
known secrets. `npm run report -- <run-id>` prints the outcome.

## The adversarial scenario

> [!WARNING]
> `github-policy-review` performs real GitHub operations and hands the
> challenger a token that can write to the configured repository. Use a
> disposable repository and a fine-grained token scoped only to it. The setup
> branch is left behind as evidence.

Fill in the model and GitHub values in `.env` (see `.env.example`), then:

```shell
npm run lab -- run github-policy-review
```

See [Run the GitHub policy-review scenario](docs/github-policy-review.md) for
setup, what happens during a run, how to read the outcome, and the fairness
choices behind the prompts.

## Extend it

- [Add a scenario](docs/add-a-scenario.md): one folder, a JSON config, a
  prompt, and about 100 lines.
- [Add a runtime](docs/add-a-runtime.md): adapt another agent to the one-turn
  contract and the common event vocabulary.
- [Add an adjudicator](docs/add-an-adjudicator.md): decide proposals on the
  trusted host while the harness applies them and fails closed.
- [Architecture](docs/architecture/index.md), [configuration](docs/reference/configuration.md),
  and [evidence](docs/reference/evidence.md) references.

## Development checks

```shell
npm run check
```

Runs the TypeScript typecheck and the unit tests for events, redaction, the
handoff and lull logic, version matching, and outcome classification. Then run
`hello-canary` against a local gateway before handing off a change to the
harness or driver.
