"""Gate registration and lazy policy-schema construction."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import reduce
from operator import getitem, or_
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    Protocol,
    TypeGuard,
    get_args,
    get_origin,
)

from pydantic import Field, TypeAdapter, ValidationError
from typing_extensions import TypeVar

from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    GateConfigurationError,
    GateRegistryError,
)
from egress_gate.gates.base import (
    Gate,
    GateCapability,
    GateConfig,
    GateConfigT,
    GateResources,
)
from egress_gate.gates.regex import RegexGate
from egress_gate.request import HttpRequest
from egress_gate.result import FindingTypeDefinition, GateEvaluation
from egress_gate.timeout import Timeout

if TYPE_CHECKING:
    from egress_gate.config import EgressGateConfig
    from egress_gate.request_processor import RequestProcessor


@dataclass(frozen=True)
class GateDescription:
    """Safe discovery metadata for one registered gate."""

    gate_type: str
    description: str
    capabilities: frozenset[GateCapability]
    finding_types: tuple[FindingTypeDefinition, ...]
    resource_type: str | None
    config_type: str


class PolicyValidationCategory(StrEnum):
    """Content-safe category for one policy schema failure."""

    REQUIRED_FIELD_MISSING = "required field is missing"
    UNKNOWN_FIELD = "unknown field is not allowed"
    UNKNOWN_VARIANT = "kind does not identify an installed variant"
    INVALID_VALUE = "value has the wrong type, shape, or constraints"


class PolicyValidationError(EgressGateError):
    """Cataloged policy failure with a trusted structural location."""

    def __init__(
        self,
        *,
        path: tuple[str | int, ...],
        category: PolicyValidationCategory,
    ) -> None:
        super().__init__(ErrorCode.CONFIG_INVALID)
        self.path = path
        self.category = category

    @property
    def formatted_path(self) -> str:
        """Render the trusted field path without submitted values."""
        rendered = ""
        for component in self.path:
            if isinstance(component, int):
                rendered += f"[{component}]"
            elif rendered:
                rendered += f".{component}"
            else:
                rendered = component
        return rendered or "policy"

    @classmethod
    def from_validation_error(
        cls,
        error: ValidationError,
        *,
        schema: Mapping[str, object],
    ) -> PolicyValidationError:
        """Reduce Pydantic diagnostics to one bounded, content-safe issue."""
        known_fields = _schema_property_names(schema)
        issues = error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        issue = min(issues, key=lambda item: _validation_error_priority(item["type"]))
        path = tuple(
            component
            for component in issue["loc"]
            if isinstance(component, int)
            or (isinstance(component, str) and component in known_fields)
        )
        return cls(
            path=path,
            category=_validation_error_category(issue["type"]),
        )


class GateRegistry:
    """Collect trusted gates and seal their exact policy union on first use."""

    def __init__(self, *, include_builtin_gates: bool = False) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._config_adapter: TypeAdapter[object] | None = None
        if include_builtin_gates:
            self.register(RegexGate)

    def register(
        self,
        gate_type: type[object],
        *,
        resources: object = None,
    ) -> None:
        """Register one gate and its application-owned resources."""
        if self._config_adapter is not None:
            raise GateRegistryError("cannot register after the registry is in use")
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
        _validate_common_gate_config_fields(config_type)
        gate_kind = _gate_kind(config_type)
        if gate_kind in self._registrations:
            raise GateRegistryError("gate kind is already registered")
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

        self._registrations[gate_kind] = _Registration(
            gate_type=gate_type,
            config_type=config_type,
            resources=resources,
        )

    def gate(
        self,
        *,
        config: type[GateConfigT],
        capabilities: frozenset[GateCapability],
        finding_types: tuple[FindingTypeDefinition, ...] = (),
    ) -> Callable[[_GateFunction[GateConfigT]], type[Gate[GateConfig, None]]]:
        """Register a typed function as one resource-free gate."""
        config_type = config
        declared_capabilities = capabilities
        declared_finding_types = finding_types

        def decorate(
            evaluate: _GateFunction[GateConfigT],
        ) -> type[Gate[GateConfig, None]]:
            class FunctionGate(Gate[GateConfig, None]):
                _decorated_config_type = config_type
                capabilities = declared_capabilities
                finding_types = declared_finding_types

                def _evaluate(
                    self,
                    request: HttpRequest,
                    *,
                    timeout: Timeout,
                ) -> GateEvaluation:
                    assert isinstance(self.config, config_type)
                    return evaluate(request, self.config, timeout=timeout)

            FunctionGate.__doc__ = inspect.getdoc(evaluate) or ""
            self.register(FunctionGate)
            return FunctionGate

        return decorate

    def validate_config(self, values: object) -> EgressGateConfig[GateConfig]:
        """Parse and validate one complete pipeline without preparing gates."""
        if not isinstance(values, Mapping):
            raise EgressGateError(ErrorCode.CONFIG_INVALID)
        try:
            config_value = self._require_config_adapter().validate_python(dict(values))
        except ValidationError as error:
            raise PolicyValidationError.from_validation_error(
                error,
                schema=self.configuration_json_schema(),
            ) from None
        except (TypeError, ValueError):
            raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        if not _is_egress_gate_config(config_value):
            raise EgressGateError(ErrorCode.CONFIG_INVALID)
        config = config_value
        for gate_index, configured_gate in enumerate(config.gates):
            registration = self._resolve_registration(configured_gate)
            try:
                registration.gate_type.validate_config(
                    configured_gate,
                    registration.resources,
                )
            except GateConfigurationError:
                raise PolicyValidationError(
                    path=("gates", gate_index),
                    category=PolicyValidationCategory.INVALID_VALUE,
                ) from None
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

    def prepare_processor(
        self,
        validated_config: EgressGateConfig[GateConfig],
        *,
        timeout: Timeout,
    ) -> RequestProcessor:
        """Prepare one processor from a validated policy configuration.

        This is the production preparation seam shared by the service and
        offline evaluation. Registration, policy validation, and resource
        ownership remain registry responsibilities; the returned processor
        owns only the prepared gates and immutable policy metadata.
        """
        from egress_gate.request_processor import RequestProcessor

        self._require_config_adapter()
        if not _is_egress_gate_config(validated_config):
            raise GateRegistryError("processor configuration is invalid")
        if not isinstance(timeout, Timeout):
            raise GateRegistryError("processor preparation timeout is invalid")

        prepared: list[tuple[str, str, Gate[GateConfig, GateResources | None]]] = []
        for configured_gate in validated_config.gates:
            timeout.raise_if_expired()
            gate_type = getattr(configured_gate, "kind", None)
            if not isinstance(gate_type, str):
                raise GateRegistryError("gate config discriminator is invalid")
            try:
                gate = self.create_gate(configured_gate, timeout=timeout)
            except GateConfigurationError:
                raise EgressGateError(ErrorCode.CONFIG_PREPARATION_FAILED) from None
            prepared.append((configured_gate.name, gate_type, gate))
        timeout.raise_if_expired()
        return RequestProcessor(
            validated_config,
            tuple(prepared),
            policy_fingerprint=self.policy_fingerprint(validated_config),
        )

    def configuration_json_schema(self) -> dict[str, object]:
        """Return the complete policy JSON Schema."""
        schema = self._require_config_adapter().json_schema()
        schema["title"] = "EgressGateConfig"
        schema["description"] = (
            "Flat policy for Egress Gate with ordered gates and a default decision."
        )
        return schema

    def describe_gates(self) -> tuple[GateDescription, ...]:
        """Return safe gate metadata without constructing gate instances."""
        self._require_config_adapter()
        return tuple(
            GateDescription(
                gate_type=gate_kind,
                description=_gate_description(registration.gate_type),
                capabilities=registration.gate_type.capabilities,
                finding_types=registration.gate_type.finding_types,
                resource_type=(
                    resources_type.__name__
                    if (resources_type := registration.gate_type.get_resources_type())
                    is not None
                    else None
                ),
                config_type=registration.config_type.__name__,
            )
            for gate_kind, registration in self._registrations.items()
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
        self._require_config_adapter()
        try:
            gate_kind = getattr(config, "kind")
            if not isinstance(gate_kind, str):
                raise AttributeError
            return self._registrations[gate_kind]
        except (AttributeError, KeyError):
            raise GateRegistryError("gate config is not registered") from None

    def _require_config_adapter(self) -> TypeAdapter[object]:
        if self._config_adapter is None:
            try:
                config_type = _build_egress_gate_config_type(
                    tuple(
                        registration.config_type
                        for registration in self._registrations.values()
                    )
                )
            except (TypeError, ValueError):
                raise GateRegistryError(
                    "gate registry has no registered gates"
                ) from None
            self._config_adapter = TypeAdapter[object](config_type)
        return self._config_adapter


def create_builtin_registry() -> GateRegistry:
    """Build the registry shipped by the base package."""
    return GateRegistry(include_builtin_gates=True)


@dataclass(frozen=True)
class _Registration:
    gate_type: type[Gate[GateConfig, GateResources | None]]
    config_type: type[GateConfig]
    resources: GateResources | None


_FunctionConfigT = TypeVar(
    "_FunctionConfigT",
    bound=GateConfig,
    contravariant=True,
)


class _GateFunction(Protocol[_FunctionConfigT]):
    def __call__(
        self,
        request: HttpRequest,
        config: _FunctionConfigT,
        *,
        timeout: Timeout,
    ) -> GateEvaluation: ...


def _build_egress_gate_config_type(
    config_types: Sequence[type[GateConfig]],
) -> object:
    from egress_gate.config import EgressGateConfig

    if not config_types:
        raise ValueError("at least one gate config type must be registered")
    registered_union = reduce(or_, config_types)
    registered_config = getitem(
        Annotated,
        (registered_union, Field(discriminator="kind")),
    )
    return getattr(EgressGateConfig, "__class_getitem__")(registered_config)


def _is_gate_type(
    value: object,
) -> TypeGuard[type[Gate[GateConfig, GateResources | None]]]:
    return isinstance(value, type) and issubclass(value, Gate)


def _is_egress_gate_config(
    value: object,
) -> TypeGuard[EgressGateConfig[GateConfig]]:
    from egress_gate.config import EgressGateConfig

    return isinstance(value, EgressGateConfig)


def _gate_kind(config_type: type[GateConfig]) -> str:
    field = config_type.model_fields.get("kind")
    if field is None:
        raise GateRegistryError("gate config lacks a kind discriminator")
    if get_origin(field.annotation) is not Literal:
        raise GateRegistryError("gate kind must be one string Literal")
    values = get_args(field.annotation)
    if len(values) != 1 or not isinstance(values[0], str):
        raise GateRegistryError("gate kind must be one string Literal")
    gate_kind = values[0]
    if _GATE_KIND_PATTERN.fullmatch(gate_kind) is None:
        raise GateRegistryError("gate kind is invalid")
    if not field.is_required():
        raise GateRegistryError("gate kind must be required")
    return gate_kind


def _validate_common_gate_config_fields(config_type: type[GateConfig]) -> None:
    required_model_config = {
        "extra": "forbid",
        "strict": True,
        "frozen": True,
        "hide_input_in_errors": True,
        "validate_default": True,
    }
    if any(
        config_type.model_config.get(setting) != value
        for setting, value in required_model_config.items()
    ):
        raise GateRegistryError(
            "gate config must retain the strict immutable model configuration"
        )

    for ancestor in config_type.__mro__:
        if ancestor is GateConfig:
            break
        if "name" in ancestor.__dict__.get("__annotations__", {}):
            raise GateRegistryError(
                "gate config must inherit name without redefining it"
            )
    else:
        raise GateRegistryError("gate config type is invalid")

    for field_name in ("name", "kind"):
        field = config_type.model_fields.get(field_name)
        if field is None:
            continue
        aliases = (field.alias, field.validation_alias, field.serialization_alias)
        if any(alias not in (None, field_name) for alias in aliases):
            raise GateRegistryError(
                "gate config name and kind must use their canonical field names"
            )


def _gate_description(gate_type: type[object]) -> str:
    description = inspect.getdoc(gate_type) or ""
    first_line = description.splitlines()[0] if description else ""
    if len(first_line.encode("utf-8")) > 1024:
        return ""
    return first_line


def _schema_property_names(value: object) -> frozenset[str]:
    names: set[str] = set()
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            names.update(key for key in properties if isinstance(key, str))
        for nested in value.values():
            names.update(_schema_property_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_schema_property_names(nested))
    return frozenset(names)


def _validation_error_priority(error_type: object) -> int:
    return {
        "missing": 0,
        "extra_forbidden": 1,
        "union_tag_invalid": 2,
        "union_tag_not_found": 2,
    }.get(error_type, 3)


def _validation_error_category(error_type: object) -> PolicyValidationCategory:
    return {
        "missing": PolicyValidationCategory.REQUIRED_FIELD_MISSING,
        "extra_forbidden": PolicyValidationCategory.UNKNOWN_FIELD,
        "union_tag_invalid": PolicyValidationCategory.UNKNOWN_VARIANT,
        "union_tag_not_found": PolicyValidationCategory.UNKNOWN_VARIANT,
    }.get(error_type, PolicyValidationCategory.INVALID_VALUE)


_GATE_KIND_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")


__all__ = [
    "GateDescription",
    "GateRegistry",
    "PolicyValidationCategory",
    "PolicyValidationError",
    "create_builtin_registry",
]
