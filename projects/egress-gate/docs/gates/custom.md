---
title: Custom gates
description: Author and register trusted request-level gates.
agent_markdown: true
---

# Custom gates

Custom gates are trusted application code. They target the protobuf-free
`egress_gate.request` and `egress_gate.result` models and do not import gRPC,
protobuf, or `RequestProcessor` internals. Use the function helper for a small,
stateless gate. Use the class-based API when a gate needs initialization,
helper-base behavior, or operational resources.

The repository includes a runnable
[minimal custom gate](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/custom-gate)
that pairs the implementation below with a policy and two offline evaluation
cases. Run it from `projects/egress-gate/`; `uv` prepares the project environment
automatically:

```bash title="Run the custom-gate example"
uv run egress-gate \
  --registry examples.custom-gate.keyword_gate:registry \
  evaluate \
  --policy examples/custom-gate/egress-gate-config.yaml \
  --cases examples/custom-gate/cases.yaml
```

The executable resolves the explicit `module:attribute` reference from the
working directory. The attribute can contain a registry or a zero-argument
registry factory. A packaged deployment can resolve the same reference from an
installed custom-gate package.

```python title="examples/custom-gate/keyword_gate.py"
from typing import Literal

from egress_gate.gates import (
    GateCapability,
    GateConfig,
    GateRegistry,
)
from egress_gate.request import HttpRequest
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class KeywordDenyConfig(GateConfig):
    kind: Literal["keyword-deny"]
    keyword: str


registry = GateRegistry(include_builtin_gates=True)


@registry.gate(
    config=KeywordDenyConfig,
    capabilities=frozenset({GateCapability.READ_BODY, GateCapability.DENY}),
)
def keyword_deny(
    request: HttpRequest,
    config: KeywordDenyConfig,
    *,
    timeout: Timeout,
) -> GateEvaluation:
    timeout.raise_if_expired()
    if config.keyword.encode("utf-8") in request.body:
        return GateEvaluation.deny("keyword_denied")
    return GateEvaluation.proceed()


```

`registry.gate` creates an ordinary resource-free `Gate` type and adds it to
the application-owned registry. The existing public wrapper still validates
configuration, capabilities, findings, mutations, timeouts, and errors. The
registry stays open while the module declares gates. The CLI or service seals
it automatically on first use.

On first use, `GateRegistry` constructs the exact discriminated `gates` schema
from the registered config types. A registry factory remains available when a
deployment must construct typed `GateResources` dynamically. Policy
configuration cannot construct or replace those resources.

`GateConfig` supplies the common required `name` field. Custom config classes
inherit it and do not redefine or alias it. Each config declares one required
literal `kind` and keeps that serialized field name. Nested unions follow the
same discriminator rule. This gives policy parsers and generated schemas one
consistent way to select an exact configuration shape.

Declare capabilities and finding types accurately. The public wrapper
rejects undeclared body replacements, header mutations, terminal decisions,
and finding types. Read capabilities are discovery metadata. They do not limit
which request fields trusted Python code can read. Keep request state local so
the gate is safe for concurrent calls.

Declare capabilities as a `frozenset` of `GateCapability` values. Read access,
body replacement, header mutation, terminal allow, and deny are explicit.
Resource use comes from the gate's `GateResources` type, and finding support
comes from `finding_types`, so a gate does not declare either fact twice.

A custom gate must not edit its `HttpRequest` input. To propose a change, return
`GateEvaluation.proceed(request_mutations=RequestMutations(...))`. The pipeline
processor validates the mutations and creates the next immutable snapshot.

## Class-based gates

The function helper does not replace the class-based extension API. Implement
`Gate[ConfigType, ResourcesType]` directly when a gate needs `_initialize`, a
helper base such as `Utf8BodyGate`, or typed `GateResources`. Resource-free
class-based gates use `registry.register(GateType)`.

```python title="Equivalent class-based gate"
from egress_gate.gates import Gate, GateCapability


class KeywordDenyGate(Gate[KeywordDenyConfig, None]):
    capabilities = frozenset(
        {GateCapability.READ_BODY, GateCapability.DENY}
    )
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode("utf-8") in request.body:
            return GateEvaluation.deny("keyword_denied")
        return GateEvaluation.proceed()
```

For a resource-backed gate, define a typed `GateResources` bundle. Pass the
bundle to `registry.register(..., resources=resources)`. Resources are trusted,
application-owned dependencies that must be safe for concurrent use. Policy
configuration can select behavior. It cannot construct clients, provide
credentials, or replace the registered resource implementation.
