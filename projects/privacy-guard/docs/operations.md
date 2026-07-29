---
title: Run and operate Privacy Guard
description: Start Privacy Guard, register it with OpenShell, configure logging and timeouts, and troubleshoot failures.
agent_markdown: true
---

# Run and operate Privacy Guard

Privacy Guard runs as a gRPC service reachable by the OpenShell gateway and
sandbox supervisors. The CLI uses the built-in `RegexEngine` registry unless
you supply a custom registry factory.

## Protection boundary and local persistence

Privacy Guard is experimental. It evaluates provider-bound network requests at
the OpenShell middleware boundary; it is not a guarantee that sensitive data
cannot leak.

The middleware does not run before harness filesystem writes. A harness may
persist raw prompts, tool output, transcripts, or session history before
OpenShell sends a provider request to Privacy Guard. Configure the harness to
disable or minimize persistence where possible, restrict access to sandbox and
host storage, set an appropriate retention policy, and remove sensitive session
artifacts after use.

## Install the project environment

From the repository checkout:

```bash
cd projects/privacy-guard
uv sync --locked
```

Confirm the installed engines and policy schema:

```bash
uv run privacy-guard engines
uv run privacy-guard configuration-schema
```

## Start the service

For host-only testing:

```bash
uv run privacy-guard serve
```

The default address is `127.0.0.1:50051`.

For OpenShell sandbox supervisors running outside the host network namespace:

```bash
uv run privacy-guard serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 4
```

`--timeout-seconds` is one deadline shared by every stage in a request. It
defaults to 1 second and must not exceed 30 seconds. OpenShell's middleware
timeout must be longer to cover worker queueing, configuration validation, and
processor preparation.

The development server uses plaintext gRPC and receives request bodies.
Restrict its listen port to trusted host and sandbox networks.

## Register the service with OpenShell

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

Choose a non-loopback IPv4 address reachable by both the local gateway and
sandbox supervisors.

Replace `YOUR_HOST_IPV4` with the address you selected. Then add or update the
gateway registration:

```bash
uv run privacy-guard add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 \
  --name privacy-guard \
  --port 50051
```

The command writes to:

1. the path in `OPENSHELL_GATEWAY_CONFIG`, when set
2. otherwise `$XDG_CONFIG_HOME/openshell/gateway.toml`
3. otherwise `~/.config/openshell/gateway.toml`

Use `--config PATH` to write a different gateway TOML. Existing unrelated
gateway settings are preserved.

Remove a registration by name when it is no longer needed:

```bash
uv run privacy-guard remove-gateway-registration \
  --name privacy-guard
```

This command uses the same default config path and accepts `--config PATH`.
It leaves unrelated registrations and gateway settings unchanged. Restart the
gateway after a registration is removed.

`add-gateway-registration` writes a five-second OpenShell middleware timeout. The
four-second processing timeout above leaves one second for queueing,
configuration validation, processor preparation, and transport overhead. If
you select a longer processing timeout, edit the generated registration to add
headroom. Rerunning `add-gateway-registration` restores the timeout to five seconds.

The registration name must match the policy's `middleware` field:

```yaml
network_middlewares:
  privacy_guard_replace:
    middleware: privacy-guard
```

OpenShell does not dynamically reload middleware registrations. Restart the
gateway after changing its configuration.

## Start the local gateway

Use the command for your system to start the gateway in the background:

```bash
# macOS with Homebrew
brew services start openshell

# Linux with a Debian or RPM package
systemctl --user start openshell-gateway
```

The gateway reads the default configuration that `add-gateway-registration`
updated.

## Verify connectivity

```bash
openshell status
```

Do not create a sandbox until the selected gateway reports as connected.
Sandbox creation validates each referenced external middleware and its policy
configuration.

## Inspect request results

Use a finite log window:

```bash
openshell logs SANDBOX_NAME -n 100 --source sandbox
```

For a provider request, inspect:

| Field | Meaning |
| --- | --- |
| `transformed:true` | Privacy Guard returned a replacement body |
| `transformed:false` | Original body was allowed |
| findings | Aggregated entity, stage, confidence, and count |
| deny reason | `privacy_guard_blocked` or `privacy_guard_limit_exceeded` |

Framework-controlled finding fields and `RegexEngine` do not add matched values
or surrounding request text. Custom engines must use stable, declared entity
identifiers that are not derived from request text.

## Privacy Guard logging

Default logs contain request IDs, evaluation duration, decision, aggregate
finding count, and stable error codes. They exclude request bodies, replacement
text, matches, offsets, regex patterns, headers, targets, and credentials.

Enable content-safe diagnostic logging:

```bash
uv run privacy-guard --debug serve
```

`--debug-log-content` logs complete input and processed text. Use it only in a
controlled development environment:

```bash
uv run privacy-guard --debug --debug-log-content serve
```

When imported as a library, Privacy Guard uses the standard `privacy_guard`
logger and does not modify application logging unless requested.

## Use a custom registry

Pass a trusted `module:function` factory to every CLI command:

```bash
uv run privacy-guard \
  --registry-factory my_engines:create_registry \
  engines

uv run privacy-guard \
  --registry-factory my_engines:create_registry \
  serve \
  --listen 0.0.0.0:50051
```

The function is imported and executed in the Privacy Guard process. It must
return a finalized `EngineRegistry`. See
[Add a custom engine](engines/custom.md).

## Run the server from Python

```python
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.service import PrivacyGuardServer

server = PrivacyGuardServer(
    create_builtin_registry(),
    timeout_seconds=5,
)
server.serve_sync("127.0.0.1:50051")
```

Async applications use:

```python
await server.serve_async("127.0.0.1:50051")
```

Listen addresses use `host:port` or bracketed IPv6 `[address]:port` form. The
port must be between 1 and 65535.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Sandbox creation reports unavailable middleware | Confirm Privacy Guard is running, the registration name matches the policy, the host IP is reachable from the sandbox network, and the port is allowed |
| Policy config is rejected | Run `privacy-guard configuration-schema` with the same registry factory used by the server |
| Relative Regex catalog is not found | Start Privacy Guard from the directory against which the catalog path is defined |
| Request is denied with `privacy_guard_limit_exceeded` | Inspect Privacy Guard logs, reduce input/output/detections, simplify stages, or increase the processing timeout with OpenShell headroom |
| Registry factory cannot be loaded | Install the module or add its parent directory to `PYTHONPATH`; verify `module:function` spelling |
| Gateway accepts config but behavior does not change | Restart the gateway; middleware registrations are not reloaded dynamically |
| CLI and gateway reject policy fields differently | Confirm `openshell`, `openshell-gateway`, and Privacy Guard use the same protocol version |

## Shutdown and cleanup

Delete test sandboxes explicitly, then stop Privacy Guard with `Ctrl-C`:

```bash
openshell sandbox delete SANDBOX_NAME
```

When the registration is no longer needed, stop the gateway before removing
it. Replace `REGISTRATION_NAME` with the name passed to
`add-gateway-registration`:

```bash
# macOS with Homebrew
brew services stop openshell

# Linux with a Debian or RPM package
systemctl --user stop openshell-gateway

uv run privacy-guard remove-gateway-registration \
  --name REGISTRATION_NAME
```

Restart the gateway with the command for your system, then verify its
connection:

```bash
# macOS with Homebrew
brew services start openshell

# Linux with a Debian or RPM package
systemctl --user start openshell-gateway

openshell status
```

## Next steps

- [Configure policies](configuration.md)
- [RegexEngine](engines/regex.md)
- [Limits and failure behavior](reference/limits-and-failures.md)
