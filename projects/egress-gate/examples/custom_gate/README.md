# Minimal custom gate

This example adds a `keyword-deny` gate in one Python file. It is intentionally
small: if the configured keyword occurs in the request body, the gate denies
the request; otherwise it returns `proceed` and the pipeline continues.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the exact policy fields and the stable
   `keyword-deny` discriminator.
2. `KeywordDenyGate` declares what it reads and may return, then implements
   `_evaluate`.
3. `create_registry` registers the trusted Python class and finalizes the
   configuration schema.

Run the example from `projects/egress-gate/`:

```bash
uv run python -m egress_gate.cli \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  gates

uv run python -m egress_gate.cli \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  validate --policy examples/custom_gate/egress-gate-config.yaml

uv run python -m egress_gate.cli \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  evaluate \
  --policy examples/custom_gate/egress-gate-config.yaml \
  --cases examples/custom_gate/cases.yaml
```

Using `python -m` keeps the repository root importable for this local example.
An installed custom-gate package can use the regular `egress-gate` executable.

The first corpus case is denied by `block-secret-keyword`. The second gate
evaluation proceeds, so the pipeline's explicit `default_decision: allow`
determines the result.

This is a teaching example, not a robust content classifier. A production gate
should define deliberate text-decoding and matching semantics, enforce input
bounds, avoid request-derived error or finding content, honor the shared
timeout in all expensive work, and remain safe for concurrent calls.
