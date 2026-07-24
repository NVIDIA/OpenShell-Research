"""Core entity-processing engine extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import (
    Annotated,
    ClassVar,
    Generic,
    TypeAlias,
    get_args,
    get_origin,
)

from pydantic import (
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import TypeVar

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import (
    MAX_BODY_BYTES,
    MAX_DETECTIONS_PER_STAGE,
    MAX_FINDING_METADATA_ENTRIES,
)
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineExecutionError,
    EngineLimitExceeded,
    EntityProcessingError,
)
from privacy_guard.string_validators import (
    ScalarString,
    validate_bounded_metadata_string,
    validate_scalar_string,
)
from privacy_guard.timeout import Timeout


class EntityProcessingStrategy(StrEnum):
    """Select whether one engine invocation detects or replaces entities."""

    DETECT = "detect"
    REPLACE = "replace"


class ConfidenceLevel(StrEnum):
    """Categorical certainty reported by an entity-processing engine."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _parse_unit_interval(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("confidence must be a number from zero through one")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be a number from zero through one")
    return result


UnitInterval = Annotated[float, BeforeValidator(_parse_unit_interval)]
DetectionConfidence: TypeAlias = ConfidenceLevel | UnitInterval
EntityName = Annotated[str, BeforeValidator(validate_bounded_metadata_string)]
MetadataString = Annotated[str, BeforeValidator(validate_bounded_metadata_string)]
BoundedMetadata: TypeAlias = Mapping[MetadataString, MetadataString]


class EntityDetection(StrictDomainModel):
    """One sensitive entity occurrence in the engine's input text."""

    entity: EntityName
    start: int = Field(ge=0)
    end: int
    confidence: DetectionConfidence | None = None
    metadata: BoundedMetadata = Field(default_factory=dict, repr=False)

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, value: object) -> object:
        if isinstance(value, str):
            return ConfidenceLevel(validate_scalar_string(value))
        return value

    @field_validator("metadata")
    @classmethod
    def _copy_bounded_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if len(value) > MAX_FINDING_METADATA_ENTRIES:
            raise ValueError("detection metadata has too many entries")
        copied: dict[str, str] = {}
        for key, item in value.items():
            copied[validate_bounded_metadata_string(key)] = (
                validate_bounded_metadata_string(item)
            )
        return MappingProxyType(copied)

    @model_validator(mode="after")
    def _span_is_non_empty(self) -> EntityDetection:
        if self.end <= self.start:
            raise ValueError("detection span must be non-empty")
        return self


class TextProcessingResult(StrictDomainModel):
    """The authoritative text and detections returned by one engine run."""

    text: ScalarString = Field(repr=False)
    detections: tuple[EntityDetection, ...]

    @field_validator("detections", mode="before")
    @classmethod
    def _detections_are_a_tuple(cls, value: object) -> object:
        if not isinstance(value, tuple):
            raise ValueError("detections must be a tuple")
        return value


class EngineConfig(StrictDomainModel):
    """Nominal base for an engine's exact policy configuration."""


class EngineResources:
    """Optional operator-owned runtime dependencies shared by engine instances.

    Resource objects contain initialized operational dependencies such as model
    clients, SDK adapters, endpoints, or credential providers. They must not
    contain policy behavior or mutable per-request state, and everything they
    expose to an engine must be safe for concurrent use.
    """

    __slots__ = ()


_ConfigT = TypeVar("_ConfigT", bound=EngineConfig)
_ResourcesT = TypeVar(
    "_ResourcesT",
    bound=EngineResources | None,
    default=None,
)


class EntityProcessingEngine(ABC, Generic[_ConfigT, _ResourcesT]):
    """Nominal, typed extension point for processing one text string."""

    supported_strategies: ClassVar[frozenset[EntityProcessingStrategy]]

    def __init__(
        self,
        config: _ConfigT,
        resources: _ResourcesT,
    ) -> None:
        """Validate typed configuration/resources and initialize reusable state."""
        self.validate_config(config, resources)
        self.__config = config
        self.__resources = resources
        self._initialize()

    @classmethod
    def validate_config(
        cls,
        config: _ConfigT,
        resources: _ResourcesT,
    ) -> None:
        """Purely validate one exact config and its registered resources."""
        cls._validate_class_contract()
        config_type = cls.get_config_type()
        try:
            if not isinstance(config, config_type):
                raise ValueError
            config_type.model_validate(config)
        except (ValidationError, ValueError):
            raise EngineConfigurationError("engine configuration is invalid") from None
        resources_type = cls.get_resources_type()
        if not _is_valid_resources(resources, resources_type):
            raise EngineConfigurationError("engine resources are invalid")
        cls._validate_config(config, resources)

    @classmethod
    def validate_run_config(
        cls,
        config: _ConfigT,
        resources: _ResourcesT,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        """Validate that one config can execute the requested strategy."""
        if not isinstance(strategy, EntityProcessingStrategy):
            raise EngineConfigurationError("engine processing strategy is invalid")
        cls.validate_config(config, resources)
        if strategy not in cls.supported_strategies:
            raise EngineConfigurationError(
                "engine does not support the requested strategy"
            )
        cls._validate_run_config(config, resources, strategy=strategy)

    @classmethod
    def get_config_type(cls) -> type[EngineConfig]:
        """Return the concrete ``EngineConfig`` type argument."""
        config_type, _ = _declared_engine_types(cls)
        return config_type

    @classmethod
    def get_resources_type(cls) -> object:
        """Return the concrete runtime-resources generic argument."""
        _, resources_type = _declared_engine_types(cls)
        return resources_type

    @property
    def config(self) -> _ConfigT:
        """Return the immutable, concrete engine configuration."""
        return self.__config

    @property
    def resources(self) -> _ResourcesT:
        """Return the validated, injected runtime resources."""
        return self.__resources

    def run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        """Process one text value and validate the complete collaborator result."""
        try:
            validated_text = validate_scalar_string(text)
        except ValueError:
            raise EngineContractError("engine input text is invalid") from None
        if not isinstance(strategy, EntityProcessingStrategy):
            raise EngineContractError("engine processing strategy is invalid")
        if not isinstance(timeout, Timeout):
            raise EngineContractError("engine timeout is invalid")
        if strategy not in self.supported_strategies:
            raise EngineContractError("engine does not support the requested strategy")
        timeout.raise_if_expired()
        result: object = self._run(
            validated_text,
            strategy=strategy,
            timeout=timeout,
        )
        timeout.raise_if_expired()
        return _validate_result(validated_text, result, strategy=strategy)

    @classmethod
    def _validate_config(
        cls,
        config: _ConfigT,
        resources: _ResourcesT,
    ) -> None:
        """Optionally validate resource-backed config without side effects."""

    @classmethod
    def _validate_run_config(
        cls,
        config: _ConfigT,
        resources: _ResourcesT,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        """Optionally validate requirements specific to one run strategy."""

    @classmethod
    def _validate_class_contract(cls) -> None:
        supported_strategies = getattr(cls, "supported_strategies", None)
        if (
            not isinstance(supported_strategies, frozenset)
            or not supported_strategies
            or any(
                not isinstance(strategy, EntityProcessingStrategy)
                for strategy in supported_strategies
            )
        ):
            raise EngineConfigurationError("engine supported strategies are invalid")
        cls.get_config_type()
        cls.get_resources_type()

    def _initialize(self) -> None:
        """Optionally initialize reusable state from config and resources."""

    @abstractmethod
    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        """Return processed text and every detected entity occurrence."""
        raise NotImplementedError


def _declared_engine_types(
    engine_type: type[object],
) -> tuple[type[EngineConfig], object]:
    for candidate in engine_type.__mro__:
        for base in getattr(candidate, "__orig_bases__", ()):
            if get_origin(base) is not EntityProcessingEngine:
                continue
            arguments = get_args(base)
            if len(arguments) != 2:
                break
            config_type, resources_type = arguments
            if isinstance(config_type, type) and issubclass(config_type, EngineConfig):
                return config_type, resources_type
    raise EngineConfigurationError(
        "engine must declare concrete configuration and resource types"
    )


def _is_valid_resources(resources: object, resources_type: object) -> bool:
    if resources_type in (None, type(None)):
        return resources is None
    origin = get_origin(resources_type)
    if origin is not None:
        resources_type = origin
    return isinstance(resources_type, type) and isinstance(resources, resources_type)


def _validate_result(
    input_text: str,
    result: object,
    *,
    strategy: EntityProcessingStrategy,
) -> TextProcessingResult:
    if not isinstance(result, TextProcessingResult):
        raise EngineContractError("engine output is invalid")
    if len(result.detections) > MAX_DETECTIONS_PER_STAGE:
        raise EngineLimitExceeded("engine returned too many detections")
    if len(result.text.encode("utf-8")) > MAX_BODY_BYTES:
        raise EngineLimitExceeded("engine output text exceeds the size limit")
    for detection in result.detections:
        if detection.end > len(input_text):
            raise EngineContractError("engine detection span is invalid")
    if strategy is EntityProcessingStrategy.DETECT and result.text != input_text:
        raise EngineContractError("detection-only engine output changed text")
    if result.text != input_text and not result.detections:
        raise EngineContractError("engine changed text without a detection")
    return result


__all__ = [
    "BoundedMetadata",
    "ConfidenceLevel",
    "DetectionConfidence",
    "EngineConfig",
    "EngineConfigurationError",
    "EngineContractError",
    "EngineExecutionError",
    "EngineLimitExceeded",
    "EngineResources",
    "EntityDetection",
    "EntityName",
    "EntityProcessingEngine",
    "EntityProcessingError",
    "EntityProcessingStrategy",
    "TextProcessingResult",
    "UnitInterval",
]
