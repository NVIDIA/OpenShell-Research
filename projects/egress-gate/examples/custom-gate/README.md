# Minimal custom gate

This example adds a `keyword-deny` gate in one Python file. If the configured
keyword occurs in the request body, the gate denies the request. Otherwise, it
returns `proceed`, and the pipeline continues.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the exact policy fields and the stable
   `kind: keyword-deny` discriminator.
2. `registry.gate` turns the typed `keyword_deny` function into a standard
   resource-free gate type and adds it to the application registry.
3. The CLI loads that module-owned registry directly.

Run the example from `projects/egress-gate/`. `uv run` prepares the project
environment before each command:

```bash
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  gates list

uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  validate --policy examples/custom-gate/egress-gate-config.yaml

uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

The executable resolves the explicit `module:attribute` reference from the
working directory. The attribute can contain a registry or a zero-argument
registry factory. An installed custom-gate package works the same way.

The `block-secret-keyword` gate denies the first corpus case. The second gate
evaluation proceeds. The explicit `default_decision: allow` then determines
the result.

This is a teaching example, not a robust content classifier. The pipeline
processor already checks the `HttpRequest` limits. Do not check those limits
again.

The bound decorator is a helper for small, stateless gates. The class-based
`Gate` API remains available for reusable initialization, helper bases, and
typed operational resources.

A production gate must define its text-decoding and matching behavior. Add
limits only for work that belongs to the gate. Do not put request content in
errors or findings. Check the shared timeout during expensive work, and keep
request state local.
