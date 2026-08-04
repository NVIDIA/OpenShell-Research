---
title: Offline policy evaluation
description: Run bounded request corpora through the production processor.
agent_markdown: true
---

# Offline policy evaluation

`egress-gate evaluate` prepares a validated pipeline once and evaluates a
bounded corpus locally through the production `RequestProcessor`. It does not
start gRPC, attach credentials, contact an upstream, or persist request data.

```bash title="Evaluate a request corpus"
uv run egress-gate evaluate \
  --policy examples/regex-redaction/egress-gate-config.yaml \
  --cases examples/regex-redaction/cases.yaml \
  --timeout-seconds 1
```

Use the global `--registry-factory` option for trusted application-owned gates.
The service and discovery commands accept the same finalized registry factory.

## Corpus v1

The corpus is strict, bounded YAML. The parser rejects aliases, duplicate keys,
unknown fields, invalid base64, oversized requests, and duplicate case names.

```yaml title="cases.yaml"
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
protobuf-free domain fields. The evaluator decodes `request.body` into the
bounded `HttpRequest` model that the service uses. `expected.decision` is
required. Source kind, gate name, gate type, and ordered finding types are
optional projections. Gate name and gate type require a `gate` source kind.
The evaluator does not compare omitted projections.

Each case gets a new `Timeout`. Policy preparation gets a separate timeout.
The evaluator reuses one prepared processor for all cases. Output is
content-safe and stable:

```text title="Evaluation output"
PASS case="ordinary-request"
SUMMARY total=1 passed=1 failed=0
```

Exit status `0` means that every case matches. Status `1` means that one or
more cases do not match. Status `2` means that input, preparation, or execution
failed. Mismatch lines contain only decision metadata and finding types. The
command does not print request bodies or raw exception text.
