# Regex redaction composition

This example runs the built-in `regex-body` gate in `replace` mode. The
standalone configuration embeds a small email catalog so it can be validated
or evaluated from any working directory. The OpenShell `policy.yaml` shows the
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

The `regex-body` gate is byte-oriented at the runtime boundary and performs
strict UTF-8 decoding only inside the gate. Its `detect`, `deny`, and `replace`
modes are independent policy choices; there is no global detection action.
