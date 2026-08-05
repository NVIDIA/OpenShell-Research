# Function-based custom gate

This example adds a `keyword-deny` gate in one Python file. If the configured
keyword occurs in the request body, the gate denies the request. Otherwise, it
returns `proceed`, and the pipeline continues.

The implementation has three pieces:

1. `KeywordDenyConfig` defines the exact policy fields and the stable
   `kind: keyword-deny` discriminator.
2. `registry.gate` turns the typed `keyword_deny` function into a standard
   resource-free gate type and adds it to the application registry.
3. The CLI loads that module-owned registry directly.

Run the example from `projects/egress-gate/`. First confirm that the custom
gate is installed in this registry:

```bash
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  gates list
```

Then test the policy against two saved requests:

```bash
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

The executable resolves the explicit `module:attribute` reference from the
working directory. The attribute can contain a registry or a zero-argument
registry factory. An installed custom-gate package works the same way.

The first case contains the configured keyword and is denied. The second gate
evaluation proceeds, so `default_decision: allow` determines its result.

This teaching gate searches the body bytes for the UTF-8 encoding of the
configured keyword. It is not a robust content classifier. The pipeline
processor already checks the `HttpRequest` limits; the gate does not repeat
those checks.

The decorator is a helper for small, stateless gates. See the runnable
[`class-based-gate`](../class-based-gate/) example when a gate needs reusable
initialization, a helper base, or typed operational resources.

A production gate must define its encoding, normalization, and matching
behavior. Add limits only for work that belongs to the gate. Do not put request
content in errors or findings. Check the shared timeout during expensive work,
and keep request state local.
