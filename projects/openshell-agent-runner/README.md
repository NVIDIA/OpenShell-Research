# OpenShell Agent Runner

OpenShell Agent Runner (OAR) is based on the
[Pi coding agent](https://github.com/earendil-works/pi/tree/main/packages/coding-agent).
It runs Pi in an isolated OpenShell sandbox and saves the task's result to a
file. Use it to review code, review technical writing, or run your own tasks
from a terminal or CI job.

```text
Profile + input → OAR → Pi in a temporary sandbox → Result file
                            sandbox removed when the run ends
```

A **profile** packages prompts, reusable skills, and settings. A **task** is one
job in that profile; it selects the prompt and skills to use. OAR includes two
ready-to-use reviewers:

| Profile | Task | Input |
| --- | --- | --- |
| `code-reviewer` | `review-repository` | A local project directory |
| `technical-writing-reviewer` | `review-document` | A text file, such as Markdown or `.txt` |

## Get started

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and
OpenShell 0.0.111 or newer, with a running gateway and configured inference.
If OpenShell is not ready, follow its
[quickstart](https://docs.nvidia.com/openshell/latest/get-started/quickstart).
OAR uses that setup to create sandboxes and reach your model.

Pi supplies the agent loop, tools, and model client. Each OAR profile supplies
Pi's `models.json` and `settings.json`; OpenShell supplies the inference route
and provider credentials. The packaged profiles send OpenAI-compatible Chat
Completions requests to `https://inference.local/v1`. Your model and endpoint
must support that API and tool calling. See
[model and inference configuration](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/profiles/#configure-pis-model-and-inference)
for the file formats, reasoning settings, and compatibility options.

**1. Install OAR and check your connection.**

```bash
uv tool install openshell-agent-runner
oar doctor
```

Alternatively, use `uvx --from openshell-agent-runner oar` in place of `oar`.

These commands use your selected OpenShell gateway and its `default` workspace.
For another target, add `--gateway NAME --workspace NAME` to `doctor` and `run`.

**2. Create your profiles.** Replace `YOUR_MODEL_ID` with the model ID shown in
the inference output from `oar doctor`.

```bash
oar init ./profiles --model YOUR_MODEL_ID
```

This creates editable copies of both reviewers and sets the model ID in both
Pi configuration files. For a model without reasoning support, add
`--thinking off`; this also sets the model's `reasoning` flag to `false`.
`init` writes local profiles; configure the inference route through OpenShell.

**3. Review a document.** Replace `./README.md` with an existing text file.

```bash
oar run ./profiles/technical-writing-reviewer \
  --task review-document \
  --input ./README.md \
  --output ./review.json
```

Open `review.json` for the verdict, a score from 0 to 100, and specific findings.
OAR saves the validated result and removes the sandbox. Add `--dry-run` to
preview the operation without launching an agent.

## Review code

Pass a local project directory. Optional `focus` and `context` values help the
reviewer understand what matters for this run:

```bash
oar run ./profiles/code-reviewer \
  --task review-repository \
  --input ./my-project \
  --prompt-var focus="src/auth and tests/auth" \
  --prompt-var context="A small internal tool; keep recommendations proportionate." \
  --output ./code-review.json
```

The agent works on an uploaded copy. Changes in the sandbox stay there; only the
result is downloaded. A `focus` value guides attention but does not limit which
files are uploaded.

## Learn more

- [Get started](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/): setup and your first review.
- [Run reviews](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/reviews/): inputs, focus, context, and scores.
- [Customize profiles](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/profiles/): prompts, variables, skills, and output formats.
- [Command reference](https://nvidia.github.io/OpenShell-Research/documentation/openshell-agent-runner/reference/): options, CI behavior, and troubleshooting.

For help with a specific task:

```bash
oar run ./profiles/code-reviewer --task review-repository --help
```

## Develop OAR

From `projects/openshell-agent-runner`, run:

```bash
make check
make build
```

`make check` includes CLI workflows against a simulated OpenShell. With Docker,
run `make test-runtime` to exercise the real Pi harness without external inference.
For runtime changes, one live integration check runs the installed wheel through
OpenShell and verifies input transfer, prompt variables, structured output, and
sandbox cleanup. It does not grade reviewer opinions.

Use `uv run --frozen oar` in this directory to run the checked-out code instead
of the installed release. Run a focused test with
`make test PYTEST_ARGS="tests/test_config.py"`. See
[RELEASING.md](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-agent-runner/RELEASING.md)
for publishing, and the
[repository CI guide](https://github.com/NVIDIA/OpenShell-Research/blob/main/docs/development/ci.md)
for automated project reviews and the live OAR smoke test.
