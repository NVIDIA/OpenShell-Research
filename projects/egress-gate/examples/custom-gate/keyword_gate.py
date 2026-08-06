"""A minimal custom Egress Gate implementation."""

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
    """Policy fields accepted by the custom gate."""

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
    """Deny requests whose body contains the configured UTF-8 keyword."""
    timeout.raise_if_expired()
    if config.keyword.encode("utf-8") in request.body:
        return GateEvaluation.deny("keyword_denied")
    return GateEvaluation.proceed()
