---
title: Test policies offline
description: Test policy decisions and findings before deployment.
agent_markdown: true
---

# Test policies offline

A policy can be valid and still do the wrong thing. It might allow a request
that you meant to deny, invoke the wrong gate, or stop reporting a finding
after a rule changes.

`egress-gate evaluate` lets you catch these problems before the policy handles
live traffic. You give it a policy and a set of request examples. Each example
states the result that you expect. Egress Gate runs every request through the
same prepared `RequestProcessor` that the service uses and reports any
difference.

This is useful when you want to:

- check a new policy before rollout
- turn a fixed bug into a permanent regression test
- test a custom gate without starting the gRPC service
- compare the behavior of two policy revisions
- build a repeatable request set for a separate performance benchmark

The command tests correctness. It does not report latency or throughput. Use a
benchmark harness around the same request set when you need performance data.

## Try the included example

The repository includes a regex policy and two request cases. Run them from
`projects/egress-gate/`; `uv` prepares the locked environment automatically:

```bash title="Run the example policy tests"
uv run egress-gate evaluate \
  --policy examples/regex-redaction/egress-gate-config.yaml \
  --cases examples/regex-redaction/cases.yaml \
  --timeout-seconds 1
```

The command prepares the policy once, runs each case with a fresh timeout, and
shows whether each request produced its expected result:

```text title="Evaluation output"
                            Policy evaluation
╭────────┬──────────────────────────────────────────┬────────────────────╮
│ Status │ Case                                     │ Details            │
├────────┼──────────────────────────────────────────┼────────────────────┤
│ PASS   │ email-is-detected-and-request-is-allowed │ All checks matched │
├────────┼──────────────────────────────────────────┼────────────────────┤
│ PASS   │ ordinary-body-is-allowed                 │ All checks matched │
╰────────┴──────────────────────────────────────────┴────────────────────╯
2 passed · 0 failed · 2 total
```

If a case fails, the Details column shows each field that differed and its
expected and actual values. The summary and exit status make the same result
easy to use in CI.

No request goes to an upstream service. The command does not start gRPC,
attach credentials, or persist request data.

## Write one test case

The CLI calls the cases file a *corpus*. In plain terms, it is a versioned YAML
test suite. `version: 1` selects the current file format. You do not need to
manage multiple versions.

This example checks that the regex policy reports an email finding and then
allows the request through its default decision:

```yaml title="cases.yaml"
version: 1
cases:
  - name: email-is-detected
    provenance:
      kind: synthetic
      redacted: false
    request:
      context:
        request_id: test-email
        sandbox_id: test-sandbox
      target:
        scheme: https
        host: api.example.com
        port: 443
        method: POST
        path: /v1/messages
        query: ""
      headers: []
      body:
        encoding: utf8
        value: "send alice@example.com"
    expected:
      decision: allow
      finding_types: [regex_match]
```

Each case has three parts:

- `provenance` records whether the request is synthetic or captured and
  whether its content is redacted.
- `request` contains the first read-only HTTP request snapshot that the gates
  will evaluate.
- `expected` contains the result fields that must match.

Only `expected.decision` is required. Add more expected fields when they make
the test more useful:

| Expected field | What it checks |
| --- | --- |
| `decision_source_kind` | Whether a gate, the pipeline default, or a runtime limit made the decision |
| `gate_name` | Which configured gate made a terminal decision |
| `gate_type` | Which gate implementation made a terminal decision |
| `finding_types` | The ordered finding types returned by the pipeline |

Gate name and gate type apply only when `decision_source_kind` is `gate`.
Omitted fields are not compared. The current evaluator does not compare the
contents of a request patch.

## Grow the suite with the policy

Start with one normal request and one request for each important deny or
finding rule. Add a case whenever you fix a policy bug. Keep captured requests
small, deliberate, and redacted when possible.

Case names must be unique. Optional tags can group cases for external tooling.
The parser also rejects aliases, duplicate keys, unknown fields, invalid
base64, and values that exceed runtime limits. These checks keep tests
repeatable and ensure that test requests follow the same bounds as service
requests.

Use `--registry-factory` when the policy contains application-owned custom
gates:

```bash title="Test a custom gate"
uv run egress-gate \
  --registry-factory examples.custom-gate.keyword_gate:create_registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

## Use the result in automation

The command uses stable exit statuses:

| Status | Meaning |
| ---: | --- |
| `0` | Every case matched |
| `1` | One or more cases did not match |
| `2` | The policy, cases, preparation, or execution failed |

Failure output contains decision metadata and finding types. It does not print
request bodies or raw exception text. This makes the command suitable for CI
logs while keeping request content out of normal output.
