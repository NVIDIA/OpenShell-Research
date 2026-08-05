# Minimal custom gate

This example adds a `keyword-deny` gate in one Python file. If the configured
keyword occurs in the request body, the gate denies the request. Otherwise, it
returns `proceed`, and the pipeline continues.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the exact policy fields and the stable
   `kind: keyword-deny` discriminator.
2. `KeywordDenyGate` declares what it reads and may return, then implements
   `_evaluate`.
3. `create_registry` registers the trusted Python class and finalizes the
   configuration schema.

Run the example from `projects/egress-gate/`. `uv run` prepares the project
environment before each command:

```bash
uv run egress-gate \
  --registry-factory examples.custom-gate.keyword_gate:create_registry \
  gates list

uv run egress-gate \
  --registry-factory examples.custom-gate.keyword_gate:create_registry \
  validate --policy examples/custom-gate/egress-gate-config.yaml

uv run egress-gate \
  --registry-factory examples.custom-gate.keyword_gate:create_registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

The executable resolves the explicit `module:factory` reference from the
working directory. An installed custom-gate package works the same way.

The `block-secret-keyword` gate denies the first corpus case. The second gate
evaluation proceeds. The explicit `default_decision: allow` then determines
the result.

This is a teaching example, not a robust content classifier. The pipeline
processor already checks the `HttpRequest` limits. Do not check those limits
again.

A production gate must define its text-decoding and matching behavior. Add
limits only for work that belongs to the gate. Do not put request content in
errors or findings. Check the shared timeout during expensive work, and keep
request state local.
