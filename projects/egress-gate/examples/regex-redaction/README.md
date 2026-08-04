# Regex redaction composition

This example runs the built-in `regex` gate with a body scan and a replace
action. The standalone configuration contains a small email catalog. You can
validate or evaluate it from any working directory. The OpenShell `policy.yaml`
shows the equivalent file-backed catalog with email and customer-ID patterns.
Both keep request-derived content out of findings.

Inspect the installed gate and exact policy schema:

```bash
cd projects/egress-gate
source .venv/bin/activate
egress-gate gates
egress-gate configuration-schema
```

Start the middleware:

```bash
cd projects/egress-gate/examples/regex-redaction
egress-gate serve --listen 0.0.0.0:50051 --timeout-seconds 4
```

Register that address with the OpenShell gateway using a reachable host IPv4
address, then create a sandbox with `policy.yaml`. The policy embeds the
`pipeline.gates` configuration and uses `egress-gate-redaction` as the
middleware registration name.

This composition selects `scan.kind: body` and
`scan.action.kind: replace`. The gate strictly decodes the body bytes as UTF-8
before it finds and replaces matches. A body scan also supports `detect` and
`deny` actions. The same built-in can detect or deny matches in a path, query,
or selected header values.
