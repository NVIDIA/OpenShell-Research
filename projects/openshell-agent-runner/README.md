# OpenShell Agent Runner

OpenShell Agent Runner (OAR) launches one ephemeral agent for one configured
task. Each `oar run` creates an isolated OpenShell sandbox, runs Pi with the
selected profile, publishes one result, and removes the sandbox. This bounded
lifecycle works well in CI jobs and other automated workflows.

OAR uses an existing OpenShell gateway, workspace, and inference route. It does
not create or change providers, credentials, gateways, workspaces, or routes.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- OpenShell 0.0.111 or newer
- A running OpenShell gateway
- An inference route and its model ID

## Quick start

Create the profiles packaged with OAR. `MODEL_ID` is an ordinary shell variable;
replace its value with the model ID configured on your inference route.

```bash
export MODEL_ID="provider/model"

uvx --from openshell-agent-runner oar init ./profiles \
  --model "$MODEL_ID"
uvx --from openshell-agent-runner oar doctor --gateway openshell
```

Validate the included technical-writing reviewer and preview its task:

```bash
printf '# Review me\n\nA short document.\n' > document.md
uvx --from openshell-agent-runner oar validate \
  ./profiles/technical-writing-reviewer

uvx --from openshell-agent-runner oar run ./profiles/technical-writing-reviewer \
  --task review-document \
  --gateway openshell \
  --input document.md \
  --output /tmp/oar-review.json \
  --dry-run
```

Replace `openshell` with your gateway name. Remove `--dry-run` to launch the
agent and write its structured result to `/tmp/oar-review.json`.

`oar init` copies the packaged profiles into an ordinary directory so you can
inspect, edit, and commit them. Omit `--profile` to create all packaged profiles,
or repeat `--profile NAME` to select a subset.

## Profiles

A profile contains `profile.yaml`, Pi's `models.json` and `settings.json`, an
OpenShell policy, and the prompts or other files referenced by its tasks. The
profile owns stable behavior and permissions; the CLI supplies values that vary
for each run, such as the task, inputs, output path, gateway, and workspace.

OAR packages three focused review profiles:

| Profile | Input | Purpose |
| --- | --- | --- |
| `code-reviewer` | Repository directory | Find concrete engineering issues without scope creep or speculative hardening. |
| `technical-writing-reviewer` | Document file | Review technical accuracy, clarity, completeness, and reader utility. |
| `slop-cop` | Document file | Find material formulaic, vague, inflated, or generic prose without making AI-authorship claims. |

Each reviewer returns criterion scores and an overall score from 0 to 100, where
100 is best. The overall score is the rounded arithmetic mean of the profile's
fixed criteria; the profile skill defines the criteria, score bands, and verdict
thresholds.

Each uses the same runtime prompt-variable mechanism. For example:

```yaml
id: code-reviewer
description: Review an input code repository for concrete engineering issues.

sandbox:
  policy: policy.yaml
  upload: []
  env: []

tasks:
  review-repository:
    required_input: repository
    prompt: prompt-repository.md
    prompt_variables:
      focus:
        description: Files, directories, behavior, or risks that deserve special attention.
        default: Review the complete repository.
      context:
        description: Intent, constraints, non-goals, or maturity that should calibrate the review.
        default: No additional context was provided.
    output_schema: schemas/review.json
    tools: [read, grep, find, ls, bash]
    skills: [skills/review-code]
    extensions: []
```

`tools` is a strict allowlist. OAR recognizes Pi's built-in `bash`, `edit`,
`find`, `grep`, `ls`, `read`, and `write` tools. Custom tools must be declared by
an extension used by the same task:

```yaml
tasks:
  check:
    prompt: prompts/check.md
    tools: [read, custom_check]
    extensions:
      - path: extensions/custom-check.ts
        tools: [custom_check]
```

`oar validate` rejects unknown fields, missing or escaping resources, invalid
schemas, and tools that are not built in or declared by a referenced extension.
The runtime also verifies that Pi actually registered every selected tool before
the first model request.

Prompts support literal runtime substitution. Tasks declare named
`prompt_variables` with optional defaults, and callers override them with a
repeatable `--prompt-var NAME=VALUE`. Variables without defaults are required.
OAR also supplies reserved input metadata such as `{{ oar.input_path }}` and
`{{ oar.input_name }}`. Templates do not execute expressions or shell syntax.

Add `output_schema` to a task when its result must be JSON. OAR exposes the
built-in Pi `submit_result` extension for that task, lets Pi correct invalid
submissions during the session, and validates the downloaded result against the
same Draft 2020-12 schema before publishing it.

## Commands

```text
oar init PROFILE_ROOT --model MODEL_ID [OPTIONS]
oar validate PROFILE_DIRECTORY
oar run PROFILE_DIRECTORY --task TASK --output PATH [OPTIONS]
oar doctor [OPTIONS]
```

- `init` creates editable copies of profiles packaged with OAR.
- `validate` checks a profile and all of its local resources without running it.
- `doctor` performs read-only OpenShell gateway and inference checks.
- `run` launches a task, or prints its resolved operations with `--dry-run`.

Run `oar COMMAND --help` for command options. For task-specific help, select the
profile and task before `--help`:

```bash
uvx --from openshell-agent-runner oar run \
  ./profiles/technical-writing-reviewer --task review-document --help
```

Review a code repository with runtime focus and context:

```bash
uvx --from openshell-agent-runner oar run ./profiles/code-reviewer \
  --task review-repository \
  --gateway openshell \
  --input ./my-project \
  --prompt-var focus="src/auth and tests/auth" \
  --prompt-var context="Pre-release security review" \
  --output /tmp/oar-repository-review.json
```

## Documentation

The [OAR guide](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/)
explains profile inputs, tools and extensions, uploads, the run lifecycle,
structured results, security boundaries, and exit codes.

## Development

From `projects/openshell-agent-runner`:

```bash
make check
make build
```

Run a focused test with `make test PYTEST_ARGS="tests/test_config.py"`. Use
`make clean` to remove generated build and cache files. See
[RELEASING.md](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-agent-runner/RELEASING.md)
for the local PyPI release process.

The `Reviewer profiles end to end` workflow starts an ephemeral OpenShell
gateway and runs each packaged reviewer directly through the OAR CLI against a
representative repository or document input.
