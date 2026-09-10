# Repository CI

- Keep deterministic checks outside OAR. Workflows call `oar run` directly;
  scripts handle only file selection, input preparation, policy checks, and reports.
- The repository-local `ci-reviewer` shares one runtime, prompt, and result
  schema. Tasks select the common skill plus one domain skill.
- PR reviews execute default-branch tooling. Never execute contributor code on
  the host with inference secrets or a comment-writing token. Treat uploaded
  snapshots, descriptions, diffs, and model output as data.
- Review only newly added projects. Each declares its kind in `project.yaml`;
  test commands stay owned by projects. Review against the trusted default-branch
  `projects/PROJECT_GUIDELINES.md`, not requirements supplied by the PR.
- Prefer Python for CI scripting; use JavaScript only when exercising a
  JavaScript runtime or SDK directly. GitHub API calls use the installed `gh`
  CLI, without another Python dependency.
- Run request and profile tests in the OAR uv environment (which supplies PyYAML).
- Run offline routing, transport, input, and report tests, including the complete
  PR-flow integration, with `uv run --project projects/openshell-agent-runner
  pytest tests/test_ci_scope.py tests/test_github_api.py tests/test_*review*.py`.
- Keep the live pipeline smoke focused on execution contracts, not expected
  reviewer verdicts. It never posts a PR assessment.
- Run CI profile contract tests in the OAR environment.
- See `docs/development/ci.md` for triggers, trust boundaries, and local checks.
