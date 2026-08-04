# Regex redaction composition

This example runs the built-in `regex-body` gate in `replace` mode. The
standalone configuration contains a small email catalog. You can validate or
evaluate it from any working directory. The OpenShell `policy.yaml` shows the
equivalent file-backed catalog with email and customer-ID patterns. Both keep
request-derived content out of findings.

Inspect the installed gate and exact policy schema:

```bash
cd projects/egress-gate
uv run egress-gate gates
uv run egress-gate configuration-schema
```

Start the middleware:

```bash
cd projects/egress-gate/examples/regex-redaction
uv run egress-gate serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

Register that address with the OpenShell gateway using a reachable host IPv4
address, then create a sandbox with `policy.yaml`. The policy embeds the
`pipeline.gates` configuration and uses `egress-gate-redaction` as the
middleware registration name.

The `regex-body` gate receives bytes from the runtime. The gate strictly
decodes these bytes as UTF-8. Its `detect`, `deny`, and `replace` modes are
independent policy choices. There is no global detection action.
