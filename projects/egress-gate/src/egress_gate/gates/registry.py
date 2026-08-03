"""Gate registration and finalized pipeline-schema construction."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from operator import getitem, or_
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    Self,
    TypeGuard,
    get_args,
    get_origin,
)

from pydantic import Field, TypeAdapter, ValidationError
from pydantic_core import PydanticUndefined

from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    GateConfigurationError,
    GateRegistryError,
)
from egress_gate.gates.base import (
    Gate,
    GateCapabilities,
    GateConfig,
    GateResources,
)
from egress_gate.gates.regex_body import RegexBodyGate
from egress_gate.gates.request_rules import RequestRulesGate
from egress_gate.result import FindingTypeDefinition
from egress_gate.timeout import Timeout

if TYPE_CHECKING:
    from egress_gate.config import EgressGateConfig


@dataclass(frozen=True)
class GateDescription:
    """Safe discovery metadata for one registered gate."""

    gate_type: str
    description: str
    capabilities: GateCapabilities
    finding_types: tuple[FindingTypeDefinition, ...]


class GateRegistry:
    """Register trusted gates and finalize their exact pipeline union."""

    def __init__(self, *, include_builtin_gates: bool = False) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._config_adapter: TypeAdapter[object] | None = None
        if include_builtin_gates:
            self.register(RegexBodyGate)
            self.register(RequestRulesGate)

    @property
    def is_finalized(self) -> bool:
        """Whether registration is closed and the policy schema is ready."""
        return self._config_adapter is not None

    def register(
        self,
        gate_type: type[object],
        *,
        resources: object = None,
    ) -> None:
        """Register one gate and its application-owned resources."""
        if self.is_finalized:
            raise GateRegistryError("cannot register after finalization")
        if not _is_gate_type(gate_type):
            raise GateRegistryError("registered gate type is invalid")
        if gate_type.__init__ is not Gate.__init__:
            raise GateRegistryError(
                "gate lifecycle contract requires Gate.__init__; use _initialize()"
            )
        if gate_type.evaluate is not Gate.evaluate:
            raise GateRegistryError(
                "gate lifecycle contract requires Gate.evaluate; implement _evaluate()"
            )

        try:
            gate_type._validate_class_contract()
            config_type = gate_type.get_config_type()
            resources_type = gate_type.get_resources_type()
        except (AttributeError, TypeError, GateConfigurationError):
            raise GateRegistryError("gate generic declaration is invalid") from None
        if not isinstance(config_type, type) or not issubclass(config_type, GateConfig):
            raise GateRegistryError("gate config type is invalid")
        gate_name = _gate_discriminator(config_type)
        if gate_name in self._registrations:
            raise GateRegistryError("gate discriminator is already registered")
        if any(
            registration.config_type is config_type
            for registration in self._registrations.values()
        ):
            raise GateRegistryError("gate config type is already registered")

        if resources_type is None:
            if resources is not None:
                raise GateRegistryError("resource-free gate received resources")
        elif resources is None or not isinstance(resources, resources_type):
            raise GateRegistryError("gate resources do not match their declared type")

        self._registrations[gate_name] = _Registration(
            gate_type=gate_type,
            config_type=config_type,
            resources=resources,
        )

    def finalize(self) -> Self:
        """Freeze registration and build the exact pipeline config union."""
        if self.is_finalized:
            return self
        try:
            config_type = _build_egress_gate_config_type(
                tuple(
                    registration.config_type
                    for registration in self._registrations.values()
                )
            )
        except (TypeError, ValueError):
            raise GateRegistryError("cannot finalize an empty gate registry") from None
        self._config_adapter = TypeAdapter[object](config_type)
        return self

    def validate_config(self, values: object) -> EgressGateConfig[GateConfig]:
        """Parse and validate one complete pipeline without preparing gates."""
        if not isinstance(values, Mapping):
            raise EgressGateError(ErrorCode.CONFIG_INVALID)
        try:
            config_value = self._require_config_adapter().validate_python(dict(values))
        except (TypeError, ValueError, ValidationError):
            raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        if not _is_egress_gate_config(config_value):
            raise EgressGateError(ErrorCode.CONFIG_INVALID)
        config = config_value
        for configured_gate in config.pipeline.gates:
            registration = self._resolve_registration(configured_gate.config)
            try:
                registration.gate_type.validate_config(
                    configured_gate.config,
                    registration.resources,
                )
            except GateConfigurationError:
                raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        return config

    def create_gate(
        self,
        config: GateConfig,
        *,
        timeout: Timeout | None = None,
    ) -> Gate[GateConfig, GateResources | None]:
        """Construct one initialized gate from its exact validated config."""
        registration = self._resolve_registration(config)
        if type(config) is not registration.config_type:
            raise GateRegistryError("gate config concrete type is invalid")
        return registration.gate_type(
            config,
            registration.resources,
            timeout=timeout,
        )

    def configuration_json_schema(self) -> dict[str, object]:
        """Return the finalized complete pipeline JSON Schema."""
        return self._require_config_adapter().json_schema()

    def describe_gates(self) -> tuple[GateDescription, ...]:
        """Return safe gate metadata without constructing runtime gates."""
        return tuple(
            GateDescription(
                gate_type=gate_name,
                description=_gate_description(registration.gate_type),
                capabilities=registration.gate_type.capabilities,
                finding_types=registration.gate_type.finding_types,
            )
            for gate_name, registration in self._registrations.items()
        )

    @staticmethod
    def policy_fingerprint(config: EgressGateConfig[GateConfig]) -> str:
        """Return the canonical fingerprint passed into a prepared processor."""
        canonical = json.dumps(
            config.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _resolve_registration(self, config: GateConfig) -> _Registration:
        if not self.is_finalized:
            raise GateRegistryError("gate registry is not finalized")
        try:
            gate_name = getattr(config, "gate")
            if not isinstance(gate_name, str):
                raise AttributeError
            return self._registrations[gate_name]
        except (AttributeError, KeyError):
            raise GateRegistryError("gate config is not registered") from None

    def _require_config_adapter(self) -> TypeAdapter[object]:
        if self._config_adapter is None:
            raise GateRegistryError("gate registry is not finalized")
        return self._config_adapter


def create_builtin_registry() -> GateRegistry:
    """Build the finalized registry shipped by the base package."""
    return GateRegistry(include_builtin_gates=True).finalize()


@dataclass(frozen=True)
class _Registration:
    gate_type: type[Gate[GateConfig, GateResources | None]]
    config_type: type[GateConfig]
    resources: GateResources | None


def _build_egress_gate_config_type(
    config_types: Sequence[type[GateConfig]],
) -> object:
    from egress_gate.config import EgressGateConfig

    if not config_types:
        raise ValueError("at least one gate config type must be registered")
    registered_union = reduce(or_, config_types)
    registered_config = getitem(
        Annotated,
        (registered_union, Field(discriminator="gate")),
    )
    pipeline_type: object = getattr(EgressGateConfig, "__class_getitem__")(
        registered_config
    )
    if not _is_egress_gate_config_type(pipeline_type):
        raise TypeError("Pydantic did not construct a pipeline config type")
    return pipeline_type


def _is_gate_type(
    value: object,
) -> TypeGuard[type[Gate[GateConfig, GateResources | None]]]:
    return isinstance(value, type) and issubclass(value, Gate)


def _is_egress_gate_config(
    value: object,
) -> TypeGuard[EgressGateConfig[GateConfig]]:
    from egress_gate.config import EgressGateConfig

    return isinstance(value, EgressGateConfig)


def _is_egress_gate_config_type(
    value: object,
) -> TypeGuard[type[EgressGateConfig[GateConfig]]]:
    from egress_gate.config import EgressGateConfig

    return isinstance(value, type) and issubclass(value, EgressGateConfig)


def _gate_discriminator(config_type: type[GateConfig]) -> str:
    field = config_type.model_fields.get("gate")
    if field is None:
        raise GateRegistryError("gate config lacks a gate discriminator")
    if get_origin(field.annotation) is not Literal:
        raise GateRegistryError("gate discriminator must be one string Literal")
    values = get_args(field.annotation)
    if len(values) != 1 or not isinstance(values[0], str):
        raise GateRegistryError("gate discriminator must be one string Literal")
    gate_name = values[0]
    if _GATE_NAME.fullmatch(gate_name) is None:
        raise GateRegistryError("gate discriminator is invalid")
    if field.default is not PydanticUndefined and field.default != gate_name:
        raise GateRegistryError("gate discriminator default is inconsistent")
    return gate_name


def _gate_description(gate_type: type[object]) -> str:
    description = inspect.getdoc(gate_type) or ""
    first_line = description.splitlines()[0] if description else ""
    if len(first_line.encode("utf-8")) > 1024:
        return ""
    return first_line


_GATE_NAME = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")


__all__ = [
    "GateDescription",
    "GateRegistry",
    "create_builtin_registry",
]
