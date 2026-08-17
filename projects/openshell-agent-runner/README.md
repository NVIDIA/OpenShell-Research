# OpenShell Agent Runner

`openshell-agent-runner` provides the `oar` command for validating and running
declarative agent profiles in OpenShell sandboxes. It has three commands:

```text
oar validate PROFILE_DIRECTORY
oar run PROFILE_DIRECTORY --task TASK --output PATH [OPTIONS]
oar doctor [OPTIONS]
```

OAR is an orchestrator, not an agent. It uploads explicitly selected files,
starts Pi, validates the configured structured output, downloads it atomically,
and deletes the sandbox. Repository inspection, Git operations, and conclusions
belong to Pi inside the sandbox.

## Install

Directly from this checkout:

```bash
uvx --from ./projects/openshell-agent-runner oar --help
```

For an editable development environment:

```bash
uv sync --project projects/openshell-agent-runner --locked
uv run --project projects/openshell-agent-runner pre-commit install
uv run --project projects/openshell-agent-runner oar --help
```

The pre-commit hook automatically applies Ruff's Black-compatible formatter to
staged Python files in this project. Hook installation is required once per
checkout.

After the package is published, the equivalent package-index invocation is
`uvx --from openshell-agent-runner oar --help`.

OpenShell 0.0.106 or newer, a selected workspace, and an existing inference
route for the profile's model are required. OAR consumes that state and never
creates or changes gateways, providers, or inference routes.

## Validate a profile

Pass the profile directory containing `profile.yaml`:

```bash
uv run --project projects/openshell-agent-runner oar validate \
  .github/openshell-agents/profiles/dev-note-reviewer
```

Validation loads every referenced prompt, policy, skill, and extension; rejects
unknown keys and path escapes; and checks the structured output contract.

## Check OpenShell

`doctor` performs read-only checks of the OpenShell CLI, selected gateway, and
inference configuration:

```bash
uv run --project projects/openshell-agent-runner oar doctor \
  --gateway openshell
```

## Run a profile task

```bash
uv run --project projects/openshell-agent-runner oar run \
  .github/openshell-agents/profiles/dev-note-reviewer \
  --task editorial \
  --gateway openshell \
  --upload .:/workspace/source \
  --upload .git:/workspace/source/.git \
  --env REVIEW_TARGET_PATH=docs/dev-notes/posts/2026-08-07-formal-methods-ai-generated-robot-actions.md \
  --output /tmp/dev-note-review.json
```

The supported run options are deliberately small:

- `--task`: task identifier from the profile.
- `--output`: host destination for the validated structured output.
- `--upload`: repeatable native OpenShell `SOURCE:DESTINATION` mapping.
- `--env`: repeatable non-secret `KEY=VALUE` sandbox environment value.
- `--gateway` and `--workspace`: select existing OpenShell state.
- `--timeout-seconds`: maximum agent runtime.
- `--keep-sandbox`: retain the sandbox for deliberate debugging.
- `--dry-run`: print the complete command sequence and host actions without
  executing anything.

A source can be a file or directory. For native file uploads, the destination
is the exact filename; for directory uploads, it is the destination directory.
OAR does not add repository, snapshot, changed-file, or Git abstractions. The
first upload above uses OpenShell's default Git-aware filtering, while the
explicit `.git` upload provides repository history without also uploading every
ignored file. Review upload contents before sending private source to a remote
gateway; do not use `no_git_ignore: true` for a repository that may contain
ignored credentials or other sensitive files.

### Inspect the execution

Add `--dry-run` to the same `run` invocation:

```bash
uv run --project projects/openshell-agent-runner oar run \
  .github/openshell-agents/profiles/dev-note-reviewer \
  --task editorial \
  --gateway openshell \
  --upload .:/workspace/source \
  --upload .git:/workspace/source/.git \
  --env REVIEW_TARGET_PATH=docs/dev-notes/posts/2026-08-07-formal-methods-ai-generated-robot-actions.md \
  --output /tmp/dev-note-review.json \
  --dry-run
```

The preview prints the exact dynamically generated `openshell sandbox create`,
`download`, ownership `get`, and `delete` commands in execution order. It also
shows host-side Pydantic validation and atomic publication. Temporary paths,
sandbox identity, and the ownership token are generated exactly as they are for
a real run, but no subprocess or sandbox operation is executed.

## Profile format

A profile contains its Pi configuration, native sandbox settings, and one or
more tasks:

```yaml
id: reviewer
description: Review an uploaded document.

harness:
  type: pi
  model: provider/model
  context_window: 200000
  max_tokens: 32000

sandbox:
  from: registry.example/oar-pi@sha256:...
  policy: policy.yaml
  upload: []
  env: [REPOSITORY_ROOT=/workspace/input]
  no_git_ignore: false
  no_auto_providers: true
  approval_mode: auto

tasks:
  inspect:
    prompt: prompt.md
    tools: [read, grep, find, ls, bash]
    skills: []
    extensions: []
    output:
      type: document_review
      contract:
        reviewer_id: general
        criteria: [clarity, completeness]
        max_findings: 8
      sandbox_path: /sandbox/artifacts/report.json
      max_bytes: 1048576
```

Each profile directory must contain `profile.yaml`. Profile-owned paths resolve
relative to that directory. Native upload sources retain OpenShell's
current-directory semantics.

`approval_mode: auto` is the autonomous-runner default. It lets OpenShell
automatically accept agent-authored policy proposals only when its prover finds
no policy delta; proposals with findings still require review. Set it to
`manual` when every proposal must wait for a person.

`document_review` is the structured output type. Its Pydantic model covers
criterion scores, evidence-backed findings, verdict, confidence, and source
provenance. OAR generates Pi's submission schema from that model and uses the
same model for authoritative structural validation on the host.

The checkout includes a repository-neutral starter profile under
[`profiles`](profiles). Its local image path is resolved by OpenShell from the
current working directory, so run it from this repository's root.

## Image contract

The runner packages a Pi image context that pins the tested Pi version and
installs the read-only harness under `/opt/oar`. A local profile may use the
packaged context path:

```text
projects/openshell-agent-runner/src/openshell_agent_runner/harnesses/pi/assets
```

A remote gateway should use a published compatible image pinned by immutable
digest. OAR passes `sandbox.from` directly to native `openshell sandbox create`;
it does not silently select or publish images.

## Security boundary

- Pi runs as the image's unprivileged user under the profile policy.
- Caller uploads under `/workspace` and generated resources under
  `/sandbox/oar-runtime` are writable because OpenShell performs uploads through
  the workload policy.
- Source changes are disposable and are never synchronized back.
- Only the task's configured output file is downloaded.
- Host-side Pydantic validation and atomic publication are the artifact
  acceptance boundary.
- Review findings and provenance remain agent-produced claims; schema
  validation does not independently prove their factual accuracy.
- `--env` is for non-secret values. Credentials remain in OpenShell's provider
  and inference mechanisms.
- Cleanup checks a reserved ownership label before deleting the sandbox.

The supplied Dev Note policy permits no ordinary network egress. Inference uses
OpenShell's managed inference path.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Execution completed and the output validated. |
| `1` | OpenShell execution, timeout, ownership inspection, or cleanup failed. |
| `2` | CLI input or profile configuration was invalid. |
| `3` | The output was missing, oversized, invalid, or failed its contract. |

## Development

Run from `projects/openshell-agent-runner`:

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

The repository workflow validates the repository and starter profiles, runs the
credential-free suite, builds the distributions, verifies the wheel contents,
and builds the Pi image. Real inference requires an authenticated OpenShell
gateway and is intentionally not run on GitHub-hosted workers.
