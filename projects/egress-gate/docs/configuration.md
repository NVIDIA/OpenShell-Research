---
title: Configure policies
description: Configure Egress Gate pipelines and request-level gates.
agent_markdown: true
---

# Configure policies

OpenShell embeds the Egress Gate policy in a `network_middlewares` entry. The
registry validates the complete strict configuration before preparing a
processor.

```yaml
network_middlewares:
  egress_gate:
    name: Inspect provider requests
    middleware: egress-gate
    order: 0
    config:
      pipeline:
        gates:
          - name: identifiers
            config:
              gate: regex-body
              pattern_catalog:
                entities:
                  - name: email
                    rules:
                      - pattern: '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
                        confidence: high
              mode: replace
              replacement:
                strategy: template
                template: '[{entity}]'
        default_decision: allow
    on_error: fail_closed
    endpoints:
      include: [api.anthropic.com]
```

The top-level policy has one `pipeline`. The pipeline has two required fields:

- `gates` contains one through ten named gate configurations.
- `default_decision` is `allow` or `deny`.

Each gate entry has a unique, bounded `name`. Its literal `gate` field selects
the exact configuration type. The registry rejects unknown fields, unknown gate
types, missing defaults, and duplicate names.

## Built-in gates

The shipped registry contains only `regex-body`. See
[Regex-body](gates/regex.md) for catalogs and replacement templates. Its
`mode` is required and is one of `detect`, `deny`, or `replace`. A replacement
recipe is required only when the mode is `replace`. A trusted application
registry factory supplies other behavior.

## Inspect the installed registry

```bash
uv run egress-gate gates
uv run egress-gate configuration-schema
uv run egress-gate validate --policy path/to/policy.yaml
```

Custom registries use the same factory for inspection and serving:

```bash
uv run egress-gate \
  --registry-factory my_gates:create_registry gates
uv run egress-gate \
  --registry-factory my_gates:create_registry configuration-schema
```

The factory must return a finalized `GateRegistry`. It owns trusted gate
classes and typed `GateResources`. Policy configuration cannot import Python,
choose a resource implementation, or provide credentials.

`validate` checks the policy and registered resources. It also reads and checks
a file-backed pattern catalog. It does not construct gates, prepare a
processor, or change the active policy. Use `evaluate` to check artifacts that
the gate creates during preparation. The gRPC service checks the exact encoded
size of the OpenShell configuration.

For repeatable request-level checks, the `evaluate` command accepts a pipeline
policy and a strict version-one corpus. It uses the registry's prepared
processor path and does not start the gRPC service. See
[Offline evaluation](evaluation.md).
