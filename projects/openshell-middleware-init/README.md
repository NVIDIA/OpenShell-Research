# OpenShell Middleware Init

`openshell-middleware-init` is a Typer-based Python CLI that creates runnable
OpenShell supervisor middleware projects in Python or Rust. Every generated
project uses the protocol contract from one explicit OpenShell release and
records its source and SHA-256 in a manifest.

The generated service is a small pass-through implementation of all three
`SupervisorMiddleware` RPCs: `Describe`, `ValidateConfig`, and
`EvaluateHttpRequest`. It provides the transport and build boilerplate without
guessing the middleware's policy or domain behavior.

## Install for development

Install [uv](https://docs.astral.sh/uv/), then create the locked environment:

```sh
uv sync --locked
```

## Generate a project

Every material choice is explicit:

```sh
uv run openshell-middleware-init audit-headers \
  --language python \
  --openshell-version v0.0.86
```

For Rust:

```sh
uv run openshell-middleware-init audit-headers \
  --language rust \
  --openshell-version v0.0.86 \
  --output ../audit-headers
```

Use `--openshell-version latest` to resolve the current release. For shared
middleware, prefer a pinned tag so regeneration remains reproducible.

Python package names default to a normalized form of the project name
(`audit-headers` becomes `audit_headers`) and can be overridden:

```sh
uv run openshell-middleware-init audit-headers \
  --language python \
  --openshell-version v0.0.86 \
  --package-name request_auditor
```

Run `uv run openshell-middleware-init --help` for the complete interface.

## What generation does

The initializer:

1. resolves and downloads `proto/supervisor_middleware.proto` from the selected
   OpenShell release;
2. renders a runnable pass-through service, tests, development configuration,
   and registration guidance;
3. generates package-safe Python protobuf/gRPC modules or configures Rust Tonic
   generation with a bundled `protoc`;
4. creates `uv.lock` or `Cargo.lock` and compile/import-checks the project;
5. writes `middleware-dev-manifest.json`; and
6. publishes the project only after validation succeeds.

Generation happens in a hidden sibling staging directory. The final output must
not already exist, and a reservation directory prevents concurrent initializers
from publishing to the same path. A failed run removes its own staging and
reservation data and does not merge into an existing project.

Unlike the original `middleware_dev_setup` spike, this project initializer does
not install or replace OpenShell. Install the desired OpenShell release through
its official installer separately.

## Compatibility with `middleware_dev_setup`

The initializer deliberately carries forward the spike's observable generation
rules: explicit language and OpenShell version selection; `latest` and bare-tag
normalization; toolchain preflight before network or output changes; one initial
download attempt plus three retries; a destination that must not exist; pinned
protocol download and SHA-256 manifest; `grpcio-tools==1.81.1` with package-safe
imports; the Rust 1.90 / edition 2024 Tonic stack with bundled `protoc`; locked
dependencies; compile/import validation; and hidden sibling staging with a
per-output reservation.

The intentional differences are scoped to making one runnable middleware
project rather than a bindings workspace:

| Difference | Reason |
| --- | --- |
| One of `python` or `rust`, rather than `all` or `none` | A generated directory is one runnable, conventional language project. Run the initializer twice when both implementations are wanted. |
| Project files at the output root, rather than nested `python/` or `rust/` directories | The output is directly usable with `uv` or Cargo and can become its own repository. |
| Required project name and optional `--output` | The name is stable project identity; output defaults to that name but remains overridable. Python import names derive from project identity, so moving the directory cannot silently rename the package. |
| No OpenShell install or replacement flags | Project initialization stays local and never mutates a developer's system installation. OpenShell installation remains a separate, explicit operation. |
| Python standard-library HTTP and hashing rather than requiring `curl` and a SHA utility | This removes unrelated host-tool prerequisites while preserving pinned-source, retry, and digest behavior. |
| Runnable service, tests, and development configuration | The CLI produces middleware boilerplate, not only generated protocol bindings. |
| Native atomic no-replace publication | This strengthens the spike's final absence check by preventing a concurrent writer from being overwritten. It is why Linux and macOS are the explicitly supported hosts. |

The manifest retains the spike's fields and adds a `generator` object for CLI
provenance.

### Recover a stale reservation

A process killed without cleanup can leave
`.<output>.openshell-middleware-init.lock` and its hidden staging directory. The
initializer deliberately leaves ambiguous state in place instead of guessing
that it is stale.

1. Read the reservation's `metadata.json`. It records the hostname, PID, start
   time, target version, final output, and staging output.
2. On the recorded host, confirm that the PID is no longer an
   `openshell-middleware-init` process. Account for PID reuse by comparing the
   process start time and command. Confirm that the final output still does not
   exist.
3. Inspect the recorded staging directory and preserve anything needed for
   diagnosis. Remove it only after confirming the initializer is no longer
   active.
4. Remove only `owner` and `metadata.json` from the reservation, then remove the
   empty reservation directory with `rmdir`. If it contains any other entry,
   stop and investigate rather than deleting recursively.
5. Run the initializer again.

## Requirements

- Linux or macOS. The initializer uses POSIX directory descriptors and native
  no-replace rename operations to preserve its non-destructive publication
  guarantee.
- All generation: network access to GitHub and the selected OpenShell release.
- Python output: `uv`.
- Rust output: Cargo and a toolchain compatible with Rust 1.90 / edition 2024.

## Develop this CLI

Run the full local gate from this directory:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

`src/openshell_middleware_init/templates/` contains the generated project
assets. Tests use local protocol fixtures and do not contact GitHub or invoke
language package managers.
