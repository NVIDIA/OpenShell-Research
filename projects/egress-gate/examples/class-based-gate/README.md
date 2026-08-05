# Class-based custom gate

This example implements the same `keyword-deny` behavior as the
function-based example, but uses the full `Gate` API. Use this form when a gate
needs initialization, a helper base, or typed operational resources.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the policy fields and `kind` discriminator.
2. `KeywordDenyGate._evaluate` implements the request decision.
3. The module creates a registry and registers the gate class.

Run the example from `projects/egress-gate/`. First inspect the registry:

```bash
uv run egress-gate \
  --registry examples.class-based-gate.keyword_gate:registry \
  gates list
```

Then test the policy against two saved requests:

```bash
uv run egress-gate \
  --registry examples.class-based-gate.keyword_gate:registry \
  evaluate \
  --policy examples/class-based-gate/egress-gate-config.yaml \
  --cases examples/class-based-gate/cases.yaml
```

The first case contains the configured keyword and is denied. The second gate
evaluation proceeds, so `default_decision: allow` determines its result.

The base class owns construction and the public `evaluate` wrapper. A custom
class implements `_evaluate` and reads its validated configuration from
`self.config`. Do not override `__init__` or `evaluate`. Use `_initialize` for
reusable derived state.

This teaching gate searches the body bytes for the UTF-8 encoding of the
configured keyword. It is not a robust content classifier. A production gate
must define its encoding, normalization, and matching behavior. Add limits only
for work that belongs to the gate. Do not put request content in errors or
findings. Check the shared timeout during expensive work, and keep request state
local.
