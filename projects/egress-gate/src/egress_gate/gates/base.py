"""Trusted request-level gate extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import NoneType
from typing import ClassVar, Generic, TypeGuard, final, get_args, get_origin

from pydantic import ValidationError
from typing_extensions import TypeVar

from egress_gate.base import StrictDomainModel
from egress_gate.errors import (
    GateConfigurationError,
    GateContractError,
    GateError,
    GateExecutionError,
    GateInputError,
    TimeoutExpiredError,
)
from egress_gate.request import HttpRequest
from egress_gate.result import (
    FindingTypeDefinition,
    GateEvaluation,
)
from egress_gate.timeout import Timeout


class GateConfig(StrictDomainModel):
    """Nominal base for one gate's exact policy configuration."""


class GateResources:
    """Operator-owned, concurrency-safe resources borrowed by prepared gates."""

    __slots__ = ()


class GateCapabilities(StrictDomainModel):
    """Declarative gate reads and mechanically enforced output capabilities."""

    reads_target: bool = False
    reads_context: bool = False
    reads_headers: bool = False
    reads_body: bool = False
    replaces_body: bool = False
    mutates_headers: bool = False
    produces_findings: bool = False
    may_allow: bool = False
    may_deny: bool = False
    uses_resources: bool = False


GateConfigT = TypeVar("GateConfigT", bound=GateConfig)
GateResourcesT = TypeVar(
    "GateResourcesT",
    bound=GateResources | None,
    default=None,
)


class Gate(ABC, Generic[GateConfigT, GateResourcesT]):
    """Typed request-level gate with a validated public evaluation wrapper."""

    capabilities: ClassVar[GateCapabilities]
    finding_types: ClassVar[tuple[FindingTypeDefinition, ...]]

    @final
    def __init__(
        self,
        config: GateConfigT,
        resources: GateResourcesT,
        *,
        timeout: Timeout | None = None,
    ) -> None:
        type(self).validate_config(config, resources)
        self.__config = config
        self.__resources = resources
        if timeout is not None and not isinstance(timeout, Timeout):
            raise GateConfigurationError("gate preparation timeout is invalid")
        self._initialize(timeout=timeout)

    @classmethod
    def validate_config(
        cls,
        config: GateConfigT,
        resources: GateResourcesT,
    ) -> None:
        """Purely validate one exact config and its registered resources."""
        cls._validate_class_contract()
        config_type, resources_type = _declared_gate_types(cls)
        try:
            if type(config) is not config_type:
                raise ValueError
            config_type.model_validate(config)
        except (ValidationError, ValueError):
            raise GateConfigurationError("gate configuration is invalid") from None
        if not _is_valid_resources(resources, resources_type):
            raise GateConfigurationError("gate resources are invalid")
        cls._validate_config(config, resources)

    @classmethod
    def get_config_type(cls) -> type[GateConfig]:
        """Return the concrete ``GateConfig`` type declared by the gate."""
        config_type, _ = _declared_gate_types(cls)
        return config_type

    @classmethod
    def get_resources_type(cls) -> type[GateResources] | None:
        """Return the concrete runtime-resource type, if any."""
        _, resources_type = _declared_gate_types(cls)
        return resources_type

    @property
    def config(self) -> GateConfigT:
        """Return the exact validated gate configuration."""
        return self.__config

    @property
    def resources(self) -> GateResourcesT:
        """Return the borrowed application-owned resources."""
        return self.__resources

    @final
    def evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        """Evaluate one immutable current request and validate the result."""
        if not isinstance(request, HttpRequest) or not isinstance(timeout, Timeout):
            raise GateContractError("gate input types are invalid")
        timeout.raise_if_expired()
        try:
            raw_result = self._evaluate(request, timeout=timeout)
            if not isinstance(raw_result, GateEvaluation):
                raise GateContractError("gate output is invalid")
            try:
                result = GateEvaluation.model_validate(raw_result.model_dump())
            except ValidationError:
                raise GateContractError("gate output is invalid") from None
            timeout.raise_if_expired()
            _validate_gate_output(
                type(self).capabilities,
                type(self).finding_types,
                result,
            )
            return result
        except (GateError, TimeoutExpiredError):
            raise
        except Exception:
            raise GateExecutionError("gate evaluation failed") from None

    @classmethod
    def _validate_class_contract(cls) -> None:
        capabilities = getattr(cls, "capabilities", None)
        if not isinstance(capabilities, GateCapabilities):
            raise GateConfigurationError("gate capabilities are invalid")
        finding_types = getattr(cls, "finding_types", None)
        if not isinstance(finding_types, tuple) or any(
            not isinstance(item, FindingTypeDefinition) for item in finding_types
        ):
            raise GateConfigurationError("gate finding declarations are invalid")
        names = tuple(item.type for item in finding_types)
        if len(names) != len(set(names)):
            raise GateConfigurationError("gate finding types must be unique")
        config_type, resources_type = _declared_gate_types(cls)
        if config_type is GateConfig:
            raise GateConfigurationError("gate config type is not concrete")
        if capabilities.uses_resources is not (resources_type is not None):
            raise GateConfigurationError(
                "gate resource capability does not match its generic resource type"
            )
        if capabilities.produces_findings != bool(finding_types):
            raise GateConfigurationError(
                "gate finding capability does not match its declarations"
            )

    @classmethod
    def _validate_config(
        cls,
        config: GateConfigT,
        resources: GateResourcesT,
    ) -> None:
        """Optionally validate resource-backed config without side effects."""

    def _initialize(self, *, timeout: Timeout | None = None) -> None:
        """Optionally derive reusable state under the preparation deadline."""

    @abstractmethod
    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        """Return one explicit control result for the current request."""
        raise NotImplementedError


class Utf8BodyGate(
    Gate[GateConfigT, GateResourcesT], Generic[GateConfigT, GateResourcesT]
):
    """Gate helper that exposes one strict UTF-8 body to an implementation."""

    capabilities = GateCapabilities(reads_body=True)
    finding_types: ClassVar[tuple[FindingTypeDefinition, ...]] = ()

    @final
    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        try:
            text = request.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GateInputError("request body is not valid UTF-8") from None
        result = self._evaluate_text(text, timeout=timeout)
        if not isinstance(result, GateEvaluation):
            raise GateContractError("UTF-8 body gate output is invalid")
        if result.patch.replacement_body is not None:
            try:
                result.patch.replacement_body.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise GateContractError(
                    "UTF-8 body gate returned a non-UTF-8 replacement"
                ) from None
        return result

    @abstractmethod
    def _evaluate_text(
        self,
        text: str,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        """Evaluate the decoded body and preserve explicit replacement intent."""
        raise NotImplementedError


def _validate_gate_output(
    capabilities: GateCapabilities,
    finding_types: tuple[FindingTypeDefinition, ...],
    result: GateEvaluation,
) -> None:
    if result.patch.replacement_body is not None and not capabilities.replaces_body:
        raise GateContractError("gate returned an undeclared body replacement")
    if result.patch.header_mutations and not capabilities.mutates_headers:
        raise GateContractError("gate returned undeclared header mutations")
    if result.findings and not capabilities.produces_findings:
        raise GateContractError("gate returned undeclared findings")
    if result.control.value == "allow" and not capabilities.may_allow:
        raise GateContractError("gate returned an undeclared terminal allow")
    if result.control.value == "deny" and not capabilities.may_deny:
        raise GateContractError("gate returned an undeclared deny")
    declared_types = frozenset(item.type for item in finding_types)
    if any(finding.type not in declared_types for finding in result.findings):
        raise GateContractError("gate returned an undeclared finding type")


def _declared_gate_types(
    gate_type: type[object],
) -> tuple[type[GateConfig], type[GateResources] | None]:
    for candidate in gate_type.__mro__:
        for base in getattr(candidate, "__orig_bases__", ()):
            origin = get_origin(base)
            if origin is None or not isinstance(origin, type):
                continue
            if not issubclass(origin, Gate):
                continue
            arguments = get_args(base)
            parameters = getattr(origin, "__parameters__", ())
            substitutions = dict(zip(parameters, arguments, strict=False))
            if origin is Gate:
                resolved = tuple(
                    substitutions.get(argument, argument) for argument in arguments
                )
                if len(resolved) == 1:
                    resolved = (*resolved, NoneType)
                if len(resolved) != 2:
                    break
                config_type, resources_type = resolved
                if _is_gate_config_type(config_type):
                    return config_type, _normalize_resources_type(resources_type)
                break
            try:
                inherited = _resolve_gate_base(origin, substitutions)
            except GateConfigurationError:
                continue
            if inherited is not None:
                return inherited
    raise GateConfigurationError(
        "gate must declare concrete configuration and resource types"
    )


def _resolve_gate_base(
    candidate: type[object],
    substitutions: dict[object, object],
) -> tuple[type[GateConfig], type[GateResources] | None] | None:
    for base in getattr(candidate, "__orig_bases__", ()):
        origin = get_origin(base)
        if (
            origin is None
            or not isinstance(origin, type)
            or not issubclass(origin, Gate)
        ):
            continue
        arguments = tuple(
            substitutions.get(argument, argument) for argument in get_args(base)
        )
        parameters = getattr(origin, "__parameters__", ())
        nested = dict(substitutions)
        nested.update(zip(parameters, arguments, strict=False))
        if origin is Gate:
            if len(arguments) == 1:
                arguments = (*arguments, NoneType)
            if len(arguments) == 2:
                config_type, resources_type = arguments
                if _is_gate_config_type(config_type):
                    return config_type, _normalize_resources_type(resources_type)
            continue
        resolved = _resolve_gate_base(origin, nested)
        if resolved is not None:
            return resolved
    return None


def _is_gate_config_type(value: object) -> TypeGuard[type[GateConfig]]:
    return isinstance(value, type) and issubclass(value, GateConfig)


def _is_gate_resources_type(value: object) -> TypeGuard[type[GateResources]]:
    return isinstance(value, type) and issubclass(value, GateResources)


def _normalize_resources_type(value: object) -> type[GateResources] | None:
    if value is None or value is NoneType:
        return None
    if _is_gate_resources_type(value):
        return value
    raise GateConfigurationError("gate resources type is invalid")


def _is_valid_resources(
    resources: object,
    resources_type: type[GateResources] | None,
) -> bool:
    if resources_type is None:
        return resources is None
    return isinstance(resources, resources_type)


__all__ = [
    "Gate",
    "GateCapabilities",
    "GateConfig",
    "GateConfigT",
    "GateResources",
    "GateResourcesT",
    "Utf8BodyGate",
]
