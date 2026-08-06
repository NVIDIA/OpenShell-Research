---
title: Run and operate Egress Gate
description: Start, register, observe, and troubleshoot Egress Gate.
agent_markdown: true
---

# Run and operate Egress Gate

The OpenShell gateway and sandbox supervisors call Egress Gate through gRPC.
Run the service from `projects/egress-gate`. `uv run` prepares the project
environment as needed.

```bash title="Start Egress Gate"
uv run egress-gate gates list
uv run egress-gate gates schema
uv run egress-gate serve --listen 0.0.0.0:50051 --timeout 4s
```

Use a reachable non-loopback address only when the supervisor is outside the
host network namespace of the service. Plaintext gRPC is for a restricted,
trusted network. Do not expose the port to an untrusted network.

## OpenShell registration

Before you change the gateway configuration, stop any running OpenShell
gateways that use it. A running gateway does not reload middleware
registrations.

```bash title="Register Egress Gate"
uv run egress-gate add-gateway-registration \
  --host-ip YOUR_HOST_IPV4 --name egress-gate --port 50051
```

The command updates `OPENSHELL_GATEWAY_CONFIG`, then
`$XDG_CONFIG_HOME/openshell/gateway.toml`, then
`~/.config/openshell/gateway.toml`. Use `--config PATH` for another file.
Start the gateways again with the same commands or service managers that you
normally use.

The registration writes `timeout = "30s"` in the gateway TOML. Egress Gate calls
this setting `timeout_gateway_ceiling`: the maximum time the gateway permits
for Egress Gate. Set the actual service value with
`egress-gate serve --timeout DURATION`; Egress Gate calls that setting
`timeout_middleware_processing`.

To remove a registration, stop any running gateways that use the configuration
again. List the available names with:

```bash title="List middleware registrations"
uv run egress-gate list-gateway-registrations
```

The gateway config does not identify which service owns a registration. The
command therefore lists all external middleware. Use its exact name to remove
the registration you no longer need:

```bash title="Remove the registration"
uv run egress-gate remove-gateway-registration --name egress-gate
```

Start the gateways again after the command completes.

Egress Gate uses `timeout_middleware_processing` across queueing, policy
preparation, and every configured gate. OpenShell applies
`timeout_gateway_ceiling` as an independent upper bound:

```text
effective timeout = min(timeout_gateway_ceiling, timeout_middleware_processing)
```

With the registration command's 30s ceiling, any supported `--timeout` value
becomes the effective timeout. If an operator lowers the gateway ceiling by
editing the gateway configuration, that lower value wins.

If the middleware RPC returns gRPC `RESOURCE_EXHAUSTED`, capacity may remain
accounted for briefly while completed RPCs are torn down. The OpenShell gateway
or supervisor should retry the middleware RPC with short, bounded exponential
backoff, for example 5, 10, then 20 milliseconds, while staying inside its
middleware deadline. Do not turn this into an unbounded application-level
retry or replay an outbound request unless its request semantics permit that.

## Verify readiness

Egress Gate does not expose a separate gRPC health service. Verify readiness at
the policy, transport, and end-to-end layers instead. The commands below use an
installed `egress-gate` executable; prefix them with `uv run` in a source
checkout.

1. Validate and evaluate the exact deployment policy before starting the
   service:

   ```bash
   egress-gate validate --policy /absolute/path/to/policy.yaml
   egress-gate evaluate \
     --policy /absolute/path/to/policy.yaml \
     --cases /absolute/path/to/cases.yaml
   ```

2. Start Egress Gate and wait for the content-safe
   `egress_gate_server_bound` log entry. From the OpenShell gateway host or
   network namespace, confirm the registered address accepts a TCP connection:

   ```bash
   python3 -c 'import socket; socket.create_connection(("EGRESS_GATE_HOST", 50051), timeout=2).close()'
   ```

   This proves transport reachability only; it does not exercise the gRPC
   contract or a policy.

3. After restarting the OpenShell gateway, send one harmless request from a
   sandbox whose policy uses the registration. Choose an endpoint explicitly
   allowed by that policy:

   ```bash
   openshell sandbox exec --name SANDBOX_NAME --no-tty -- \
     curl --fail --silent --show-error https://ALLOWED_TEST_ENDPOINT/health
   openshell logs SANDBOX_NAME -n 100 --source sandbox
   ```

   Readiness requires the request to receive its expected allow or deny result
   without a middleware connection, timeout, or configuration error. A TCP
   check alone is not sufficient.

## Logging and decisions

`--debug` enables content-safe diagnostics. Egress Gate does not log request or
replacement bodies. Set `NO_COLOR` to any value to suppress ANSI styling when
default logging writes to an interactive terminal. Application code can still
request colors explicitly with `LoggingConfig(color_mode=ColorMode.ALWAYS)`.

Successful policy outcomes are distinct from gRPC failures:

| Outcome | Wire result |
| --- | --- |
| Gate deny | deny, gate-owned reason code |
| Pipeline default deny | deny, `egress_gate_default_deny` |
| Pipeline processor reaches a safety limit | deny, `egress_gate_limit_exceeded` |
| Invalid request or config | gRPC `INVALID_ARGUMENT` |
| Gate or service failure | gRPC `INTERNAL` |

Results caused by pipeline processor limits contain no partial mutations or
findings. A failed candidate does not replace the active policy. See
[Limits and failures](reference/limits-and-failures.md).

## Policy rollout

Each Egress Gate service keeps one active prepared policy. To change the
policy, first stop requests that use the old configuration. Let all admitted
requests finish. Then, send a request that uses the new configuration. Use
separate service instances when different policies must be active at the same
time.

## Shutdown

Use Ctrl-C for an interactive process or send `SIGINT` through the service
manager, then wait for the process to exit before replacing it. Egress Gate
stops the gRPC server with zero transport grace and closes its worker resources;
callers with an active RPC may observe cancellation or unavailability and
should follow the bounded retry guidance above. grpcio messages such as
`Got goaway` or `Cancelling all calls` are expected during a planned shutdown
when the process exits normally. Investigate them when they occur outside a
deployment or shutdown window, accompany lost work, or the process does not
exit.

## Troubleshooting

Inspect a finite OpenShell log window:

```bash title="Inspect recent sandbox logs"
openshell status
openshell logs SANDBOX_NAME -n 100 --source sandbox
```

Check the request ID and stable error code in content-safe Egress Gate logs.
Reduce request, header, finding, metadata, or regex catalog size when the
limit reason is returned. Check the exact schema with `gates schema`
when validation fails.
