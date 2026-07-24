"""Bounded regular-expression entity detection and replacement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import Literal, Protocol, Self

import regex
from pydantic import Field, field_validator, model_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import (
    CONFIDENCE_RANK,
    MAX_BODY_BYTES,
    MAX_DETECTIONS_PER_STAGE,
    MAX_DIAGNOSTIC_TEXT_BYTES,
    MAX_MATCHES_PER_PATTERN,
    MAX_REGEX_ENTITIES_PER_CATALOG,
    MAX_REGEX_NAME_BYTES,
    MAX_REGEX_PATTERN_BYTES,
    MAX_REGEX_PATTERNS_PER_CATALOG,
)
from privacy_guard.engines.base import (
    ConfidenceLevel,
    EngineConfig,
    EngineConfigurationError,
    EngineContractError,
    EngineLimitExceeded,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.string_validators import ScalarString, validate_scalar_string
from privacy_guard.timeout import Timeout, TimeoutExpired


class RegexPattern(StrictDomainModel):
    """One optional diagnostic identity, pattern string, and explicit flags."""

    name: str | None = None
    pattern: ScalarString = Field(repr=False)
    confidence: ConfidenceLevel
    ignore_case: bool = False
    multiline: bool = False
    dot_all: bool = False
    ascii: bool = False

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else _validate_name(value)

    @field_validator("pattern")
    @classmethod
    def _pattern_is_bounded(cls, value: str) -> str:
        if not value:
            raise ValueError("pattern must be non-empty")
        if len(value.encode("utf-8")) > MAX_REGEX_PATTERN_BYTES:
            raise ValueError("pattern exceeds the size limit")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, value: object) -> ConfidenceLevel:
        if not isinstance(value, str):
            raise ValueError("confidence must be a string")
        return ConfidenceLevel(validate_scalar_string(value))


class RegexEntity(StrictDomainModel):
    """One entity name and its ordered, non-empty regex patterns."""

    name: str
    patterns: tuple[RegexPattern, ...] = Field(repr=False)

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("patterns", mode="before")
    @classmethod
    def _patterns_are_non_empty(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("patterns must be a non-empty list")
        return tuple(value)

    @model_validator(mode="after")
    def _supplied_pattern_names_are_unique(self) -> Self:
        supplied_names = [
            pattern.name for pattern in self.patterns if pattern.name is not None
        ]
        if len(supplied_names) != len(set(supplied_names)):
            raise ValueError("supplied pattern names must be unique within an entity")
        return self


class RegexPatternCatalog(StrictDomainModel):
    """The complete ordered entity catalog for one RegexEngine stage."""

    entities: tuple[RegexEntity, ...] = Field(repr=False)

    @field_validator("entities", mode="before")
    @classmethod
    def _entities_are_non_empty(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("entities must be a non-empty list")
        return tuple(value)

    @model_validator(mode="after")
    def _catalog_is_bounded_and_unambiguous(self) -> Self:
        names = [entity.name for entity in self.entities]
        if len(names) != len(set(names)):
            raise ValueError("entity names must be unique")
        if len(self.entities) > MAX_REGEX_ENTITIES_PER_CATALOG:
            raise ValueError("entity catalog exceeds the size limit")
        if (
            sum(len(entity.patterns) for entity in self.entities)
            > MAX_REGEX_PATTERNS_PER_CATALOG
        ):
            raise ValueError("pattern catalog exceeds the size limit")
        return self


class RegexReplacement(StrictDomainModel):
    """A constrained template replacement recipe."""

    strategy: Literal["template"] = "template"
    template: ScalarString = Field(default="[{entity}]", repr=False)

    @field_validator("template")
    @classmethod
    def _template_is_safe_and_bounded(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_DIAGNOSTIC_TEXT_BYTES:
            raise ValueError("replacement template exceeds the size limit")
        try:
            for _, field_name, format_spec, conversion in Formatter().parse(value):
                if field_name is not None and field_name != "entity":
                    raise ValueError
                if format_spec or conversion is not None:
                    raise ValueError
        except ValueError:
            raise ValueError("replacement template syntax is invalid") from None
        return value


class RegexEngineConfig(EngineConfig):
    """Exact policy configuration owned by ``RegexEngine``."""

    engine: Literal["regex"] = "regex"
    pattern_catalog: RegexPatternCatalog = Field(repr=False)
    replacement: RegexReplacement | None = None

    @model_validator(mode="after")
    def _patterns_are_valid(self) -> Self:
        try:
            for global_index, (entity, pattern_index, pattern) in enumerate(
                _iter_catalog_patterns(self.pattern_catalog)
            ):
                _compile_rule(
                    entity,
                    pattern,
                    catalog_index=global_index,
                    entity_pattern_index=pattern_index,
                )
        except (RecursionError, ValueError, regex.error):
            raise ValueError("regex pattern catalog is invalid") from None
        return self


class RegexEngine(EntityProcessingEngine[RegexEngineConfig]):
    """Detect overlapping regex matches and optionally replace deterministic winners."""

    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    @classmethod
    def _validate_run_config(
        cls,
        config: RegexEngineConfig,
        resources: None,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        del cls, resources
        if strategy is EntityProcessingStrategy.REPLACE and config.replacement is None:
            raise EngineConfigurationError(
                "regex replacement configuration is required"
            )

    def _initialize(self) -> None:
        try:
            self._rules = tuple(
                _compile_rule(
                    entity,
                    pattern,
                    catalog_index=global_index,
                    entity_pattern_index=pattern_index,
                )
                for global_index, (entity, pattern_index, pattern) in enumerate(
                    _iter_catalog_patterns(self.config.pattern_catalog)
                )
            )
        except (RecursionError, ValueError, regex.error):
            raise EngineConfigurationError(
                "regex engine configuration is invalid"
            ) from None

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        detections_with_identity: list[tuple[EntityDetection, str]] = []
        try:
            for rule in self._rules:
                match_count = 0
                next_position = 0
                while next_position <= len(text):
                    match = rule.compiled.search(
                        text,
                        next_position,
                        timeout=timeout.remaining_seconds(),
                    )
                    timeout.raise_if_expired()
                    if match is None:
                        break
                    start, end = match.span()
                    if start == end or match.span(rule.marker) != (end, end):
                        raise EngineConfigurationError(
                            "regex engine configuration is invalid"
                        )
                    match_count += 1
                    if match_count > MAX_MATCHES_PER_PATTERN:
                        raise EngineLimitExceeded("regex match count exceeds the limit")
                    detection = EntityDetection(
                        entity=rule.entity,
                        start=start,
                        end=end,
                        confidence=rule.confidence,
                        metadata={_PATTERN_METADATA_KEY: rule.pattern_identity},
                    )
                    detections_with_identity.append((detection, rule.pattern_identity))
                    if len(detections_with_identity) > MAX_DETECTIONS_PER_STAGE:
                        raise EngineLimitExceeded(
                            "regex detection count exceeds the limit"
                        )
                    next_position = start + 1
        except TimeoutError:
            raise TimeoutExpired from None

        detections_with_identity.sort(
            key=lambda item: (
                item[0].start,
                item[0].end,
                item[0].entity,
                item[1],
            )
        )
        detections = tuple(item[0] for item in detections_with_identity)
        output_text = text
        if strategy is EntityProcessingStrategy.REPLACE and detections:
            replacement = self.config.replacement
            if replacement is None:
                raise EngineConfigurationError(
                    "regex replacement configuration is required"
                )
            winners = _resolve_overlaps(detections_with_identity)
            output_text = _render_bounded_replacement(
                text,
                winners,
                replacement.template,
            )
        return TextProcessingResult(text=output_text, detections=detections)


@dataclass(frozen=True)
class _CompiledRule:
    entity: str
    pattern_identity: str
    confidence: ConfidenceLevel
    marker: str
    compiled: _CompiledPattern


class _RegexMatch(Protocol):
    def span(self, group: int | str = 0) -> tuple[int, int]:
        """Return the matched span for a numbered or named group."""
        ...


class _CompiledPattern(Protocol):
    @property
    def groupindex(self) -> Mapping[str, int]:
        """Return the pattern's named capture groups."""
        ...

    def search(
        self,
        string: str,
        pos: int = 0,
        *,
        timeout: float | None = None,
    ) -> _RegexMatch | None:
        """Search from a code-point offset with a bounded timeout."""
        ...


def _validate_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or _NAME_PATTERN.fullmatch(value) is None
        or len(value.encode("ascii")) > MAX_REGEX_NAME_BYTES
    ):
        raise ValueError("name is invalid")
    return value


def _compile_rule(
    entity: RegexEntity,
    pattern: RegexPattern,
    catalog_index: int,
    entity_pattern_index: int,
) -> _CompiledRule:
    flags = 0
    if pattern.ignore_case:
        flags |= regex.IGNORECASE
    if pattern.multiline:
        flags |= regex.MULTILINE
    if pattern.dot_all:
        flags |= regex.DOTALL
    if pattern.ascii:
        flags |= regex.ASCII
    if _contains_inline_flags(pattern.pattern):
        raise ValueError("inline flags are unsupported")
    unmarked = regex.compile(pattern.pattern, flags)
    if unmarked.groupindex:
        raise ValueError("named groups are reserved")
    if unmarked.search("") is not None:
        raise ValueError("pattern must not match empty input")
    marker = f"_pg_pattern_{catalog_index:06d}"
    compiled = regex.compile(f"(?:{pattern.pattern})(?P<{marker}>)", flags)
    if marker not in compiled.groupindex:
        raise ValueError("internal marker is missing")
    pattern_identity = pattern.name or f"{entity.name}.patterns[{entity_pattern_index}]"
    return _CompiledRule(
        entity=entity.name,
        pattern_identity=pattern_identity,
        confidence=pattern.confidence,
        marker=marker,
        compiled=compiled,
    )


def _iter_catalog_patterns(
    catalog: RegexPatternCatalog,
) -> tuple[tuple[RegexEntity, int, RegexPattern], ...]:
    return tuple(
        (entity, pattern_index, pattern)
        for entity in catalog.entities
        for pattern_index, pattern in enumerate(entity.patterns)
    )


def _contains_inline_flags(pattern: str) -> bool:
    escaped = False
    in_character_class = False
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "[":
            in_character_class = True
        elif character == "]" and in_character_class:
            in_character_class = False
        elif not in_character_class and pattern.startswith("(?", index):
            suffix = pattern[index + 2 :]
            if _INLINE_FLAG_PATTERN.match(suffix) is not None:
                return True
        index += 1
    return False


def _resolve_overlaps(
    detections: list[tuple[EntityDetection, str]],
) -> tuple[EntityDetection, ...]:
    winners: list[EntityDetection] = []
    ranked = sorted(
        detections,
        key=lambda item: (
            -_categorical_confidence_rank(item[0].confidence),
            -(item[0].end - item[0].start),
            item[0].start,
            item[0].end,
            item[0].entity,
            item[1],
        ),
    )
    for candidate, _ in ranked:
        if all(
            candidate.end <= winner.start or candidate.start >= winner.end
            for winner in winners
        ):
            winners.append(candidate)
    return tuple(
        sorted(
            winners,
            key=lambda item: (item.start, item.end, item.entity),
        )
    )


def _categorical_confidence_rank(confidence: object) -> int:
    if not isinstance(confidence, ConfidenceLevel):
        raise EngineContractError("regex detection confidence is invalid")
    return CONFIDENCE_RANK[confidence.value]


def _render_bounded_replacement(
    text: str,
    detections: tuple[EntityDetection, ...],
    template: str,
) -> str:
    projected_size = 0
    cursor = 0
    for detection in detections:
        projected_size += len(text[cursor : detection.start].encode("utf-8"))
        projected_size += _rendered_template_size(template, detection.entity)
        if projected_size > MAX_BODY_BYTES:
            raise EngineLimitExceeded("regex replacement exceeds the size limit")
        cursor = detection.end
    projected_size += len(text[cursor:].encode("utf-8"))
    if projected_size > MAX_BODY_BYTES:
        raise EngineLimitExceeded("regex replacement exceeds the size limit")

    parts: list[str] = []
    cursor = 0
    for detection in detections:
        parts.append(text[cursor : detection.start])
        parts.append(template.format(entity=detection.entity))
        cursor = detection.end
    parts.append(text[cursor:])
    return "".join(parts)


def _rendered_template_size(template: str, entity: str) -> int:
    size = 0
    entity_size = len(entity.encode("utf-8"))
    for literal, field_name, _, _ in Formatter().parse(template):
        size += len(literal.encode("utf-8"))
        if field_name is not None:
            size += entity_size
    return size


_NAME_PATTERN = regex.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_INLINE_FLAG_PATTERN = regex.compile(r"[A-Za-z0-9-]+(?=[:)])")
_PATTERN_METADATA_KEY = "pattern"


__all__ = [
    "RegexEngine",
    "RegexEngineConfig",
    "RegexEntity",
    "RegexPattern",
    "RegexPatternCatalog",
    "RegexReplacement",
]
