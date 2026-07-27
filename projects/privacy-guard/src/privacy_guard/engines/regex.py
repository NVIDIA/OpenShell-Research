"""Bounded regular-expression entity detection and replacement."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from string import Formatter
from threading import RLock
from typing import Literal, Protocol, Self

import regex
import yaml
from pydantic import Field, field_validator, model_validator
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import (
    MAX_BODY_BYTES,
    MAX_DETECTIONS_PER_STAGE,
    MAX_DIAGNOSTIC_TEXT_BYTES,
    MAX_REGEX_CATALOG_FILE_BYTES,
    MAX_REGEX_CATALOG_PATH_BYTES,
    MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES,
    MAX_REGEX_ENTITIES_PER_CATALOG,
    MAX_REGEX_NAME_BYTES,
    MAX_REGEX_PARSED_CATALOG_CACHE_BYTES,
    MAX_REGEX_PATTERN_BYTES,
    MAX_REGEX_PATTERNS_PER_CATALOG,
    REGEX_COMPILED_RULE_WEIGHT_BYTES,
)
from privacy_guard.engines.base import (
    ConfidenceLevel,
    EngineConfig,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineLimitExceededError,
)
from privacy_guard.logging import get_logger
from privacy_guard.string_validators import ScalarString, validate_scalar_string
from privacy_guard.timeout import Timeout


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
    pattern_catalog: RegexPatternCatalog = Field(
        repr=False,
        description=(
            "Complete structured catalog or relative path to a complete YAML catalog."
        ),
    )
    replacement: RegexReplacement | None = None

    @field_validator(
        "pattern_catalog",
        mode="before",
        json_schema_input_type=RegexPatternCatalog | str,
    )
    @classmethod
    def _load_pattern_catalog(
        cls,
        value: object,
    ) -> object:
        del cls
        if isinstance(value, str):
            return _load_pattern_catalog_file(value)
        return value

    @model_validator(mode="after")
    def _patterns_are_valid(self) -> Self:
        try:
            _compile_pattern_catalog(self.pattern_catalog)
        except (RecursionError, ValueError, regex.error):
            raise ValueError("regex pattern catalog is invalid") from None
        return self


class RegexEngine(EntityProcessingEngine[RegexEngineConfig]):
    """Detect every regex match, including matches that share input characters."""

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
            self._rules = _compile_pattern_catalog(self.config.pattern_catalog)
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
        for rule in self._rules:
            next_position = 0
            while next_position <= len(text):
                with timeout.enforce():
                    match = rule.compiled.search(
                        text,
                        next_position,
                        timeout=timeout.remaining_seconds(),
                    )
                if match is None:
                    break
                start, end = match.span()
                if start == end:
                    raise EngineConfigurationError(
                        "regex engine configuration is invalid"
                    )
                if match.span(rule.marker) != (end, end):
                    raise EngineConfigurationError(
                        "regex engine configuration is invalid"
                    )
                detection = EntityDetection(
                    entity=rule.entity,
                    start=start,
                    end=end,
                    confidence=rule.confidence,
                    metadata={_PATTERN_METADATA_KEY: rule.pattern_identity},
                )
                detections_with_identity.append((detection, rule.pattern_identity))
                if len(detections_with_identity) > MAX_DETECTIONS_PER_STAGE:
                    raise EngineLimitExceededError(
                        "regex detection count exceeds the limit"
                    )
                next_position = start + 1

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


class _StrictCatalogLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and duplicate mapping keys."""

    def compose_node(
        self,
        parent: object,
        index: object,
    ) -> yaml.Node:
        if self.check_event(AliasEvent):
            raise ConstructorError(
                None,
                None,
                "YAML aliases are not supported",
                self.peek_event().start_mark,
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _StrictCatalogLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from None
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictCatalogLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_pattern_catalog_file(value: str) -> RegexPatternCatalog:
    descriptor: int | None = None
    try:
        path_text = validate_scalar_string(value)
        if (
            not path_text
            or len(path_text.encode("utf-8")) > MAX_REGEX_CATALOG_PATH_BYTES
        ):
            raise ValueError
        relative_path = Path(path_text)
        if (
            relative_path.is_absolute()
            or relative_path.suffix.lower() not in {".yaml", ".yml"}
            or ".." in relative_path.parts
        ):
            raise ValueError

        descriptor = _open_pattern_catalog_file(relative_path)
        metadata = os.fstat(descriptor)
        if (
            not S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_REGEX_CATALOG_FILE_BYTES
        ):
            raise ValueError
        return _read_pattern_catalog_file(
            descriptor,
            path_text,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        yaml.YAMLError,
    ):
        raise ValueError("regex pattern catalog file is invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_pattern_catalog_file(relative_path: Path) -> int:
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    directory = os.open(".", directory_flags)
    try:
        for part in relative_path.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(relative_path.parts[-1], file_flags, dir_fd=directory)
    finally:
        os.close(directory)


def _read_pattern_catalog_file(
    descriptor: int,
    path: str,
    device: int,
    inode: int,
    size: int,
    modified_at_ns: int,
    changed_at_ns: int,
) -> RegexPatternCatalog:
    cache_key = (
        path,
        device,
        inode,
        size,
        modified_at_ns,
        changed_at_ns,
    )
    with _PATTERN_CATALOG_CACHE_LOCK:
        cached = _PATTERN_CATALOG_CACHE.get(cache_key)
        if cached is not None:
            _PATTERN_CATALOG_CACHE.move_to_end(cache_key)
            return cached[0]

    contents = _read_bounded_file(descriptor)
    if len(contents) != size or len(contents) > MAX_REGEX_CATALOG_FILE_BYTES:
        raise ValueError
    final_metadata = os.fstat(descriptor)
    if (
        final_metadata.st_dev,
        final_metadata.st_ino,
        final_metadata.st_size,
        final_metadata.st_mtime_ns,
        final_metadata.st_ctime_ns,
    ) != (device, inode, size, modified_at_ns, changed_at_ns):
        raise ValueError
    values = yaml.load(
        contents.decode("utf-8", errors="strict"),
        Loader=_StrictCatalogLoader,
    )
    catalog = RegexPatternCatalog.model_validate(values)
    if size > MAX_REGEX_PARSED_CATALOG_CACHE_BYTES:
        _LOGGER.debug(
            "privacy_guard_cache_skip cache=regex_parsed_catalog "
            "weight_bytes=%d budget_bytes=%d",
            size,
            MAX_REGEX_PARSED_CATALOG_CACHE_BYTES,
        )
        return catalog
    evicted_entries = 0
    evicted_weight_bytes = 0
    with _PATTERN_CATALOG_CACHE_LOCK:
        cached = _PATTERN_CATALOG_CACHE.get(cache_key)
        if cached is not None:
            _PATTERN_CATALOG_CACHE.move_to_end(cache_key)
            return cached[0]
        _PATTERN_CATALOG_CACHE[cache_key] = (catalog, size)
        global _PATTERN_CATALOG_CACHE_WEIGHT_BYTES
        _PATTERN_CATALOG_CACHE_WEIGHT_BYTES += size
        while (
            len(_PATTERN_CATALOG_CACHE) > _MAX_CACHED_PATTERN_CATALOGS
            or _PATTERN_CATALOG_CACHE_WEIGHT_BYTES
            > MAX_REGEX_PARSED_CATALOG_CACHE_BYTES
        ):
            _, (_, evicted_weight) = _PATTERN_CATALOG_CACHE.popitem(last=False)
            _PATTERN_CATALOG_CACHE_WEIGHT_BYTES -= evicted_weight
            evicted_entries += 1
            evicted_weight_bytes += evicted_weight
    if evicted_entries:
        _LOGGER.debug(
            "privacy_guard_cache_eviction cache=regex_parsed_catalog "
            "entries=%d weight_bytes=%d",
            evicted_entries,
            evicted_weight_bytes,
        )
    return catalog


def _read_bounded_file(descriptor: int) -> bytes:
    contents = bytearray()
    while len(contents) <= MAX_REGEX_CATALOG_FILE_BYTES:
        chunk = os.read(
            descriptor,
            min(64 * 1024, MAX_REGEX_CATALOG_FILE_BYTES + 1 - len(contents)),
        )
        if not chunk:
            break
        contents.extend(chunk)
    return bytes(contents)


def _validate_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or _NAME_PATTERN.fullmatch(value) is None
        or len(value.encode("ascii")) > MAX_REGEX_NAME_BYTES
    ):
        raise ValueError("name is invalid")
    return value


def _compile_pattern_catalog(
    catalog: RegexPatternCatalog,
) -> tuple[_CompiledRule, ...]:
    with _COMPILED_PATTERN_CACHE_LOCK:
        cached = _COMPILED_PATTERN_CACHE.get(catalog)
        if cached is not None:
            _COMPILED_PATTERN_CACHE.move_to_end(catalog)
            return cached[0]

    rules = tuple(
        _compile_rule(
            entity,
            pattern,
            catalog_index=global_index,
            entity_pattern_index=pattern_index,
        )
        for global_index, (entity, pattern_index, pattern) in enumerate(
            _iter_catalog_patterns(catalog)
        )
    )
    weight_bytes = _compiled_pattern_weight(catalog, len(rules))
    if weight_bytes > MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES:
        _LOGGER.debug(
            "privacy_guard_cache_skip cache=regex_compiled "
            "weight_bytes=%d budget_bytes=%d",
            weight_bytes,
            MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES,
        )
        return rules

    evicted_entries = 0
    evicted_weight_bytes = 0
    with _COMPILED_PATTERN_CACHE_LOCK:
        cached = _COMPILED_PATTERN_CACHE.get(catalog)
        if cached is not None:
            _COMPILED_PATTERN_CACHE.move_to_end(catalog)
            return cached[0]

        global _COMPILED_PATTERN_CACHE_WEIGHT_BYTES
        _COMPILED_PATTERN_CACHE[catalog] = (rules, weight_bytes)
        _COMPILED_PATTERN_CACHE_WEIGHT_BYTES += weight_bytes
        while (
            len(_COMPILED_PATTERN_CACHE) > _MAX_CACHED_COMPILED_CATALOGS
            or _COMPILED_PATTERN_CACHE_WEIGHT_BYTES
            > MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES
        ):
            _, (_, evicted_weight) = _COMPILED_PATTERN_CACHE.popitem(last=False)
            _COMPILED_PATTERN_CACHE_WEIGHT_BYTES -= evicted_weight
            evicted_weight_bytes += evicted_weight
            evicted_entries += 1
    if evicted_entries:
        _LOGGER.debug(
            "privacy_guard_cache_eviction cache=regex_compiled "
            "entries=%d weight_bytes=%d",
            evicted_entries,
            evicted_weight_bytes,
        )
    return rules


def _compiled_pattern_weight(
    catalog: RegexPatternCatalog,
    rule_count: int,
) -> int:
    catalog_bytes = len(
        json.dumps(
            catalog.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return catalog_bytes + rule_count * REGEX_COMPILED_RULE_WEIGHT_BYTES


def _clear_compiled_pattern_cache() -> None:
    global _COMPILED_PATTERN_CACHE_WEIGHT_BYTES
    with _COMPILED_PATTERN_CACHE_LOCK:
        _COMPILED_PATTERN_CACHE.clear()
        _COMPILED_PATTERN_CACHE_WEIGHT_BYTES = 0


def _clear_parsed_pattern_catalog_cache() -> None:
    global _PATTERN_CATALOG_CACHE_WEIGHT_BYTES
    with _PATTERN_CATALOG_CACHE_LOCK:
        _PATTERN_CATALOG_CACHE.clear()
        _PATTERN_CATALOG_CACHE_WEIGHT_BYTES = 0


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
    return _CONFIDENCE_RANK[confidence]


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
            raise EngineLimitExceededError("regex replacement exceeds the size limit")
        cursor = detection.end
    projected_size += len(text[cursor:].encode("utf-8"))
    if projected_size > MAX_BODY_BYTES:
        raise EngineLimitExceededError("regex replacement exceeds the size limit")

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
_CONFIDENCE_RANK = {
    ConfidenceLevel.LOW: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.HIGH: 2,
}
_PATTERN_METADATA_KEY = "pattern"
_MAX_CACHED_PATTERN_CATALOGS = 64
_PATTERN_CATALOG_CACHE: OrderedDict[
    tuple[str, int, int, int, int, int],
    tuple[RegexPatternCatalog, int],
] = OrderedDict()
_PATTERN_CATALOG_CACHE_WEIGHT_BYTES = 0
_PATTERN_CATALOG_CACHE_LOCK = RLock()
_MAX_CACHED_COMPILED_CATALOGS = 128
_COMPILED_PATTERN_CACHE: OrderedDict[
    RegexPatternCatalog,
    tuple[tuple[_CompiledRule, ...], int],
] = OrderedDict()
_COMPILED_PATTERN_CACHE_WEIGHT_BYTES = 0
_COMPILED_PATTERN_CACHE_LOCK = RLock()
_LOGGER = get_logger(__name__)


__all__ = [
    "RegexEngine",
    "RegexEngineConfig",
    "RegexEntity",
    "RegexPattern",
    "RegexPatternCatalog",
    "RegexReplacement",
]
