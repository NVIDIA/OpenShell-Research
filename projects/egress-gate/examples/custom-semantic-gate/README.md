# Custom semantic gate example

This example keeps semantic policy application-owned. It defines a typed
`SemanticGateResources` bundle, a provider-neutral `JudgeClient` protocol, and
a stateless deterministic `FakeJudgeClient`; no vendor SDK is part of Egress
Gate or this example.

The custom `semantic-judge` gate accepts strict configuration with a selected
`include` object, an allowlisted resource `profile`, a bounded policy, and two
modes:

- `enforce` turns a judge deny into a terminal deny with the configured stable
  label and reason code. A judge allow is a terminal allow.
- `observe` records the proposed decision using configured stable allow or deny
  labels, then always proceeds with an empty patch. It cannot make the request
  deny or mutate it.

The judge receives deterministic, bounded JSON containing only the configured
selected request fields. A body bound intentionally truncates the body, sets an
explicit `body_truncated` flag, and creates a blind spot. Deterministic
serialization does not prevent prompt
injection; it is a reproducibility and data-minimization boundary, not a
semantic security proof. A deterministic terminal allow can skip later gates,
while deterministic denies constrain the semantic gate's authority.

The included fake judge denies only when the selected body contains the stable
marker `[semantic-deny]`. Load the trusted example registry and run both
offline corpora from `projects/egress-gate/`:

```bash
PYTHONPATH=examples/custom-semantic-gate \
uv run --frozen egress-gate \
  --registry-factory custom_semantic_gate:create_registry evaluate \
  --policy examples/custom-semantic-gate/policies/deterministic-plus-semantics.yaml \
  --cases examples/custom-semantic-gate/corpora/deterministic-plus-semantics.yaml

PYTHONPATH=examples/custom-semantic-gate \
uv run --frozen egress-gate \
  --registry-factory custom_semantic_gate:create_registry evaluate \
  --policy examples/custom-semantic-gate/policies/privacy-before-semantics.yaml \
  --cases examples/custom-semantic-gate/corpora/privacy-before-semantics.yaml
```

The privacy-first composition replaces email addresses before the semantic
gate. Its observer still proceeds when the fake judge returns deny, and the
processor retains the earlier body replacement in the final allow result.
The corpus declares captured-like cases with `redacted: false` to make that
ordering explicit; do not put real captured traffic in a committed corpus.
