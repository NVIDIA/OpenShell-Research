"""Strict finding models and the nominal scanner extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Generic, Self, TypeVar, get_args, get_origin

from pydantic import (
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import TypeIs

from privacy_guard.constants import MAX_FINDINGS_PER_BLOCK, MAX_SCANNER_METADATA_BYTES
from privacy_guard.validation import ScalarString, StrictDomainModel


class Confidence(StrEnum):
    """Scanner-owned confidence assigned to a finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(StrictDomainModel):
    """A scanner-owned, block-relative sensitive-data detection."""

    entity: str = Field(..., min_length=1, max_length=MAX_SCANNER_METADATA_BYTES)
    scanner_name: str = Field(..., min_length=1, max_length=MAX_SCANNER_METADATA_BYTES)
    start_offset: int = Field(..., ge=0)
    end_offset: int
    confidence: Confidence = Confidence.HIGH

    @field_validator("entity", "scanner_name")
    @classmethod
    def validate_metadata_boundary(cls, value: str) -> str:
        """Require valid Unicode metadata that fits the UTF-8 byte limit."""
        if any("\ud800" <= character <= "\udfff" for character in value):
            raise ValueError("finding metadata must contain valid Unicode")
        if len(value.encode("utf-8")) > MAX_SCANNER_METADATA_BYTES:
            raise ValueError("finding metadata exceeds the UTF-8 byte limit")
        return value

    @model_validator(mode="after")
    def validate_non_empty_span(self) -> Self:
        """Require the finding to cover at least one character."""
        if self.end_offset <= self.start_offset:
            raise ValueError("finding span must be non-empty")
        return self


class RequestBodyFinding(StrictDomainModel):
    """Place a block-relative Finding at its path within a normalized RequestBody."""

    finding: Finding
    text_block_path: ScalarString = Field(repr=False)


class ScannerConfig(StrictDomainModel):
    """Immutable scanner identity and complete entity catalog."""

    name: str = Field(..., min_length=1)
    entity_types: frozenset[str]


class ScannerContractError(Exception):
    """A content-safe scanner configuration or output contract failure."""


class ScannerFindingLimitExceeded(Exception):
    """The scanner exceeded the bounded per-block finding count."""


def parse_scanner_output(result: object) -> tuple[Finding, ...]:
    """Validate a scanner's tuple output against the Finding contract."""
    if not isinstance(result, tuple):
        raise ScannerContractError("scanner output is invalid")
    if len(result) > MAX_FINDINGS_PER_BLOCK:
        raise ScannerFindingLimitExceeded
    if not _contains_only_findings(result):
        raise ScannerContractError("scanner output is invalid")
    return result


def _contains_only_findings(
    result: tuple[object, ...],
) -> TypeIs[tuple[Finding, ...]]:
    return all(isinstance(finding, Finding) for finding in result)


# This private type variable must precede the public generic classes that use it.
_ScannerConfigT = TypeVar("_ScannerConfigT", bound=ScannerConfig, covariant=True)


class Scanner(ABC, Generic[_ScannerConfigT]):
    """Nominal, concurrency-safe extension point for scanning one text block."""

    def __init__(self, config: _ScannerConfigT) -> None:
        """Validate and retain the scanner's declared concrete configuration."""
        try:
            validated_config = self.get_config_type().model_validate(config)
        except ValidationError:
            raise ScannerContractError("scanner configuration is invalid") from None
        self.__config = validated_config
        self.__scanner_name = validated_config.name

    @classmethod
    def get_config_type(cls) -> type[_ScannerConfigT]:
        """Return the ScannerConfig type declared by the subclass."""
        for base in getattr(cls, "__orig_bases__", ()):
            for argument in get_args(base):
                origin = get_origin(argument) or argument
                if isinstance(origin, type) and issubclass(origin, ScannerConfig):
                    return origin
        raise TypeError(f"{cls.__name__} must declare a ScannerConfig generic argument")

    @property
    def config(self) -> _ScannerConfigT:
        """Return the scanner's immutable concrete configuration."""
        return self.__config

    @property
    def scanner_name(self) -> str:
        """Return the immutable stable scanner name."""
        return self.__scanner_name

    @property
    def supported_entity_types(self) -> frozenset[str]:
        """Return the complete entity catalog declared by the scanner config."""
        return self.config.entity_types

    def scan(self, text_block: ScalarString) -> tuple[Finding, ...]:
        """Run the implementation and validate its observable output shape."""
        result: object = self._scan(text_block)
        return parse_scanner_output(result)

    @abstractmethod
    def _scan(self, text_block: str) -> tuple[Finding, ...]:
        """Return block-relative findings for one block of text."""
        raise NotImplementedError


__all__ = [
    "Confidence",
    "Finding",
    "RequestBodyFinding",
    "Scanner",
    "ScannerConfig",
    "ScannerContractError",
    "ScannerFindingLimitExceeded",
    "parse_scanner_output",
]
