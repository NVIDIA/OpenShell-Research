# Agent instructions

Read `README.md` and `pyproject.toml` before changing this project.

## Safety rules

- `mkit create` must never write into, follow, or replace an existing output
  path, including a symlink.
- Build and check the project in a temporary directory next to its destination.
  Move it into place only after every check passes.
- Check lock ownership before writing. Creation must not overwrite an existing
  path. Updates must swap each generated file atomically and undo earlier swaps
  if one fails.
- Support only Linux and macOS. Do not add a platform unless it can provide the
  same file-safety guarantees.
- Keep the OpenShell tag, downloaded proto, bindings or Rust build files,
  lockfile, and manifest on the same version.
- Never install, replace, or configure OpenShell.

## Dependencies

- Use `uv`. `pyproject.toml` and `uv.lock` define the dependencies.
- Do not add `requirements.txt` or another dependency export unless a documented
  tool needs one.
- Use `uv add` or `uv remove` to change dependencies. Do not edit `uv.lock` by
  hand.

## Templates

- Templates in `src/middleware_kit/templates/` must produce working standalone
  projects.
- Write template markers as `__UPPER_SNAKE_CASE__`. Add each marker to
  `TemplateContext.replacements` and test its rendered value.
- Do not format, type-check, or edit generated Python protobuf and gRPC files.
- After changing a template, generate a project in a temporary directory and
  run its documented checks when practical.

## Tests

- Unit tests must not contact GitHub or run `uv` or Cargo in generated projects.
  Pass test doubles for downloads and command execution.
- Add regression tests when changing file safety, failure cleanup, names,
  manifests, network handling, or generated files.
- Run end-to-end generation in a new temporary directory. Never generate over
  an existing directory.

## Checks

Run these commands from this directory:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

Report any command that could not run and why.
