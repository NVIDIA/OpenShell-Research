# Privacy Guard OpenShell E2E example

This example proves the complete local path:

```text
sandbox curl
  -> OpenShell network policy and HTTP middleware
  -> Privacy Guard gRPC service
  -> reconstructed request
  -> host capture endpoint
```

It uses no provider credentials or paid API. The sandbox sends a provider-shaped
JSON request to a local capture endpoint. Privacy Guard uses its explicit
development-only deterministic scanner to redact `user@example.com`, and verifies the
captured request body after OpenShell forwards it.

## Prerequisites

- macOS with the Homebrew OpenShell `0.0.86` gateway running
- Docker Desktop running with
  pinned `ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e` available locally
- `brew`, `docker`, `openshell`, `python3`, and `uv`
- ports `50051` and `18080` available on the host

Run:

```bash
./projects/privacy-guard/examples/openshell-e2e/run.sh
```

The pytest hook is skipped by default. From the repository root, run the same
state-mutating harness through the integration suite only with:

```bash
PRIVACY_GUARD_RUN_OPEN_SHELL_E2E=1 \
  uv run --project projects/privacy-guard pytest -q \
  projects/privacy-guard/tests/examples/test_openshell_e2e.py
```

The harness temporarily appends a `privacy-guard-e2e` registration to
`~/.config/openshell/gateway.toml`, restarts the gateway, creates a disposable
sandbox, and restores the exact prior gateway configuration on exit. Existing
gateway configuration is preserved.

If the default `en0` address is not reachable from Docker, provide the host IPv4
address explicitly:

```bash
PRIVACY_GUARD_E2E_HOST_ADDRESS=192.168.1.10 \
  ./projects/privacy-guard/examples/openshell-e2e/run.sh
```

The default reference is the locally verified immutable digest used during this
spike. Override the sandbox image when necessary with
`PRIVACY_GUARD_E2E_SANDBOX_IMAGE`. The image must already exist in the local
Docker daemon and provide `/usr/bin/curl`.

## Interrupted-run recovery

Normal failures and signals trigger automatic cleanup. If the process is killed
without running its trap, inspect these files before restarting OpenShell:

- `~/.config/openshell/gateway.toml.privacy-guard-e2e.bak` means the original
  configuration should replace `gateway.toml`.
- `~/.config/openshell/gateway.toml.privacy-guard-e2e.absent` means no user
  configuration existed before the run; remove both the marker and the temporary
  `gateway.toml`.

Then run `brew services restart openshell` and verify `openshell status`.
