---
title: Custom gates
description: Author and register trusted request-level gates.
agent_markdown: true
---

# Custom gates

Custom gates are trusted application code. They target the protobuf-free
`egress_gate.request` and `egress_gate.result` models and do not import gRPC,
protobuf, or `RequestProcessor` internals.

```python
from typing import Literal

from egress_gate.gates import Gate, GateCapabilities, GateConfig, GateRegistry
from egress_gate.request import HttpRequest
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class KeywordConfig(GateConfig):
    gate: Literal["keyword-deny"] = "keyword-deny"
    keyword: str


class KeywordGate(Gate[KeywordConfig, None]):
    capabilities = GateCapabilities(reads_body=True, may_deny=True)
    finding_types = ()

    def _evaluate(
        self, request: HttpRequest, *, timeout: Timeout
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode() in request.body:
            return GateEvaluation.deny("egress_gate_keyword_denied")
        return GateEvaluation.proceed()


def create_registry() -> GateRegistry:
    registry = GateRegistry(include_builtin_gates=True)
    registry.register(KeywordGate)
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
