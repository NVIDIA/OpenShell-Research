# Project guidelines

Use these guidelines when adding a project under `projects/<name>/`. They are
the shared expectations for contributors and the automated new-project reviewer.
The goal is useful, reproducible work at the right level of engineering—not a
production checklist for every experiment.

## Declare the project kind

Add `project.yaml` in the project root with one required field, `kind`. Choose
the primary purpose; a project does not need to satisfy all three categories.

| `kind` | Purpose |
| --- | --- |
| `tool` | A reusable tool or library intended for other code or people to use. |
| `research-spike` | An investigation intended to answer a question or test an idea. |
| `use-case-example` | An end-to-end demonstration that teaches a concrete workflow. |

Existing projects do not need to be backfilled for this first CI iteration.

## Guidelines for every new project

- **Purpose and scope.** Explain in the README what the project does, who it
  serves, how it uses OpenShell, and its important limitations or non-goals.
  Distinguish working behavior from plans and unverified claims.
- **Self-contained layout.** Keep implementation, dependencies, and runtime
  notes in the project directory. Keep project-specific guides in its `docs/`
  tree when a README is no longer sufficient; do not add empty documentation
  scaffolding. Link directly relevant existing repository documentation.
- **Reproducible first run.** Document prerequisites, setup, the smallest useful
  run command, and how to recognize the expected output. Disclose required
  hardware, services, credentials, restricted inputs, and meaningful costs.
  Do not claim a workflow was exercised when only a substitute was checked.
- **Evidence and verification.** Supply verification appropriate to the claims:
  focused tests, a runnable check, or recorded experimental evidence with a
  repeatable procedure. State what has not been verified. A coverage percentage
  or comprehensive test framework is not required.
- **Dependencies and configuration.** Declare dependencies in the project's
  normal manifest and lockfile. Python projects use `uv`, `pyproject.toml`, and
  a committed `uv.lock`, unless an exception is explicitly documented. Keep
  secrets out of committed files; document needed variables in `.env.example`.
  Include required license and attribution notices. Automated dependency-license
  checks are separate; the reviewer must not invent a new license policy.
- **Proportionate engineering.** Prefer direct, understandable code. Handle
  realistic failures and actual trust boundaries. Do not add speculative
  abstractions, compatibility layers, excessive defensive checks, or unrelated
  features. Follow the repository's coding and content-routing conventions;
  Python uses absolute imports. Add a nested `AGENTS.md` only when the project
  has distinct conventions that agents need to follow.

## Additional expectations by kind

| Kind | What should be present | What is not required by default |
| --- | --- | --- |
| Tool / library | A usable installation and CLI/API path; clear inputs, outputs, and failures; focused verification of critical behavior. | A plugin framework, multiple integrations, exhaustive validation, or production service architecture. |
| Research spike | A clear question and method; evidence supporting conclusions; enough environment, input, and execution detail to repeat the experiment; honest uncertainty and limitations. | Positive results, production packaging, stable APIs, or tests unrelated to experimental validity. Seeds and baselines matter only when the claim depends on them. |
| Use case example | A concrete audience and scenario; a coherent start-to-finish workflow; setup and expected outputs; safe configuration and clear distinction between demonstration shortcuts and deployable behavior. | A reusable framework, coverage of every possible scenario, or duplicating the underlying tools' documentation. |

## How review uses these guidelines

The reviewer assesses every applicable guideline and the selected kind's
expectations alongside whether the project delivers its claims. It reports
guideline compliance explicitly, cites concrete gaps in its findings, and
discloses requirements it could not verify. Inapplicable requirements are not
defects. Recommendations must stay within the project's purpose and maturity.

Review scores are advisory, not a replacement for deterministic checks or human
judgment. CI uses the version of these guidelines on the default branch, so a PR
cannot relax its own review requirements. See the
[CI guide](../docs/development/ci.md) for execution and reporting details.
