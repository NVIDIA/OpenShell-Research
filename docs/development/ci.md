---
title: Repository CI
description: New-project assessments through OAR, separate from deterministic checks.
---

# Repository CI

## Checks and reviews

| Workflow | Purpose | When it runs |
| --- | --- | --- |
| OAR and PR review checks | Lint, types, tests, wheel installation, offline Pi sessions, and PR selection-to-report integration. No inference credentials. | OAR or review infrastructure changes. |
| OAR pipeline smoke | One installed CLI run through a real gateway; checks input transfer, prompt variables, structured output, and sandbox cleanup. | OAR source, dependencies, smoke fixture, or gateway setup changes on trusted branches. |
| New project review | An advisory assessment of each new project against its purpose and project guidelines, in one updated PR comment. | Eligible new-project PRs, as described below. |
| Existing repository checks | Dependency licenses, source headers, project tests, documentation builds, and previews. | Their existing workflow triggers; independent of OAR reviews. |

Model verdicts are not CI pass/fail expectations. The live smoke checks execution
contracts; it does not evaluate reviewer quality or assess the PR's content.

The first iteration answers: **Does this new project deliver what it claims,
follow our project guidelines, and use an appropriate level of engineering?**

## Contributor workflow

1. Follow the [project guidelines](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/PROJECT_GUIDELINES.md).
2. Add a project under `projects/<name>/`, including a root `project.yaml` with
   the required `kind` field.
3. Open a non-draft PR from a branch in this repository.
4. Read the automated assessment in one updated PR comment. Fix useful findings;
   humans decide whether the project is ready.

| `kind` | Task | Review emphasis |
| --- | --- | --- |
| `tool` | `review-tool` | Correctness, realistic failure handling, maintainability, verification, usability. |
| `research-spike` | `review-research-spike` | Method, evidence, reproducibility, limitations, proportionate implementation. |
| `use-case-example` | `review-use-case-example` | Working workflow, reproducibility, instructional clarity, safe configuration, appropriate scope. |

A project is new when its directory does not exist in the PR's base revision.
Later commits on its introducing PR reassess the whole project, including its
README and documentation. Multiple new projects receive separate assessments in
the same comment. Renaming an existing project to a new directory counts as an
addition. Missing or invalid kinds fail selection before inference and are
reported with the file to fix; no project in that request runs until corrected.

Existing-project changes, Dev Notes, and unrelated repository changes do not
start live reviews. There is no central registry or metadata backfill.

## How the pieces fit

```text
New-project PR
  └─ Select new directories and read project.yaml
       └─ One ci-reviewer profile
            ├─ Shared prompt + common review skill
            ├─ One kind-specific skill
            └─ Trusted PROJECT_GUIDELINES.md
                 └─ oar run once per project
                      └─ Validated JSON → one current-revision PR comment

Native checks + offline integration ─── execution and policy checks
One live OAR pipeline check ─────────── Actions summary, not a PR assessment
```

The result includes an overall verdict, five fixed criterion scores (0–100,
100 best), their rounded mean, findings, strengths, and limitations.
`guidelines_assessment` separately records a verdict and evidence-based
explanation covering applicable requirements and material verification gaps.
Findings cite specific guideline violations; they do not invent requirements.

Verdicts are `pass` (no material change needed), `needs_changes` (a demonstrated
issue needs correction), and `inconclusive` (insufficient evidence). A high score
does not hide guideline violations or force a passing verdict. Scores and
findings are advisory; native checks remain separate merge gates.

## Execution and trust

`New project review` runs on PR opening, reopening, new commits, and readiness.
Draft, fork, and Dependabot PRs skip live review. There is no manual fork
authorization or waiting for other workflows.

- Tooling, profile, and guidelines come from one pinned default-branch revision.
  Proposed guideline edits cannot change a PR's own review requirements.
- PR code is checked out only as data. The snapshot preserves committed bytes,
  excludes Git metadata, removes symlinks, and records omitted symlinks/submodules.
  The host never runs contributor setup hooks or project code with inference secrets.
- The sandbox receives the selected project, repository context, diff, and a
  separately uploaded trusted guidelines file. Missing evidence is reported,
  not treated as verified compliance.
- One ephemeral gateway serves sequential reviews. Each direct `oar run` gets
  a fresh sandbox and a 600-second timeout; the job has a 45-minute limit.
  Completed results survive later failures. Unfinished projects are visible.
- A separate reporter can write comments but has no inference secrets. It
  checks that the PR is open and its head is current before updating the
  comment. Older runs cannot overwrite a newer report. JSON and logs are
  retained as Actions artifacts for 14 days.

The workflow must first land on the default branch before it can review actual
project additions. Before that, offline integration tests exercise selection,
snapshot preparation, the real OAR CLI, and report creation/update together,
with simulated GitHub and OpenShell boundaries. This does not claim a live
GitHub comment has been tested. The separate smoke verifies real OpenShell
execution using the candidate wheel.

## Setup

Set these repository Actions secrets:

- `INFERENCE_API_KEY`: inference provider credential.
- `INFERENCE_BASE_URL`: OpenAI-compatible provider endpoint.
- `MODEL_ID_TOP`: model accepted by that endpoint.

The shared gateway action installs pinned OpenShell v0.0.116, starts an ephemeral
gateway, and configures inference. Credentials stay with gateway configuration,
not the sandbox or comment reporter. No persistent gateway is required.

Dependency-license checks are already part of the repository's CI. They neither
use OAR nor coordinate with this reviewer; see [Dependency License Checks](dependency-licenses.md).

## Validation and evolution

CI scripts are Python; requests use PyYAML from OAR's locked environment and
GitHub calls use the runner's `gh api`. Run from the repository root:

```sh
uv run --project projects/openshell-agent-runner pytest \
  tests/test_ci_scope.py tests/test_github_api.py tests/test_*review*.py
```

Run `make check` and `make build` from the OAR project directory. `make check`
includes CLI workflows against a simulated OpenShell; `make test-runtime` uses
Docker to exercise real Pi sessions against local scripted inference, with no
external network or credentials. CI runs both, plus the Pi extension SDK checks.

The live smoke workflow (`.github/workflows/oar-smoke.yml`) installs the built
wheel, configures a small task from `tests/fixtures/pipeline-smoke/`, and calls
`oar run` directly. The agent reads an uploaded file and returns its contents
plus a runtime prompt variable in a schema-validated result. CI checks those
values and confirms that no sandbox remains on its dedicated gateway.

The task has a five-minute timeout and the job has a 15-minute limit.
Input, result, log, and sandbox inventory are uploaded as
the `oar-pipeline-smoke` artifact. The workflow writes an Actions summary,
never a PR comment. No clean/flawed reviewer experiments run in CI.

Keep team expectations in the project guidelines. Change review judgment in the
common or kind-specific skill, and change selection/reporting only when the
workflow needs it. Existing-project and document-only review, generic project
test orchestration, extra profiles, and new example projects are out of scope.
