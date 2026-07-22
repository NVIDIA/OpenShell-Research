# OpenShell Middleware Init

`openshell-middleware-init` creates a runnable Python or Rust starter for an
OpenShell supervisor middleware service. The starter implements the complete
gRPC service as a pass-through, pins its protocol contract to one OpenShell
release, and includes tests, dependency locks, and registration guidance.

The initializer does not install or replace OpenShell.

## Requirements

- Linux or macOS
- [uv](https://docs.astral.sh/uv/)
- Network access to GitHub and the selected OpenShell release
- For Rust projects: Cargo and a Rust 1.90-compatible toolchain

## Quick start

From this directory, install the CLI's locked development environment:

```sh
uv sync --locked
```

Generate and run a Python starter:

```sh
uv run openshell-middleware-init audit-headers \
  --language python \
  --openshell-version v0.0.86 \
  --output /tmp/audit-headers

cd /tmp/audit-headers
uv run pytest
uv run audit-headers
```

Or generate and run a Rust starter:

```sh
uv run openshell-middleware-init audit-headers \
  --language rust \
  --openshell-version v0.0.86 \
  --output /tmp/audit-headers-rust

cd /tmp/audit-headers-rust
cargo test --locked
cargo run --locked -- 127.0.0.1:50051
```

The output path must not already exist. Use a pinned OpenShell tag for
reproducible projects; `--openshell-version latest` is available for
experimentation.

Run `uv run openshell-middleware-init --help` for all options. Python package
names default to a normalized project name and can be changed with
`--package-name`.

## What you get

Each generated project contains:

- a pass-through implementation of `Describe`, `ValidateConfig`, and
  `EvaluateHttpRequest`;
- the exact `supervisor_middleware.proto` from the selected OpenShell release;
- generated Python gRPC bindings or Rust Tonic build configuration;
- tests and lint/type-check configuration;
- `uv.lock` or `Cargo.lock`; and
- `middleware-dev-manifest.json` with the release, source URL, and protocol
  SHA-256.

Start by implementing policy behavior in the generated `validate_config` and
`evaluate_http_request` functions. The generated README explains how to run the
service and register it with OpenShell.

## Safety and failure behavior

Generation is non-destructive. The initializer validates a hidden sibling
staging directory, then publishes it atomically. It refuses an existing output,
including a symlink, and uses a per-output reservation to prevent concurrent
writers. A normal failure removes the initializer's own staging and reservation
without publishing a partial project.

If the process is killed, it may leave
`.<output>.openshell-middleware-init.lock` and a hidden staging directory. Before
removing either one:

1. Read `metadata.json` in the reservation.
2. On the recorded host, confirm that the recorded PID is no longer the same
   initializer process and that the final output does not exist.
3. Inspect and remove only the recorded staging directory.
4. Remove `owner` and `metadata.json`, then remove the empty reservation with
   `rmdir`. Stop if it contains anything unexpected.

## Develop the CLI

Run the complete local gate from this directory:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

Unit tests are hermetic: they use local protocol fixtures and do not contact
GitHub or invoke uv or Cargo for generated projects.
