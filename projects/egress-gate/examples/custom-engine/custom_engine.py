"""Complete custom request-level gate example."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from egress_gate.gates import (
    FindingTypeDefinition,
    Gate,
    GateCapabilities,
    GateConfig,
    GateRegistry,
)
from egress_gate.request import HttpRequest
from egress_gate.result import Finding, GateEvaluation, ReasonCode
from egress_gate.timeout import Timeout


class KeywordDenyConfig(GateConfig):
    """Policy-owned configuration for keyword denial."""

    gate: Literal["keyword-deny"] = "keyword-deny"
    keyword: str = Field(min_length=1, max_length=256, repr=False)
    reason_code: ReasonCode = "egress_gate_keyword_denied"


class KeywordDenyGate(Gate[KeywordDenyConfig, None]):
    """Deny a request containing one configured byte-oriented keyword."""

    capabilities = GateCapabilities(
        reads_body=True, produces_findings=True, may_deny=True
    )
    finding_types = (FindingTypeDefinition(type="keyword_match"),)

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode("utf-8") not in request.body:
            return GateEvaluation.proceed()
        return GateEvaluation.deny(
            self.config.reason_code,
            findings=(Finding(type="keyword_match", label="configured_keyword"),),
        )


def create_registry() -> GateRegistry:
    """Create a finalized registry containing built-in and custom gates."""
    registry = GateRegistry(include_builtin_gates=True)
    registry.register(KeywordDenyGate)
    return registry.finalize()
