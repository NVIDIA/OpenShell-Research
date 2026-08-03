"""Registry finalization and exact pipeline-schema tests."""

from __future__ import annotations

from typing import Literal

import pytest

from egress_gate.errors import EgressGateError, GateRegistryError
from egress_gate.gates import (
    Gate,
    GateCapabilities,
    GateConfig,
    GateRegistry,
    GateResources,
    create_builtin_registry,
)
from egress_gate.request import HttpRequest
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class _RegistryConfig(GateConfig):
    gate: Literal["registry-test"] = "registry-test"
    answer: int


class _RegistryGate(Gate[_RegistryConfig, None]):
    """A small resource-free gate used to exercise registry assembly."""

    capabilities = GateCapabilities(reads_context=True)
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        del request
        timeout.raise_if_expired()
        return GateEvaluation.proceed()


class _ResourceConfig(GateConfig):
    gate: Literal["resource-test"] = "resource-test"


class _ResourceBundle(GateResources):
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _ResourceGate(Gate[_ResourceConfig, _ResourceBundle]):
    capabilities = GateCapabilities(uses_resources=True)
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        del request
        timeout.raise_if_expired()
        return GateEvaluation.proceed()


def _pipeline(config: dict[str, object]) -> dict[str, object]:
    return {
        "pipeline": {
            "gates": [{"name": "one", "config": config}],
            "default_decision": "allow",
        }
    }


def test_builtin_registry_is_finalized_and_contains_only_regex_body() -> None:
    registry = create_builtin_registry()

    assert registry.is_finalized
    assert tuple(item.gate_type for item in registry.describe_gates()) == (
        "regex-body",
    )
    schema = registry.configuration_json_schema()
    assert "pipeline" in str(schema.get("properties"))
    assert "entity_processing" not in str(schema)
    assert "request-rules" not in str(schema)


def test_registry_validates_exact_pipeline_and_gate_config() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()

    config = registry.validate_config(
        _pipeline({"gate": "registry-test", "answer": 42})
    )

    assert config.pipeline.default_decision.value == "allow"
    assert type(config.pipeline.gates[0].config) is _RegistryConfig
    gate = registry.create_gate(config.pipeline.gates[0].config)
    assert type(gate) is _RegistryGate
    assert gate.config.answer == 42


def test_registry_injects_typed_application_resources() -> None:
    resources = _ResourceBundle("shared-client")
    registry = GateRegistry()
    registry.register(_ResourceGate, resources=resources)
    registry.finalize()

    config = registry.validate_config(_pipeline({"gate": "resource-test"}))
    gate = registry.create_gate(config.pipeline.gates[0].config)

    assert gate.resources is resources

    with pytest.raises(GateRegistryError):
        GateRegistry().register(_ResourceGate)
    with pytest.raises(GateRegistryError):
        GateRegistry().register(_ResourceGate, resources=object())


def test_registry_rejects_old_or_unknown_policy_shapes() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()

    for values in (
        {"entity_processing": {"stages": []}},
        _pipeline({"gate": "missing", "answer": 1}),
        _pipeline({"gate": "registry-test", "answer": 1, "extra": True}),
        {
            "pipeline": {
                "gates": [
                    {"name": "one", "config": {"gate": "registry-test", "answer": 1}}
                ],
            }
        },
    ):
        with pytest.raises(EgressGateError):
            registry.validate_config(values)


def test_registry_lifecycle_and_fingerprint_are_deterministic() -> None:
    registry = GateRegistry()
    with pytest.raises(GateRegistryError):
        registry.finalize()

    registry.register(_RegistryGate)
    registry.finalize()
    with pytest.raises(GateRegistryError):
        registry.register(_ResourceGate)

    first = registry.validate_config(_pipeline({"gate": "registry-test", "answer": 1}))
    second = registry.validate_config(_pipeline({"gate": "registry-test", "answer": 2}))
    assert registry.policy_fingerprint(first) != registry.policy_fingerprint(second)
    assert registry.policy_fingerprint(first) == registry.policy_fingerprint(first)


def test_registry_rejects_duplicate_gate_names_before_preparation() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()
    values = {
        "pipeline": {
            "gates": [
                {"name": "same", "config": {"gate": "registry-test", "answer": 1}},
                {"name": "same", "config": {"gate": "registry-test", "answer": 2}},
            ],
            "default_decision": "allow",
        }
    }

    with pytest.raises(EgressGateError):
        registry.validate_config(values)
