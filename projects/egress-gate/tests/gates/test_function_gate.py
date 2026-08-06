"""Tests for the resource-free function-gate authoring helper."""

from __future__ import annotations

from typing import ClassVar, Literal, Self

import pytest
from pydantic import ConfigDict, model_validator

from egress_gate.errors import GateContractError, GateRegistryError
from egress_gate.gates import (
    Gate,
    GateCapability,
    GateConfig,
    GateRegistry,
)
from egress_gate.request import HttpRequest, HttpTarget, RequestContext
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class _KeywordConfig(GateConfig):
    kind: Literal["keyword"]
    keyword: str


_registry = GateRegistry()


@_registry.gate(
    config=_KeywordConfig,
    capabilities=frozenset({GateCapability.READ_BODY, GateCapability.DENY}),
)
def _keyword_gate(
    request: HttpRequest,
    config: _KeywordConfig,
    *,
    timeout: Timeout,
) -> GateEvaluation:
    """Deny a request that contains the configured keyword."""
    timeout.raise_if_expired()
    if config.keyword.encode("utf-8") in request.body:
        return GateEvaluation.deny("keyword_denied")
    return GateEvaluation.proceed()


_invalid_registry = GateRegistry()


@_invalid_registry.gate(config=_KeywordConfig, capabilities=frozenset())
def _undeclared_deny(
    request: HttpRequest,
    config: _KeywordConfig,
    *,
    timeout: Timeout,
) -> GateEvaluation:
    del request, config, timeout
    return GateEvaluation.deny("keyword_denied")


def test_gate_decorator_builds_an_ordinary_resource_free_gate_type() -> None:
    assert issubclass(_keyword_gate, Gate)
    assert _keyword_gate.get_config_type() is _KeywordConfig
    assert _keyword_gate.get_resources_type() is None

    configured = _KeywordConfig(name="keywords", kind="keyword", keyword="SECRET")
    instance = _keyword_gate(configured, None)

    assert instance.config is configured
    assert (
        instance.evaluate(
            _request(body=b"contains SECRET"),
            timeout=Timeout.from_seconds(1),
        ).control.value
        == "deny"
    )


def test_decorated_gate_uses_the_standard_output_contract() -> None:
    configured = _KeywordConfig(name="keywords", kind="keyword", keyword="SECRET")

    with pytest.raises(GateContractError, match="undeclared deny"):
        _undeclared_deny(configured, None).evaluate(
            _request(), timeout=Timeout.from_seconds(1)
        )


def test_registry_bound_decorator_registers_and_seals_on_first_use() -> None:
    config = _registry.validate_config(
        {
            "gates": [
                {
                    "name": "keywords",
                    "kind": "keyword",
                    "keyword": "SECRET",
                }
            ],
            "default_decision": "allow",
        }
    )

    descriptions = _registry.describe_gates()
    assert tuple(item.gate_type for item in descriptions) == ("keyword",)
    assert descriptions[0].description == (
        "Deny a request that contains the configured keyword."
    )
    assert _registry.create_gate(config.gates[0]).get_config_type() is _KeywordConfig
    with pytest.raises(GateRegistryError, match="registry is in use"):
        _registry.register(_keyword_gate)


def test_decorated_gate_does_not_revalidate_config_during_evaluation() -> None:
    class CountingConfig(GateConfig):
        model_config = ConfigDict(revalidate_instances="always")

        kind: Literal["counting"]
        validation_count: ClassVar[int] = 0

        @model_validator(mode="after")
        def count_validation(self) -> Self:
            type(self).validation_count += 1
            return self

    registry = GateRegistry()

    @registry.gate(config=CountingConfig, capabilities=frozenset())
    def counting_gate(
        request: HttpRequest,
        config: CountingConfig,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        del request, config, timeout
        return GateEvaluation.proceed()

    configured = CountingConfig(name="counting", kind="counting")
    gate = counting_gate(configured, None)
    validation_count = CountingConfig.validation_count

    gate.evaluate(_request(), timeout=Timeout.from_seconds(1))
    gate.evaluate(_request(), timeout=Timeout.from_seconds(1))

    assert CountingConfig.validation_count == validation_count


def _request(*, body: bytes = b"ordinary") -> HttpRequest:
    return HttpRequest(
        context=RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        target=HttpTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/v1/items",
            query="",
        ),
        headers=(),
        body=body,
    )
