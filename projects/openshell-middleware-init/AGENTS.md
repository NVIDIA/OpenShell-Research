# Agent instructions

Read `README.md` and `pyproject.toml` before changing this project.

## Preserve these invariants

- Keep initialization non-destructive. Never merge into, follow, or replace an
  existing output path, including a symlink.
- Build and validate in a hidden sibling staging directory. Publish only after
  all generation and validation steps succeed.
- Preserve reservation ownership checks and atomic no-replace publication.
- Support Linux and macOS explicitly. Do not weaken publication guarantees to
  add another platform implicitly.
- Keep every generated project version-matched: the OpenShell tag, downloaded
  proto, bindings or build configuration, lockfile, and manifest must agree.
- Do not install, replace, or configure the user's OpenShell installation.

## Use the project toolchain

- Use `uv` for this Python project. Treat `pyproject.toml` and `uv.lock` as the
  dependency sources of truth.
- Do not add `requirements.txt` or another dependency export unless a documented
  non-uv consumer requires it.
- Use `uv add` or `uv remove` for dependency changes; do not hand-edit the lock.

## Change templates carefully

- Keep templates under `src/openshell_middleware_init/templates/` runnable as
  standalone projects.
- Use `__UPPER_SNAKE_CASE__` for template markers. Add every marker to
  `TemplateContext.replacements` and cover it with a rendering test.
- Treat generated Python protobuf and gRPC modules as generator-owned. Do not
  format, type-check, or hand-edit them.
- When changing a template, generate the affected language project in isolated
  scratch storage and run its documented checks when practical.

## Test behavior, not implementation details

- Keep initializer unit tests hermetic. Inject protocol downloads and project
  preparation instead of contacting GitHub or invoking uv or Cargo.
- Add regression tests for changes to output safety, failure cleanup, naming,
  manifests, network behavior, or rendered files.
- Use isolated temporary paths for end-to-end generation. Never generate over an
  existing directory.

## Validate every change

Run these commands from this directory:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

Report any command that could not run and why.
