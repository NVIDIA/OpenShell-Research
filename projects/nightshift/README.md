# Nightshift

Run an agent on a long task overnight, inside an OpenShell sandbox, and wake up
to a ledger you can trust.

Nightshift is a small tool for the autoresearch pattern: an agent edits one
artifact, runs an experiment, reads a number, keeps or discards, and repeats for
hours. OpenShell makes that safe to leave unattended. The agent works inside a
sandbox with an opening policy you wrote. When it needs more, it asks, a
reviewer you configured decides, and OpenShell enforces the answer. Progress
is scored from the trusted side and written to a ledger the agent cannot edit.

An experiment is one folder you edit:

```text
experiments/autoresearch/
  experiment.json   agent, reviewer, duration, scorer, hardware profiles
  program.md        the task, read by the agent
  reviewer.md       what capability expansion the reviewer may grant
  policy.json       the opening OpenShell policy
  workdir/          the agent's files; becomes its git repository
  image/            fixed code and data, baked read-only into the sandbox image
```

Three experiments ship:

| Experiment | What it is | Needs |
| --- | --- | --- |
| `hello-canary` | Zero-credential smoke test of the whole loop: blocked request, proposal, approval, enforcement, score. | A local OpenShell gateway. |
| `autoresearch` | Karpathy's autoresearch: train a small GPT for a fixed budget, lower the validation bits per byte. Laptop profile on CPU; station profile for a GPU. | The image (`npm run image:autoresearch`) and a model key. |
| `github-policy-review` | The adversarial reviewer experiment from the [policy-review Dev Note](../../docs/dev-notes/posts/2026-08-27-adversarial-policy-review-long-horizon-agents.md). | A disposable GitHub repository and token. |

Any experiment runs with any agent runtime (`scripted`, `responses`, `codex`,
`claude-code`) and any reviewer (`auto-approve`, `reject-all`,
`model-reviewer`). Full documentation lives in [`docs/`](docs/index.md).

## Requirements

- Node.js 20.3 or newer, npm, and Docker.
- OpenShell 0.0.116 or newer with a gateway that can create Docker sandboxes.
- The `@nvidia/openshell-sdk` release matching the gateway exactly, from GitHub
  Packages (a token with `read:packages`).

Model-driven runtimes need a key for the runtime's API family: OpenAI Responses
for `responses` and `codex`, Anthropic for `claude-code`. The `model-reviewer`
reviewer needs an OpenAI Responses-compatible endpoint.

## Quickstart

Install the command from a checkout. Nothing is published to npm yet, so link
it:

```shell
export NODE_AUTH_TOKEN="$(gh auth token)"
npm ci
unset NODE_AUTH_TOKEN
npm link
nightshift doctor
```

To hand it to a teammate without a checkout, `npm pack` produces a tarball they
install with `npm install -g nightshift-0.0.0.tgz` (they still need the
GitHub Packages token for the SDK dependency).

```shell
nightshift run hello-canary
```

```text
DONE — validRun=true
```

Then take an experiment and make it yours:

```shell
nightshift init autoresearch
nightshift run ./autoresearch
nightshift report
```

`init` copies the bundled folder into your directory. A run without `--minutes`
continues until you press Ctrl-C, which ends it cleanly and prints the ledger;
`report` with no argument reads the latest run.

## Run autoresearch

Build the sandbox image once. It downloads TinyStories, trains a tokenizer, and
bakes the fixed code and data read-only under `/opt/autoresearch`:

```shell
npm run image:autoresearch
```

Put your model key and endpoint in `.env` (see `.env.example`), then let an
agent work until you stop it, or for a fixed night:

```shell
nightshift run autoresearch --runtime claude-code --minutes 480
```

Every run writes `runs/<run-id>/` with `results.tsv` (one row per trial: time,
turn, commit, score), `outcome.json`, the agent's events, every proposal and
decision, and the before and after policies, redacted of known secrets.
`nightshift report` prints the outcome and the ledger of the latest run.

See [Run autoresearch](docs/autoresearch.md) for the profiles, what the agent
may and may not do, and what the score does and does not guarantee.

## Extend it

- [Add an experiment](docs/add-an-experiment.md): one folder, no harness code.
- [Add a runtime](docs/add-a-runtime.md): adapt another agent to the one-turn
  contract and the common event vocabulary.
- [Add a reviewer](docs/add-a-reviewer.md): decide proposals on the trusted
  host while the harness applies them and fails closed.
- [Architecture](docs/architecture/index.md), [configuration](docs/reference/configuration.md),
  and [evidence](docs/reference/evidence.md) references.

## Development checks

```shell
npm run check
```

Runs the TypeScript typecheck and the unit tests. Then run `hello-canary`
against a local gateway before handing off a change to the harness or driver.
