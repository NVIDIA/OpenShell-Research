---
title: Privacy Guard
description: Overview and quickstart for running Privacy Guard with OpenShell.
agent_markdown: true
---

# Privacy Guard

Privacy Guard is an OpenShell supervisor middleware. It examines provider-bound
HTTP request bodies before OpenShell attaches provider credentials. A policy can
instruct Privacy Guard to:

| Action | Result when entities are detected |
| --- | --- |
| `detect` | Allow the original body and report bounded findings |
| `block` | Deny the request and report bounded findings |
| `replace` | Allow a body produced by the configured replacement engines and report bounded findings |

Privacy Guard processes the complete request body as UTF-8 text. It does not
parse JSON fields, inspect files in the sandbox, modify provider responses, or
send network requests to the provider.

## How it fits into OpenShell

![A provider-bound request travels from the sandbox through OpenShell and
Privacy Guard. Privacy Guard returns an allow, replacement, or deny decision
before OpenShell attaches credentials and sends an allowed request to the
provider.](../../assets/privacy-guard/diagrams/request-path.svg)

Privacy Guard exposes OpenShell's `SupervisorMiddleware` gRPC service. It
registers for the pre-credentials request phase so replacement happens before
the provider receives the request.

## Quickstart

This quickstart uses the built-in `RegexEngine` example to replace an email
address and customer ID. It requires:

- Python 3.11 or newer
- `uv` 0.11 or newer
- OpenShell and `openshell-gateway` at the version recorded by the Privacy
  Guard project
- a Docker or Podman backend supported by OpenShell

Run the commands from a checkout of OpenShell Research.

### 1. Prepare the example

```bash
cd projects/privacy-guard/examples/regex-engine
uv sync --locked
uv run privacy-guard engines
```

The engine list should contain:

```text
regex    detect,replace
```

The example files are:

| File | Purpose |
| --- | --- |
| `patterns.yaml` | Email and customer-ID rules |
| `privacy-guard-config.yaml` | Standalone Privacy Guard policy configuration |
| `policy.yaml` | Complete OpenShell sandbox policy with the same configuration |

### 2. Start Privacy Guard

From the example directory:

```bash
uv run privacy-guard serve --listen 0.0.0.0:50051
```

Keep the process running. The development server uses plaintext gRPC and
receives request bodies. Restrict port 50051 to the host and trusted sandbox
network.

### 3. Register Privacy Guard with the gateway

Choose a non-loopback host IPv4 address that both the gateway and sandbox
supervisor can reach:

```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I
```

Create a local gateway configuration:

```bash
export PRIVACY_GUARD_HOST_IP=YOUR_HOST_IPV4

uv run privacy-guard configure-gateway \
  --host-ip "$PRIVACY_GUARD_HOST_IP" \
  --name privacy-guard-regex \
  --config gateway.local.toml
```

Do not use `127.0.0.1`: loopback inside the sandbox supervisor does not refer to
the host.

Restart the local gateway with `gateway.local.toml`. For a Homebrew
installation:

```bash
brew services stop openshell
export OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/homebrew/tls"
openshell-gateway --config "$PWD/gateway.local.toml"
```

For a Debian or RPM installation:

```bash
systemctl --user stop openshell-gateway
export OPENSHELL_LOCAL_TLS_DIR="$HOME/.local/state/openshell/tls"
openshell-gateway --config "$PWD/gateway.local.toml"
```

Keep the foreground gateway running.

### 4. Create a sandbox

In another terminal, return to the example directory and verify the gateway:

```bash
openshell gateway select openshell
openshell status
```

Create the example sandbox:

```bash
openshell sandbox create \
  --name privacy-guard-regex \
  --from base \
  --no-auto-providers \
  --policy "$PWD/policy.yaml" \
  -- env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude
```

After authenticating Claude Code, submit:

```text
Draft a short greeting for user@example.com about customer CUST-12345678.
```

The provider-bound body should contain `[email]` and `[customer-id]` instead of
the original values.

### 5. Verify the result

From a host terminal:

```bash
openshell logs privacy-guard-regex -n 100 --source sandbox
```

Find the provider request and confirm:

- `transformed:true`
- an `email (identifiers)` finding
- a `customer-id (identifiers)` finding
- no matched email address or customer ID in the findings

See [Run and operate Privacy Guard](operations.md) for cleanup, gateway
configuration, logging, timeouts, and troubleshooting.

## Core concepts

### Policies control behavior

The OpenShell policy supplies the ordered engine stages and final action.
Privacy Guard startup selects which engine implementations and operational
resources are installed.

### Stages run in order

Each stage receives the current text. In `replace` mode, later stages receive
the text returned by earlier stages. In `detect` and `block` mode, engines must
return the input text unchanged.

### Findings do not contain matched values

Findings contain the entity name, stage, confidence, and occurrence count.
Matched text, surrounding text, offsets, regex patterns, headers, and request
bodies do not cross the service boundary.

### Processing is bounded

Request size, output size, detections, regex execution, concurrency, and
processing time have explicit limits. A limit failure denies the request
without returning partial replacement text or partial findings.

## Documentation map

- [Configure policies](configuration.md): stages, actions, catalogs, and policy
  recipes.
- [Run and operate Privacy Guard](operations.md): CLI, gateway registration,
  server lifecycle, logging, and troubleshooting.
- [Use RegexEngine](engines/regex.md): pattern catalogs, flags, matching, and
  replacement.
- [Add a custom engine](engines/custom.md): engine contract, resources,
  registry factories, and tests.
- [System architecture](architecture/index.md): components, trust boundaries,
  state, and concurrency.
- [Request lifecycle](architecture/request-lifecycle.md): validation,
  activation, processing, and result mapping.
- [Service boundary](architecture/service-boundary.md): gRPC methods,
  protobuf translation, and worker scheduling.
- [Limits and failure behavior](reference/limits-and-failures.md): enforced
  bounds, error outcomes, retention, and measured latency.
