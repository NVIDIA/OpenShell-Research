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

The top-level policy is exactly:

```yaml
pipeline:
  gates: []             # one through ten named entries
  default_decision: allow  # allow or deny; required
```

Each entry has a unique bounded `name` and a gate-specific `config` selected
by its literal `gate` discriminator. Unknown fields, unknown gate types,
missing defaults, duplicate names, and more than ten entries are rejected.
There is no compatibility acceptance for legacy policy keys.

## Built-in gates

The shipped registry contains only `regex-body`. See
[Regex-body](gates/regex.md) for catalogs and replacement templates. Its
`mode` is required and is one of `detect`, `deny`, or `replace`; a replacement
recipe is required exactly when the mode is `replace`. Other behavior is
supplied by a trusted application registry factory, not by configuration.

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
classes and typed `GateResources`; policy configuration cannot import Python,
choose a resource implementation, or provide credentials.

`validate` performs the same strict policy and registered-resource validation
used by the processing domain, without constructing gates, loading catalogs,
preparing a processor, or changing the running service's active policy. Use
`evaluate` to exercise preparation-time artifacts. Exact encoded OpenShell
configuration size remains a gRPC service-boundary check.

For repeatable request-level checks, the `evaluate` command accepts a pipeline
policy and a strict version-one corpus. It uses the registry's prepared
processor seam and does not start the gRPC service; see [Offline evaluation](evaluation.md).
