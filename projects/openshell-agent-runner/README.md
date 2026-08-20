# OpenShell Agent Runner

`openshell-agent-runner` provides the `oar` command for validating and running
declarative agent profiles in OpenShell sandboxes. It has three commands:

```text
oar validate PROFILE_DIRECTORY
oar run PROFILE_DIRECTORY --task TASK --output PATH [OPTIONS]
oar doctor [OPTIONS]
```

OAR is an orchestrator, not an agent. It uploads explicitly selected files,
starts Pi, captures its result, optionally validates it against a configured
JSON Schema, publishes it atomically, and deletes the sandbox. Repository
inspection, Git operations, and conclusions belong to Pi inside the sandbox.

## Documentation

- [How OAR works](docs/index.md): components, profiles, uploads, execution
  lifecycle, result handling, and failure boundaries.

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

Release instructions are in [RELEASING.md](RELEASING.md). The release command
builds and publishes only `openshell-agent-runner`; it does not package other
projects in this repository.

OpenShell 0.0.106 or newer, a selected workspace, and an existing inference
route for the profile's model are required. OAR consumes that state and never
creates or changes gateways, providers, or inference routes.

## Validate a profile

Pass the profile directory containing `profile.yaml`:

```bash
uv run --project projects/openshell-agent-runner oar validate \
  .github/openshell-agents/profiles/dev-note-reviewer
```

Validation loads every referenced prompt, policy, skill, extension, and optional
output schema; rejects unknown keys and path escapes; and checks each schema.

## Check OpenShell

`doctor` performs read-only checks of the OpenShell CLI, selected gateway, and
inference configuration:

```bash
uv run --project projects/openshell-agent-runner oar doctor \
  --gateway openshell
```

## Run a profile task

Show help for a specific task by placing its profile and task before `--help`:

```bash
cd projects/openshell-agent-runner
uv run oar run \
  profiles/reviewer \
  --task review \
  --help
```

This prints focused task help from the executable profile and task
configuration: the invocation, configured uploads and environment, and the
resulting output. Generic CLI options remain in `oar run --help`.

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
- `--output`: host destination for the agent result.
- `--input`: host document required by tasks declaring `required_input: document`.
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
gateway. OAR always preserves OpenShell's Git-aware filtering; upload an ignored
file explicitly when a task genuinely needs it.

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
shows host-side result validation and atomic publication. Temporary paths,
sandbox identity, and the ownership token are generated exactly as they are for
a real run, but no subprocess or sandbox operation is executed.

## Profile format

A profile contains only settings that can change model behavior, sandbox
permissions, inputs, or task execution:

```yaml
id: reviewer
description: Review an uploaded document.

sandbox:
  policy: policy.yaml
  upload: []
  env: []

tasks:
  review:
    required_input: document
    prompt: prompt.md
    tools: [read, grep, find, ls, bash]
    skills: []
    extensions: []
```

Each profile directory must contain `profile.yaml`, `models.json`, and
`settings.json`. Profile-owned paths resolve relative to that directory. Native
upload sources retain OpenShell's current-directory semantics.

`models.json` is Pi's native provider and model registry. OAR requires exactly
one provider named `openshell` and exactly one model. `settings.json` is Pi's
native runtime selection and must set `defaultProvider`, `defaultModel`, and
`defaultThinkingLevel`. OAR copies both files unchanged and passes that same
selection explicitly as `--provider`, `--model`, and `--thinking`, so every task
uses one visible runtime configuration. Never place real credentials in these
files; OpenShell supplies inference access.

The included profiles provide complete examples. Their model files use this
shape:

```json
{
  "providers": {
    "openshell": {
      "baseUrl": "https://inference.local/v1",
      "api": "openai-completions",
      "apiKey": "unused",
      "authHeader": true,
      "compat": {
        "supportsDeveloperRole": false
      },
      "models": [
        {
          "id": "provider/model",
          "reasoning": true,
          "contextWindow": 200000,
          "maxTokens": 32000
        }
      ]
    }
  }
}
```

The matching runtime selection is:

```json
{
  "defaultProvider": "openshell",
  "defaultModel": "provider/model",
  "defaultThinkingLevel": "high"
}
```

Only non-default model behavior belongs in `models.json`. The included profiles
retain `contextWindow` and `maxTokens` because they affect compaction and output
limits, `reasoning: true` because the model supports thinking, and the one
compatibility override required by the OpenAI-compatible route. Display names,
text-only input, and zero-valued cost fields merely repeated Pi defaults and
were omitted.

### Result protocol

By default, OAR captures Pi's final headless response and publishes it without
interpreting its contents. The result must exist, be non-empty, and fit within
the one-MiB transport limit.

A task can optionally require structured JSON by referencing a JSON Schema:

```yaml
tasks:
  review:
    prompt: prompt.md
    output_schema: schemas/review.json
    tools: [read, grep, find, ls, bash]
```

OAR uploads the schema and automatically enables the generic `submit_result`
tool. Invalid submissions return schema diagnostics to Pi so it can correct and
resubmit within the same session. OAR validates the accepted JSON against the
same Draft 2020-12 schema again before publishing it. Pi's tool parameters use
TypeBox, as required by its extension API, while the submitted result is
validated with Ajv. The schema and its domain concepts belong entirely to the
profile; OAR has no built-in review result type.

OAR fixes implementation details that do not change the intended result: Pi is
the harness, its image is bundled with the package, autonomous approval and
provider isolation are enabled, the result is written to a standard sandbox
path, and the result size guard is one MiB.

The checkout includes a repository-neutral starter profile under
[`profiles`](profiles). Run it from the `projects/openshell-agent-runner`
directory. Its `review` task requires `--input DOCUMENT` and uploads that file
to OAR's standard document location in the sandbox.

## Image contract

The runner packages the Pi image context, pins the tested Pi version, and
installs the read-only harness under `/opt/oar`. OAR passes that packaged
context to native `openshell sandbox create`; profiles do not select an image.
This keeps the harness implementation and image contract in one release unit.

## Security boundary

- Pi runs as the image's unprivileged user under the profile policy.
- Caller uploads under `/workspace` and generated resources under
  `/sandbox/oar-runtime` are writable because OpenShell performs uploads through
  the workload policy.
- Source changes are disposable and are never synchronized back.
- Only OAR's standard result path is downloaded.
- Host-side transport checks, optional JSON Schema validation, and atomic
  publication are the result acceptance boundary.
- Result claims and provenance remain agent-produced; schema
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
