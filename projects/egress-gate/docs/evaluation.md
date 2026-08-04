---
title: Offline policy evaluation
description: Run bounded request corpora through the production processor.
agent_markdown: true
---

# Offline policy evaluation

`egress-gate evaluate` prepares a validated pipeline once and evaluates a
bounded corpus locally through the production `RequestProcessor`. It does not
start gRPC, attach credentials, contact an upstream, or persist request data.

```bash
uv run egress-gate evaluate \
  --policy examples/regex-redaction/egress-gate-config.yaml \
  --cases examples/regex-redaction/cases.yaml \
  --timeout-seconds 1
```

The command's `--registry-factory` global option is available for trusted
application-owned gates, using the same finalized registry factory accepted by
the service and discovery commands.

## Corpus v1

The corpus is strict bounded YAML. It rejects aliases, duplicate keys, unknown
fields, invalid base64, oversized bodies or request aggregates, and any case
name repeated in the document.

```yaml
version: 1
cases:
  - name: ordinary-request
    tags: [smoke]
    provenance:
      kind: synthetic       # synthetic or captured
      redacted: true
    request:
      context:
        request_id: corpus-1
        sandbox_id: sandbox-1
      target:
        scheme: https
        host: api.example.com
        port: 443
        method: POST
        path: /v1/items
        query: ""
      headers: []
      body:
        encoding: utf8       # utf8 or base64
        value: '{"item":"ordinary"}'
    expected:
      decision: allow
      decision_source_kind: pipeline_default
      finding_types: []
```

`request.context`, `request.target`, and `request.headers` use the existing
protobuf-free domain fields exactly. `request.body` is decoded into the same
bounded `HttpRequest` model used by the service. `expected.decision` is
required; source kind, gate name/type, and ordered finding types are optional
projections. Gate name/type projections require a `gate` source kind. Omitted
projections are not compared.

Each case gets a fresh `Timeout`; preparation gets its own timeout and the
prepared processor is reused for all cases. Output is content-safe and stable:

```text
PASS case="ordinary-request"
SUMMARY total=1 passed=1 failed=0
```

Exit status is `0` when every case matches, `1` when at least one case
mismatches, and `2` for invalid input, preparation failure, or execution
failure. Mismatch lines contain only expected/actual decision metadata and
finding types; request bodies and raw exception text are never printed.
