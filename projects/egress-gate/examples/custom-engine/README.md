# Custom gate example

`custom_engine.py` defines a trusted `KeywordDenyGate` with a strict
`KeywordDenyConfig`, a declared `keyword_match` finding type, and no transport
or processor imports. `create_registry()` registers it alongside the built-in
`regex-body` gate and returns a finalized `GateRegistry`.

Run discovery and schema inspection from this directory:

```bash
PYTHONPATH=. uv run egress-gate \
  --registry-factory custom_engine:create_registry gates
PYTHONPATH=. uv run egress-gate \
  --registry-factory custom_engine:create_registry configuration-schema
```

The policy in `egress-gate-config.yaml` denies requests containing the
configured keyword and uses `default_decision: allow` when the gate proceeds.
The OpenShell policy embeds the same pipeline under its middleware entry.

Custom gates are trusted Python code. Their read capabilities are discovery
metadata, while output capabilities and declared finding types are enforced by
the public `Gate.evaluate` wrapper. Implementations must be safe for
concurrent calls; the runtime does not claim Python-level deep immutability.
