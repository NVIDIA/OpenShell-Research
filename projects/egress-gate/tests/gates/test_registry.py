"""Registry finalization and exact pipeline-schema tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import pytest
from pydantic import Field

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
from egress_gate.request_processor import RequestProcessor
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class _RegistryConfig(GateConfig):
    kind: Literal["registry-test"]
    answer: int


class _RegistryGate(Gate[_RegistryConfig, None]):
    """A small resource-free gate used to exercise registry assembly."""

    capabilities = GateCapabilities(reads_context=True)
    finding_types = ()

    def _initialize(self, *, timeout: Timeout | None = None) -> None:
        self.preparation_timeout = timeout

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
    kind: Literal["resource-test"]


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


def test_builtin_registry_is_finalized_and_contains_only_regex() -> None:
    registry = create_builtin_registry()

    assert registry.is_finalized
    assert tuple(item.gate_type for item in registry.describe_gates()) == ("regex",)
    schema = registry.configuration_json_schema()
    assert _discriminator_names(schema) == {"kind"}
    assert "pipeline" in str(schema.get("properties"))
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    regex_schema = next(
        value for key, value in definitions.items() if key == "RegexConfig"
    )
    assert isinstance(regex_schema, Mapping)
    required = next(value for key, value in regex_schema.items() if key == "required")
    assert isinstance(required, list)
    assert "kind" in required
    assert "scan" in required
    body_scan_schema = next(
        value for key, value in definitions.items() if key == "RegexBodyScan"
    )
    header_scan_schema = next(
        value for key, value in definitions.items() if key == "RegexHeaderScan"
    )
    assert "RegexReplaceAction" in str(body_scan_schema)
    assert "RegexReplaceAction" not in str(header_scan_schema)

    with pytest.raises(EgressGateError):
        registry.validate_config(
            _pipeline(
                {
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": "token",
                                "rules": [{"pattern": "secret", "confidence": "high"}],
                            }
                        ]
                    },
                    "scan": {"kind": "body", "action": {"kind": "detect"}},
                }
            )
        )


def test_registry_validates_exact_pipeline_and_gate_config() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()

    config = registry.validate_config(
        _pipeline({"kind": "registry-test", "answer": 42})
    )

    assert config.pipeline.default_decision.value == "allow"
    assert type(config.pipeline.gates[0].config) is _RegistryConfig
    gate = registry.create_gate(config.pipeline.gates[0].config)
    assert type(gate) is _RegistryGate
    assert gate.config.answer == 42


def test_registry_requires_an_explicit_gate_discriminator() -> None:
    class DefaultedConfig(GateConfig):
        kind: Literal["defaulted"] = "defaulted"

    class DefaultedGate(Gate[DefaultedConfig, None]):
        capabilities = GateCapabilities()
        finding_types = ()

        def _evaluate(
            self,
            request: HttpRequest,
            *,
            timeout: Timeout,
        ) -> GateEvaluation:
            del request, timeout
            return GateEvaluation.proceed()

    with pytest.raises(GateRegistryError, match="gate kind must be required"):
        GateRegistry().register(DefaultedGate)

    class FactoryDefaultedConfig(GateConfig):
        kind: Literal["factory-defaulted"] = Field(
            default_factory=lambda: "factory-defaulted"
        )

    class FactoryDefaultedGate(Gate[FactoryDefaultedConfig, None]):
        capabilities = GateCapabilities()
        finding_types = ()

        def _evaluate(
            self,
            request: HttpRequest,
            *,
            timeout: Timeout,
        ) -> GateEvaluation:
            del request, timeout
            return GateEvaluation.proceed()

    with pytest.raises(GateRegistryError, match="gate kind must be required"):
        GateRegistry().register(FactoryDefaultedGate)


def test_registry_forwards_the_shared_preparation_timeout() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()
    config = registry.validate_config(
        _pipeline({"kind": "registry-test", "answer": 42})
    )
    timeout = Timeout.from_seconds(1)

    gate = registry.create_gate(config.pipeline.gates[0].config, timeout=timeout)

    assert isinstance(gate, _RegistryGate)
    assert gate.preparation_timeout is timeout


def test_registry_prepares_the_production_processor_from_validated_config() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()
    config = registry.validate_config(
        _pipeline({"kind": "registry-test", "answer": 42})
    )

    processor = registry.prepare_processor(
        config,
        timeout=Timeout.from_seconds(1),
    )

    assert isinstance(processor, RequestProcessor)


def test_registry_injects_typed_application_resources() -> None:
    resources = _ResourceBundle("shared-client")
    registry = GateRegistry()
    registry.register(_ResourceGate, resources=resources)
    registry.finalize()

    config = registry.validate_config(_pipeline({"kind": "resource-test"}))
    gate = registry.create_gate(config.pipeline.gates[0].config)

    assert gate.resources is resources

    with pytest.raises(GateRegistryError):
        GateRegistry().register(_ResourceGate)
    with pytest.raises(GateRegistryError):
        GateRegistry().register(_ResourceGate, resources=object())


def test_registry_rejects_unknown_policy_shapes() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()

    for values in (
        {"unexpected": {}},
        _pipeline({"gate": "registry-test", "answer": 1}),
        _pipeline({"kind": "missing", "answer": 1}),
        _pipeline({"kind": "registry-test", "answer": 1, "extra": True}),
        {
            "pipeline": {
                "gates": [
                    {"name": "one", "config": {"kind": "registry-test", "answer": 1}}
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

    first = registry.validate_config(_pipeline({"kind": "registry-test", "answer": 1}))
    second = registry.validate_config(_pipeline({"kind": "registry-test", "answer": 2}))
    assert registry.policy_fingerprint(first) != registry.policy_fingerprint(second)
    assert registry.policy_fingerprint(first) == registry.policy_fingerprint(first)


def test_registry_rejects_duplicate_gate_names_before_preparation() -> None:
    registry = GateRegistry()
    registry.register(_RegistryGate)
    registry.finalize()
    values = {
        "pipeline": {
            "gates": [
                {"name": "same", "config": {"kind": "registry-test", "answer": 1}},
                {"name": "same", "config": {"kind": "registry-test", "answer": 2}},
            ],
            "default_decision": "allow",
        }
    }

    with pytest.raises(EgressGateError):
        registry.validate_config(values)


def _discriminator_names(value: object) -> set[object]:
    if isinstance(value, Mapping):
        names = {
            discriminator.get("propertyName")
            for key, discriminator in value.items()
            if key == "discriminator" and isinstance(discriminator, Mapping)
        }
        for nested in value.values():
            names.update(_discriminator_names(nested))
        return names
    if isinstance(value, list | tuple):
        names: set[object] = set()
        for nested in value:
            names.update(_discriminator_names(nested))
        return names
    return set()
