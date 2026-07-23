# OpenShell Middleware Kit

`middleware-kit` creates and updates runnable Python or Rust OpenShell
supervisor middleware services. A new project implements the complete gRPC
service as a pass-through, pins its protocol contract to one OpenShell release,
and includes tests, dependency locks, and registration guidance.

The project tool does not install or replace OpenShell.

## Requirements

- Linux or macOS
- [uv](https://docs.astral.sh/uv/)
- Network access to GitHub and the selected OpenShell release
- For Rust projects: Cargo and a Rust 1.90-compatible toolchain

## Install the CLI

Install the command in an isolated tool environment from GitHub:

```sh
uv tool install \
  "middleware-kit @ git+https://github.com/NVIDIA/OpenShell-Research.git#subdirectory=projects/middleware-kit"
```

If you already have this repository checked out, install from its local path
instead:

```sh
uv tool install /path/to/OpenShell-Research/projects/middleware-kit
```

Both forms make `mkit` available outside the source tree
without running `uv sync` in this project.

Contributors working on the CLI should use the locked project environment:

```sh
uv sync --locked
uv run mkit --help
```

## Quick start

Generate and run a Python starter with the installed command:

```sh
mkit create audit-headers \
  --language python \
  --openshell-version v0.0.86 \
  --output /tmp/audit-headers

cd /tmp/audit-headers
uv run pytest
uv run audit-headers
```

Or generate and run a Rust starter:

```sh
mkit create audit-headers \
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

Run `mkit --help` for all options. Python package names
default to a normalized project name and can be changed with `--package-name`.

## Update a project

From a generated project, refresh to the latest OpenShell release:

```sh
mkit update
```

To select a release or update a project from another directory:

```sh
mkit update /path/to/audit-headers \
  --openshell-version v0.0.90
```

The update command reads `middleware-dev-manifest.json` to discover the
project language and Python package. It downloads the selected
`supervisor_middleware.proto`, regenerates Python protobuf and gRPC bindings
when applicable, refreshes `uv.lock` or `Cargo.lock`, and records the new
version and protocol checksum in the manifest. Projects created under the
former `middleware-project` and `openshell-middleware-init` names are supported.

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

Creation is non-destructive. The project tool validates a hidden sibling
staging directory, then publishes it atomically. It refuses an existing output,
including a symlink. Updates copy the complete existing project into a hidden
sibling staging directory, change only generator-owned protocol artifacts
there, validate the staged project, then atomically exchange those artifacts
in place. User implementation files and the project directory itself are
preserved. If publication fails, completed exchanges are rolled back in reverse
order. Both operations use a per-project reservation to prevent concurrent
writers; a normal failure removes the tool's own staging and reservation
without leaving partial changes.

If the process is killed, it may leave
`.<output>.middleware-kit.lock` and a hidden staging directory. Before
removing either one:

1. Read `metadata.json` in the reservation.
2. On the recorded host, confirm that the recorded PID is no longer the same
   project tool process. For a create operation, confirm that the final output
   does not exist. For an update, do not remove the final project.
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
