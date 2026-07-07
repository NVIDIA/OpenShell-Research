# Python Project Template

A minimal Python package template for OpenShell research projects. It uses a
`src` layout, uv for dependency and environment management, Ruff for linting and
formatting, ty for type checking, and pytest for tests and coverage.

## Start a project from this template

Copy this directory, then update the distribution name, import package,
description, and command entry point in `pyproject.toml`. Keep `uv.lock` and the
generated `requirements.txt` committed.

## Develop

Install [uv](https://docs.astral.sh/uv/), then create the locked development
environment:

```sh
uv sync --locked
```

Run the example command:

```sh
uv run python-project-template --name OpenShell
```

Run all local checks:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

## Manage dependencies

Use `uv add <package>` for runtime dependencies and `uv add --dev <package>` for
development tools. uv updates `pyproject.toml` and `uv.lock` together.

nSpect needs a resolved dependency manifest it can inspect. `uv.lock` remains
the source of truth for installs, while `requirements.txt` is a generated,
hash-pinned export of runtime dependencies for the scanner. Do not edit the
export by hand. Regenerate it after every runtime dependency update:

```sh
uv export \
  --format requirements.txt \
  --no-dev \
  --no-emit-project \
  --locked \
  --output-file requirements.txt
```

CI and deployments should install from the lockfile with `uv sync --locked` or
`uv sync --frozen`, never resolve dependencies afresh.
