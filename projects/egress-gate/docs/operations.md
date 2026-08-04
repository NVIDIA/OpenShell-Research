---
title: Run and operate Egress Gate
description: Start, register, observe, and troubleshoot Egress Gate.
agent_markdown: true
---

# Run and operate Egress Gate

Egress Gate is a gRPC service reached by the OpenShell gateway and sandbox
supervisors. Install and run it from `projects/egress-gate`:

```bash
uv sync --frozen
uv run egress-gate gates
uv run egress-gate configuration-schema
uv run egress-gate serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

Use a reachable non-loopback address only when the supervisor is outside the
service's host network namespace. Plaintext gRPC is intended for a restricted
trusted network; do not expose the port broadly.

## OpenShell registration

```bash
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 --name egress-gate --port 50051
```

The command updates `OPENSHELL_GATEWAY_CONFIG`, then
`$XDG_CONFIG_HOME/openshell/gateway.toml`, then
`~/.config/openshell/gateway.toml`. Use `--config PATH` for another file.
Restart the OpenShell gateway after changing registrations. Remove one with:

```bash
uv run egress-gate remove-gateway-registration --name egress-gate
```

The generated OpenShell middleware timeout is five seconds. Keep the Egress
Gate `--timeout-seconds` below it so queueing, preparation, and transport have
headroom.

## Logging and decisions

`--debug` enables content-safe diagnostics. `--debug-log-content` is an
explicit development-only option that logs complete request and replacement
body content.

Successful policy outcomes are distinct from gRPC failures:

| Outcome | Wire result |
| --- | --- |
| Gate deny | deny, gate-owned reason code |
| Pipeline default deny | deny, `egress_gate_default_deny` |
| Runtime safety limit | deny, `egress_gate_limit_exceeded` |
| Invalid request or config | gRPC `INVALID_ARGUMENT` |
| Gate or service failure | gRPC `INTERNAL` |

Runtime limit results contain no partial patch or findings. The active policy
is not replaced by a failed candidate. See [Limits and failures](reference/limits-and-failures.md).

## Policy rollout

Each Egress Gate service keeps one active prepared policy. During a policy
change, stop admitting the old configuration before sending the new one so the
service performs one serialized cutover. Run separate service instances when
distinct policies must remain active concurrently.

## Troubleshooting

Inspect a finite OpenShell log window:

```bash
openshell status
openshell logs SANDBOX_NAME -n 100 --source sandbox
```

Check the request ID and stable error code in content-safe Egress Gate logs.
Reduce request, header, finding, metadata, or regex catalog size when the
limit reason is returned. Check the exact schema with `configuration-schema`
when validation fails.
