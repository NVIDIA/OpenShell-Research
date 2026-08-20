---
title: Configure policies
description: Configure Egress Gate pipelines and request-level gates.
agent_markdown: true
---

# Configure policies

OpenShell embeds the Egress Gate policy in a `network_middlewares` entry. The
registry validates the complete strict configuration before preparing a
pipeline processor.

```yaml title="OpenShell policy"
network_middlewares:
  egress_gate:
    name: Inspect provider requests
    middleware: egress-gate
    order: 0
    config:
      gates:
        - name: identifiers
          kind: regex
          scan:
            kind: body
            action:
              kind: replace
              template: '[{entity}]'
          pattern_catalog: patterns.yaml
      default_decision: allow
    on_error: fail_closed
    endpoints:
      include: [api.anthropic.com]
```

Relative catalog paths resolve from the Egress Gate process working directory,
not from the policy file. Use an inline catalog when the process does not have a
stable working directory.

The Egress Gate policy has two required fields:

- `gates` contains one through ten named gate configurations.
- `default_decision` is `allow` or `deny`.

Each gate entry has a unique, bounded `name`. Its literal `kind` field selects
the exact gate type and its remaining fields. The registry rejects unknown
fields, unknown gate types, missing defaults, and duplicate names.

## Built-in gates

The shipped registry contains only `regex`. See
[Regex gate](gates/regex.md) for scans, actions, catalogs, and replacement
templates. `scan.kind` selects the complete body, selected JSON string fields,
normalized message blocks, path, query, or named headers. Every scan supports
`detect` and `deny`. Complete-body, `json-fields`, and `message-blocks` scans
also support `replace`; path, query, and header schemas do not. A trusted
application registry supplies other behavior.

Structured scans do not activate automatically based on headers or request
contents. Existing `kind: body` policies retain their complete-body UTF-8
behavior. Choose `json-fields` or `message-blocks` explicitly when a policy
expects a strict JSON request body.

## Inspect the installed registry

Run these commands from the
[`projects/egress-gate/`](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate)
directory in a source checkout:

```bash title="Inspect the default registry"
uv run egress-gate gates list
uv run egress-gate gates schema
uv run egress-gate validate --policy path/to/policy.yaml
```

Custom registries use the same module attribute for inspection and serving:

```bash title="Inspect a custom registry"
uv run egress-gate \
  --registry my_gates:registry gates list
uv run egress-gate \
  --registry my_gates:registry gates schema
```

The attribute can contain a `GateRegistry` or a zero-argument factory that
returns one. A factory is useful when a deployment must construct typed
`GateResources` dynamically. Policy configuration cannot import Python, choose
a resource implementation, or provide credentials.

`validate` checks the policy and registered resources. It also reads and checks
a file-backed pattern catalog. It does not construct gates, prepare a
pipeline processor, or change the active policy. Use `evaluate` to check
artifacts that the gate creates during preparation. The gRPC service checks the
exact encoded size of the OpenShell configuration.

For repeatable request-level checks, the `evaluate` command accepts a pipeline
policy and a strict version-one corpus. It uses the registry's prepared
pipeline processor path and does not start the gRPC service. See
[Test policies offline](evaluation.md).
