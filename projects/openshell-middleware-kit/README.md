# OpenShell Middleware Kit

`openshell-middleware-kit` creates and updates Python or Rust services for OpenShell
supervisor middleware. Each new project starts as a working pass-through gRPC
service. It includes the protocol file for one OpenShell release, tests,
dependency locks, and instructions for registering the service.

The CLI does not install or change OpenShell.

## Requirements

- Linux or macOS
- [uv](https://docs.astral.sh/uv/)
- Network access to GitHub and the selected OpenShell release
- For Rust projects: Cargo and a Rust 1.90-compatible toolchain

## Install the CLI

Install `omkit` from GitHub with `uv`:

```sh
uv tool install \
  "openshell-middleware-kit @ git+https://github.com/NVIDIA/OpenShell-Research.git#subdirectory=projects/openshell-middleware-kit"
```

If you already have this repository checked out, install from its local path
instead:

```sh
uv tool install /path/to/OpenShell-Research/projects/openshell-middleware-kit
```

Both commands install `omkit` for use outside this repository.

To work on the CLI itself, use the locked project environment:

```sh
uv sync --locked
uv run omkit --help
```

## Quick start

Generate and run a Python starter with the installed command:

```sh
omkit create audit-headers \
  --language python \
  --openshell-version v0.0.86 \
  --output /tmp/audit-headers

cd /tmp/audit-headers
uv run pytest
uv run audit-headers
```

Or generate and run a Rust starter:

```sh
omkit create audit-headers \
  --language rust \
  --openshell-version v0.0.86 \
  --output /tmp/audit-headers-rust

cd /tmp/audit-headers-rust
cargo test --locked
cargo run --locked -- 127.0.0.1:50051
```

The output path must not exist. Pin an OpenShell tag when you need repeatable
builds. Use `--openshell-version latest` when you want the newest release.

Run `omkit --help` for all options. By default, `omkit` derives the Python package
name from the project name. Use `--package-name` to set it yourself.

## Update a project

Run this inside a generated project to use the latest OpenShell release:

```sh
omkit update
```

To choose a release or update a project in another directory:

```sh
omkit update /path/to/audit-headers \
  --openshell-version v0.0.90
```

`omkit update` reads `.openshell-middleware-manifest.json` to find the project language
and Python package. It downloads the selected `supervisor_middleware.proto`,
regenerates Python protobuf and gRPC bindings when needed, updates `uv.lock` or
`Cargo.lock`, and writes the version and protocol checksum to the manifest.
The manifest must name `openshell-middleware-kit` as its generator.

## What you get

Each project contains:

- a pass-through implementation of `Describe`, `ValidateConfig`, and
  `EvaluateHttpRequest`;
- the exact `supervisor_middleware.proto` from the selected OpenShell release;
- generated Python gRPC bindings or Rust Tonic build configuration;
- tests and lint/type-check configuration;
- `uv.lock` or `Cargo.lock`; and
- `.openshell-middleware-manifest.json` with the release, source URL, and protocol
  checksum.

Start by implementing policy behavior in the generated `validate_config` and
`evaluate_http_request` functions. The generated README explains how to run the
service and register it with OpenShell.

## How `omkit` protects your files

`omkit create` builds and checks the project in a temporary directory next to
the output path. It moves the finished project into place only after every
check passes. If the output path already exists, including as a symlink, the
command stops without changing it.

`omkit update` works on a temporary copy of the project. It changes only the
protocol, generated bindings or Rust build files, lockfile, and manifest. It
runs the project checks before replacing those files. Your implementation files
stay unchanged. If a file replacement fails, `omkit` restores the files it
already replaced.

A lock prevents two `omkit` processes from changing the same path at once.
Normal failures remove the lock and temporary files. If an update and its
rollback both fail, `omkit` keeps the recovery files and prints their locations.

If the process is killed, it may leave a `.<output>.openshell-middleware-kit.lock`
directory and a temporary project directory. Clean them up as follows:

1. Open `metadata.json` in the lock directory.
2. On the host listed in that file, check that the listed PID is no longer an
   `omkit` process.
3. For `create`, also check that the requested output path does not exist.
   Never remove the project directory after an interrupted `update`.
4. Inspect the temporary directory listed in `metadata.json`, then remove only
   that directory.
5. Remove `owner` and `metadata.json`. Use `rmdir` to remove the empty lock
   directory. Stop if the lock directory contains any other files.

## Develop the CLI

Run these checks from this directory:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build
```

Unit tests use local protocol fixtures. They do not contact GitHub or run `uv`
or Cargo inside generated projects.
