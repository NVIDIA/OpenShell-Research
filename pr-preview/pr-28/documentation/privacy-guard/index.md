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

> **Experimental:** Privacy Guard is a proof of concept, not a guarantee that
> sensitive data cannot leak. It currently protects only provider-bound network
> requests that OpenShell routes through this middleware.

Privacy Guard does not intercept prompts, tool output, transcripts, or session
history before a harness writes them to disk. Those files may contain raw
sensitive values even when Privacy Guard later replaces or blocks the network
request. Use harness persistence controls and appropriate storage isolation,
retention, and cleanup in addition to Privacy Guard.

Privacy Guard processes the complete request body as UTF-8 text. It does not
parse JSON fields, inspect files in the sandbox, modify provider responses, or
send network requests to the provider.

## How it fits into OpenShell

![A provider-bound request travels from the sandbox through OpenShell and
Privacy Guard. Privacy Guard returns an allow, replacement, or deny decision
before OpenShell attaches credentials and sends an allowed request to the
provider.](assets/diagrams/request-path.svg)

Privacy Guard exposes OpenShell's `SupervisorMiddleware` gRPC service. It
registers for the pre-credentials request phase so replacement happens before
the provider receives the request.

## Quickstart

This quickstart uses the built-in `RegexEngine` example to replace an email
address and customer ID. Before you start, install:

- Python 3.11 or newer
- `uv` 0.11 or newer
- [OpenShell](https://github.com/NVIDIA/OpenShell) `v0.0.90` or a later
  compatible version

Privacy Guard is tested with the version recorded in the
[middleware manifest](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/privacy-guard/.openshell-middleware-manifest.json).
A later version must support the same supervisor middleware contract.

Run the following commands from a checkout of OpenShell Research.

### 1. Stop the local gateway

First, check the local gateway:

```bash
openshell status
```

If the gateway is running, stop it before you change its configuration. Use the
command for your system:

```bash
# macOS with Homebrew
brew services stop openshell

# Linux with a Debian or RPM package
systemctl --user stop openshell-gateway
```

### 2. Start Privacy Guard

```bash
cd projects/privacy-guard/examples/regex-engine
uv run --locked privacy-guard serve --listen 0.0.0.0:50051
```

Keep the process running. The development server uses plaintext gRPC and
receives request bodies. Restrict port 50051 to the host and trusted sandbox
network.

### 3. Configure and start the gateway

Choose a non-loopback host IPv4 address that both the gateway and sandbox
supervisor can reach.

Open another terminal and return to the example directory. Replace
`YOUR_HOST_IPV4` with the address you selected. Then update the default gateway
configuration:

```bash
cd projects/privacy-guard/examples/regex-engine
uv run privacy-guard add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name privacy-guard-regex
```

Do not use `127.0.0.1`: loopback inside the sandbox supervisor does not refer to
the host.

The command above updates the default OpenShell gateway configuration. Next,
use the command for your system to start the gateway in the background:

```bash
# macOS with Homebrew
brew services start openshell

# Linux with a Debian or RPM package
systemctl --user start openshell-gateway
```

### 4. Create a sandbox

Open another terminal, return to the example directory, and check the gateway:

```bash
cd projects/privacy-guard/examples/regex-engine
openshell status
```

This walkthrough starts Claude Code in the sandbox. To use a different harness,
replace everything after `--` with its command.

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

### Findings use stable identifiers

Framework-controlled fields do not add matched text, surrounding text, offsets,
regex patterns, headers, or request bodies to findings. `RegexEngine` uses
configured entity identifiers. Custom engines must also return stable,
declared entity identifiers that are not derived from request text.

### Processing is bounded

Request size, output size, detections, regex execution, concurrency, and
processing time have explicit limits. A limit failure denies the request
without returning partial replacement text or partial findings.

## Documentation map

- [Configure policies](configuration.md): stages, actions, catalogs, and policy
  recipes.
- [Run and operate Privacy Guard](operations.md): CLI, gateway registration,
  server lifecycle, logging, and troubleshooting.
- [RegexEngine](engines/regex.md): pattern catalogs, flags, matching, and
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
