---
title: Configure policies
description: Configure Egress Gate stages, actions, catalogs, and OpenShell middleware routing.
agent_markdown: true
---

# Configure policies

Egress Gate configuration is embedded in an OpenShell
`network_middlewares` entry. The policy determines:

- which provider endpoints use Egress Gate
- the order of entity-processing stages
- each engine's exact configuration
- whether detections are reported, blocked, or replaced
- OpenShell's behavior when the middleware RPC fails

Egress Gate validates the complete configuration before processing a request.

## Complete middleware entry

```yaml
network_middlewares:
  egress_gate_replace:
    name: Replace email addresses and customer IDs
    middleware: egress-gate
    order: 0
    config:
      entity_processing:
        stages:
          - name: identifiers
            config:
              engine: regex
              pattern_catalog:
                entities:
                  - name: email
                    rules:
                      - name: conventional-email
                        pattern: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
                        confidence: high
                replacement:
                  strategy: template
                  template: "[{entity}]"
      on_detection:
        action: replace
    on_error: fail_closed
    endpoints:
      include:
        - api.anthropic.com
```

`middleware` must match the name registered in the OpenShell gateway
configuration. `endpoints.include` restricts the middleware to the listed
provider hosts.

Use `on_error: fail_closed` when a Egress Gate RPC failure must stop the
request. A policy block or processing-limit denial is a successful middleware
result and does not use `on_error`.

## Entity-processing stages

`entity_processing.stages` is a non-empty ordered list with at most ten stages.
Each stage has:

| Field | Required | Purpose |
| --- | --- | --- |
| `name` | No | Stable diagnostic name used in findings and logs |
| `config` | Yes | Exact configuration for the selected engine |
| `config.engine` | Yes | Engine discriminator registered by the running Egress Gate process |

Stage names must be unique when supplied. Egress Gate derives names such as
`regex[1]` for unnamed stages.

In `replace` mode, the output of one stage becomes the input to the next:

```text
original text -> stage 1 -> stage 2 -> final replacement text
```

Detection offsets belong to the input revision seen by the stage that produced
them. Findings aggregate by stage, entity, and confidence.

## Detection actions

Set `on_detection.action` to one of:

| Action | Engine strategy | No detections | Detections |
| --- | --- | --- | --- |
| `detect` | `DETECT` | Allow original body | Allow original body and report findings |
| `block` | `DETECT` | Allow original body | Deny with `egress_gate_blocked` |
| `replace` | `REPLACE` | Allow final stage output | Allow final stage output and report findings |

`replace` requires every configured stage to support replacement and to satisfy
its engine-specific replacement requirements. A replacement recipe may remain
configured when the action is `detect` or `block`; it is not used in those
modes.

## Common policy recipes

### Detect without changing the request

```yaml
entity_processing:
  stages:
    - name: identifiers
      config:
        engine: regex
        pattern_catalog: patterns.yaml
on_detection:
  action: detect
```

Use this to observe findings while leaving the provider-bound body unchanged.

### Block requests containing configured entities

```yaml
entity_processing:
  stages:
    - name: restricted-values
      config:
        engine: regex
        pattern_catalog: patterns.yaml
on_detection:
  action: block
```

The request is denied only when at least one configured entity is detected.

### Replace entities

```yaml
entity_processing:
  stages:
    - name: identifiers
      config:
        engine: regex
        pattern_catalog: patterns.yaml
        replacement:
          strategy: template
          template: "[{entity}]"
on_detection:
  action: replace
```

`{entity}` is replaced with the catalog entity name. For example,
`user@example.com` becomes `[email]`.

### Run multiple stages

```yaml
entity_processing:
  stages:
    - name: structured-identifiers
      config:
        engine: regex
        pattern_catalog: identifiers.yaml
        replacement:
          strategy: template
          template: "[{entity}]"
    - name: organization-model
      config:
        engine: acme-pii
        model_profile: organization-default
        replacement:
          strategy: native
on_detection:
  action: replace
```

The `acme-pii` engine and its configuration are examples of a custom
installation. The running registry must contain every engine named by the
policy.

## Regex catalogs

`RegexEngine` accepts an inline catalog or a relative YAML path.

Inline:

```yaml
pattern_catalog:
  entities:
    - name: customer-id
      rules:
        - name: prefixed-eight-digit-id
          pattern: '\bCUST-[0-9]{8}\b'
          confidence: high
```

File-backed:

```yaml
pattern_catalog: patterns.yaml
```

Relative paths resolve beneath Egress Gate's working directory. The path must
end in `.yaml` or `.yml`. Absolute paths, `..` traversal, and symlinks are
rejected. Start Egress Gate from the directory that contains the referenced
catalog, or use a path relative to that directory.

See [RegexEngine](engines/regex.md) for the complete catalog schema.

## Inspect and validate configuration

List the engines installed in the selected registry:

```bash
uv run egress-gate engines
```

Print the exact JSON Schema accepted by that registry:

```bash
uv run egress-gate configuration-schema
```

For a custom registry, pass the same factory to inspection and serving:

```bash
uv run egress-gate \
  --registry-factory my_engines:create_registry \
  engines

uv run egress-gate \
  --registry-factory my_engines:create_registry \
  configuration-schema

uv run egress-gate \
  --registry-factory my_engines:create_registry \
  serve
```

Sandbox creation calls `ValidateConfig`. A successful creation proves that the
middleware registration is reachable and that the supplied config matches the
running registry.

## Policy and deployment ownership

Keep privacy behavior in policy:

- stage order
- entity definitions
- detection settings
- engine-specific replacement recipes
- final action

Keep operational resources in the Egress Gate deployment:

- installed engine implementations
- model clients and SDK adapters
- endpoints and credentials
- approved model profiles
- processing timeout

A policy cannot select a registry factory or import Python code.

## Configuration activation

OpenShell sends the complete configuration on each evaluation. Egress Gate
validates it and compares the normalized immutable result with the active
configuration:

- equal configuration reuses the active processor
- changed valid configuration is fully prepared, then atomically activated
- failed validation or preparation leaves the active processor unchanged and
  fails the triggering evaluation

Send one consistent configuration stream to each Egress Gate process.
Interleaving configurations causes the active processor to switch between them.

The transport configuration is limited to 64 KiB. File-backed Regex catalogs
carry only their relative path through the transport and are loaded by the
Egress Gate process.

## Next steps

- [RegexEngine](engines/regex.md)
- [Add a custom engine](engines/custom.md)
- [Run and operate Egress Gate](operations.md)
- [Limits and failure behavior](reference/limits-and-failures.md)
