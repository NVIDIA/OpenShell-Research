# Deterministic request-rules example

This example uses the built-in `request-rules` gate to allow only known
read-oriented GitHub requests and deny destructive `DELETE` requests. The
policy default is deny, so a request that matches neither rule is not
implicitly allowed.

Inspect the built-in registry and exact generated schema from the project
directory:

```bash
cd projects/egress-gate
uv run egress-gate gates
uv run egress-gate configuration-schema
```

The standalone pipeline is in `egress-gate-config.yaml`; `policy.yaml` embeds
the same pipeline in an OpenShell `network_middlewares` entry. Register the
middleware with the OpenShell gateway using the service address, then create a
sandbox with `policy.yaml`.

The deny rule wins whenever it matches, even if an allow rule appears earlier
in the list. Otherwise the first matching allow is terminal and prevents later
pipeline gates from running. A match finding contains only the configured rule
name and its allow/deny severity; request content is never copied into it.
