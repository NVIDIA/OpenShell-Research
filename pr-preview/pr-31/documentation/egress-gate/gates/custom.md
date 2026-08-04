---
title: Custom gates
description: Author and register trusted request-level gates.
agent_markdown: true
---

# Custom gates

Custom gates are trusted application code. They target the protobuf-free
`egress_gate.request` and `egress_gate.result` models and do not import gRPC,
protobuf, or `RequestProcessor` internals.

The repository includes a runnable
[minimal custom gate](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/custom_gate)
that pairs the implementation below with a policy and two offline evaluation
cases. From `projects/egress-gate/`, run it with:

```bash
uv run python -m egress_gate.cli \
  --registry-factory examples.custom_gate.keyword_gate:create_registry \
  evaluate \
  --policy examples/custom_gate/egress-gate-config.yaml \
  --cases examples/custom_gate/cases.yaml
```

```python
from typing import Literal

from egress_gate.gates import Gate, GateCapabilities, GateConfig, GateRegistry
from egress_gate.request import HttpRequest
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class KeywordDenyConfig(GateConfig):
    gate: Literal["keyword-deny"]
    keyword: str


class KeywordDenyGate(Gate[KeywordDenyConfig, None]):
    capabilities = GateCapabilities(reads_body=True, may_deny=True)
    finding_types = ()

    def _evaluate(
        self, request: HttpRequest, *, timeout: Timeout
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode("utf-8") in request.body:
            return GateEvaluation.deny("keyword_denied")
        return GateEvaluation.proceed()


def create_registry() -> GateRegistry:
    registry = GateRegistry(include_builtin_gates=True)
    registry.register(KeywordDenyGate)
    return registry.finalize()
```

`GateRegistry.finalize()` constructs the exact discriminated `pipeline.gates`
schema from the registered config types. Registry factories supply typed
`GateResources` objects for deployment-owned clients or profiles; policy
configuration cannot construct or replace those resources.

Declare output capabilities and finding types accurately. The public wrapper
rejects undeclared body replacements, header mutations, terminal allows,
denies, and finding types. Read capabilities are discovery metadata, not field
isolation. Implementations must keep request state local and be safe for
concurrent calls; no Python deep-immutability guarantee is made.

For a resource-backed gate, define a typed `GateResources` bundle and pass it
to `registry.register(..., resources=resources)`. Resources are trusted,
application-owned, concurrency-safe dependencies; policy configuration may
select behavior but cannot construct clients, provide credentials, or replace
the registered resource implementation.
