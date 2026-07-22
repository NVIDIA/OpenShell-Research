"""Strict finding models and the nominal scanner extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from time import monotonic
from types import MappingProxyType
from typing import Generic, Self, TypeVar, get_args, get_origin

from pydantic import (
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import TypeIs

from privacy_guard.constants import (
    DEFAULT_SCAN_TIMEOUT_SECONDS,
    MAX_FINDING_METADATA_ENTRIES,
    MAX_FINDINGS_PER_BLOCK,
    MAX_SCANNER_METADATA_BYTES,
)
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
    metadata: Mapping[str, str] | None = Field(default=None, repr=False)

    @field_validator("entity", "scanner_name")
    @classmethod
    def validate_metadata_boundary(cls, value: str) -> str:
        """Require valid Unicode metadata that fits the UTF-8 byte limit."""
        if any("\ud800" <= character <= "\udfff" for character in value):
            raise ValueError("finding metadata must contain valid Unicode")
        if len(value.encode("utf-8")) > MAX_SCANNER_METADATA_BYTES:
            raise ValueError("finding metadata exceeds the UTF-8 byte limit")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_finding_metadata(
        cls, value: Mapping[str, str] | None
    ) -> Mapping[str, str] | None:
        """Copy valid, bounded string metadata into an immutable mapping."""
        if value is None:
            return None
        if len(value) > MAX_FINDING_METADATA_ENTRIES:
            raise ValueError("finding metadata has too many entries")
        for key, item in value.items():
            cls.validate_metadata_boundary(key)
            cls.validate_metadata_boundary(item)
        return MappingProxyType(dict(value))

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


class ScanBudgetExceeded(Exception):
    """A content-safe signal that scanning exhausted its shared request budget."""


class ScanBudget(StrictDomainModel):
    """Immutable request-scoped monotonic deadline shared by all scanner calls."""

    deadline: float = Field(allow_inf_nan=False)

    @classmethod
    def from_timeout(cls, timeout_seconds: float) -> Self:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or timeout_seconds <= 0
        ):
            raise ValueError("scan timeout must be finite and positive")
        return cls(deadline=monotonic() + timeout_seconds)

    def remaining_seconds(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ScanBudgetExceeded
        return remaining


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
        self._initialize()

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

    def _initialize(self) -> None:
        """Optionally initialize scanner state after validated config is retained."""

    def scan(
        self, text_block: ScalarString, *, budget: ScanBudget | None = None
    ) -> tuple[Finding, ...]:
        """Scan one block using a shared or standalone request deadline.

        RequestProcessor supplies one budget to every scanner and text block in a
        request. Standalone callers may omit it and receive a fresh default budget.
        """
        effective_budget = budget or ScanBudget.from_timeout(
            DEFAULT_SCAN_TIMEOUT_SECONDS
        )
        result: object = self._scan(text_block, effective_budget)
        return parse_scanner_output(result)

    @abstractmethod
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        """Return block-relative findings for one block of text.

        Implementations with potentially expensive work should call
        ``budget.remaining_seconds()`` at practical interruption points.
        """
        raise NotImplementedError


__all__ = [
    "Confidence",
    "Finding",
    "RequestBodyFinding",
    "ScanBudget",
    "ScanBudgetExceeded",
    "Scanner",
    "ScannerConfig",
    "ScannerContractError",
    "ScannerFindingLimitExceeded",
    "parse_scanner_output",
]
