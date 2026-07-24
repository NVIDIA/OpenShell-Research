# OpenShell Research

## Purpose

This repository contains research engineering projects, Dev Notes, and durable
documentation for work that uses OpenShell as its runtime.

## Work routing

- Put self-contained implementations and experiments in `projects/<name>/`.
- Put durable user-facing guides and references in `docs/documentation/`.
- Put Dev Notes (human-written technical notes) `docs/dev-notes/`.
- Put agent-facing repository maintenance workflows in `docs/development/`.

Before changing a project, read that project's `README.md` and `pyproject.toml`;
projects are self-contained and may have different platforms, dependencies, and
validation commands. Before changing anything under `docs/` or `zensical.toml`,
read `docs/development/index.md`.

## Repository rules

- Make the smallest change that satisfies the task and preserve unrelated work.
- Prefer explicit, clear names and language over concise but ambiguous
  alternatives. Value concision when it does not reduce clarity.
- Use `uv` for Python dependency management, environments, locking, builds, and
  command execution unless a project explicitly documents an exception. Treat
  `pyproject.toml` and the committed `uv.lock` as the dependency sources of truth.
- Do not add `requirements.txt` or another generated dependency export by
  default. Commit one only when a named non-uv consumer requires it and that
  workflow is documented; regenerate exports with `uv`, never by hand.
- Never commit credentials or populated `.env` files. Document configuration in
  `.env.example`.
- Do not hand-edit generated Dev Notes cards, bylines, or navigation; use
  `python3 scripts/render-dev-notes.py`.
- When adding a project or workflow with distinct conventions, add a nested
  `AGENTS.md` in that directory instead of expanding this root file.

## Validation

- For project changes, run the checks documented by that project's README from
  the project directory.
- For documentation changes, run `python3 tests/test_render_dev_notes.py` and
  `scripts/build-docs.sh`, then serve the generated site as described in
  `docs/development/index.md`.
- Report the checks run and any checks that could not be completed.
