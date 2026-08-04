---
title: Run and operate Egress Gate
description: Start, register, observe, and troubleshoot Egress Gate.
agent_markdown: true
---

# Run and operate Egress Gate

The OpenShell gateway and sandbox supervisors call Egress Gate through gRPC.
Install and run the service from `projects/egress-gate`:

```bash title="Start Egress Gate"
uv sync --frozen
source .venv/bin/activate
egress-gate gates
egress-gate configuration-schema
egress-gate serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

Use a reachable non-loopback address only when the supervisor is outside the
host network namespace of the service. Plaintext gRPC is for a restricted,
trusted network. Do not expose the port to an untrusted network.

## OpenShell registration

```bash title="Register Egress Gate"
egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 --name egress-gate --port 50051
```

The command updates `OPENSHELL_GATEWAY_CONFIG`, then
`$XDG_CONFIG_HOME/openshell/gateway.toml`, then
`~/.config/openshell/gateway.toml`. Use `--config PATH` for another file.
Restart the OpenShell gateway after changing registrations. Remove one with:

```bash title="Remove the registration"
egress-gate remove-gateway-registration --name egress-gate
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

Runtime limit results contain no partial patch or findings. A failed candidate
does not replace the active policy. See
[Limits and failures](reference/limits-and-failures.md).

## Policy rollout

Each Egress Gate service keeps one active prepared policy. To change the
policy, first stop requests that use the old configuration. Let all admitted
requests finish. Then, send a request that uses the new configuration. Use
separate service instances when different policies must be active at the same
time.

## Troubleshooting

Inspect a finite OpenShell log window:

```bash title="Inspect recent sandbox logs"
openshell status
openshell logs SANDBOX_NAME -n 100 --source sandbox
```

Check the request ID and stable error code in content-safe Egress Gate logs.
Reduce request, header, finding, metadata, or regex catalog size when the
limit reason is returned. Check the exact schema with `configuration-schema`
when validation fails.
