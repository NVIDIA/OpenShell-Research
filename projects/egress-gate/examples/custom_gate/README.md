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

Run the example from `projects/egress-gate/`. Activate the installed project
environment once, then use the CLI executable directly:

```bash
source .venv/bin/activate
egress-gate \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  gates

egress-gate \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  validate --policy examples/custom_gate/egress-gate-config.yaml

egress-gate \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  evaluate \
  --policy examples/custom_gate/egress-gate-config.yaml \
  --cases examples/custom_gate/cases.yaml
```

The executable resolves the explicit `module:factory` reference from the
working directory. An installed custom-gate package works the same way.

The `block-secret-keyword` gate denies the first corpus case. The second gate
evaluation proceeds. The explicit `default_decision: allow` then determines
the result.

This is a teaching example, not a robust content classifier. The runtime
already checks the `HttpRequest` limits. Do not check those limits again.

A production gate must define its text-decoding and matching behavior. Add
limits only for work that belongs to the gate. Do not put request content in
errors or findings. Check the shared timeout during expensive work, and keep
request state local.
