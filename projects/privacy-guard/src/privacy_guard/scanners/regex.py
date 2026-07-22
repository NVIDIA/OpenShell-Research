"""Strict YAML-configured regular-expression scanner."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator
from typing_extensions import TypeIs, override
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    NodeEvent,
    ScalarEvent,
)
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from privacy_guard.constants import (
    MAX_FINDINGS_PER_BLOCK,
    MAX_MATCHES_PER_PATTERN,
    MAX_REGEX_ENTITIES_PER_PROFILE,
    MAX_REGEX_ENTITIES_TOTAL,
    MAX_REGEX_NAME_BYTES,
    MAX_REGEX_PATTERN_BYTES,
    MAX_REGEX_PATTERNS_PER_PROFILE,
    MAX_REGEX_PATTERNS_TOTAL,
    MAX_REGEX_PROFILES,
    MAX_SCANNER_CONFIG_BYTES,
    MAX_SCANNER_CONFIG_NESTING,
    MAX_SCANNER_CONFIG_NODES,
    MAX_SCANNER_CONFIG_SCALAR_BYTES,
    PATTERN_NAME_METADATA_KEY,
)
from privacy_guard.errors import (
    ErrorCode,
    InternalPrivacyGuardError,
    PrivacyGuardError,
)
from privacy_guard.scanners.base import (
    Confidence,
    Finding,
    ScanBudget,
    Scanner,
    ScannerConfig,
    ScannerFindingLimitExceeded,
)
from privacy_guard.validation import StrictSensitiveModel


class RegexPattern(StrictSensitiveModel):
    """One named expression and its explicit engine flags."""

    name: str
    regex: str = Field(repr=False)
    confidence: Confidence
    ignore_case: bool = False
    multiline: bool = False
    dot_all: bool = False
    ascii: bool = False

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("regex")
    @classmethod
    def _regex_is_bounded(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("regex must be a non-empty string")
        if any("\ud800" <= character <= "\udfff" for character in value):
            raise ValueError("regex must contain valid Unicode")
        if len(value.encode("utf-8")) > MAX_REGEX_PATTERN_BYTES:
            raise ValueError("regex is too long")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, value: object) -> Confidence:
        if not isinstance(value, str):
            raise ValueError("confidence must be a string")
        return Confidence(value)


class RegexEntity(StrictSensitiveModel):
    """A named entity and its non-empty pattern catalog."""

    name: str
    patterns: tuple[RegexPattern, ...] = Field(repr=False)

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("patterns", mode="before")
    @classmethod
    def _patterns_are_a_list(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("patterns must be a non-empty list")
        return tuple(value)

    @model_validator(mode="after")
    def _pattern_names_are_unique(self) -> Self:
        names = [pattern.name for pattern in self.patterns]
        if len(names) != len(set(names)):
            raise ValueError("pattern names must be unique within an entity")
        return self


class RegexScannerConfig(ScannerConfig):
    """Selected, immutable regular-expression entity catalog."""

    entities: tuple[RegexEntity, ...] = Field(repr=False)

    @model_validator(mode="after")
    def _catalog_is_consistent(self) -> Self:
        if not self.entities:
            raise ValueError("entity catalog must not be empty")
        names = [entity.name for entity in self.entities]
        if len(names) != len(set(names)) or self.entity_types != frozenset(names):
            raise ValueError("entity catalog is inconsistent")
        if len(self.entities) > MAX_REGEX_ENTITIES_PER_PROFILE:
            raise ValueError("entity catalog is too large")
        if sum(len(entity.patterns) for entity in self.entities) > (
            MAX_REGEX_PATTERNS_PER_PROFILE
        ):
            raise ValueError("pattern catalog is too large")
        return self


class RegexScanner(Scanner[RegexScannerConfig]):
    """Find every configured, possibly overlapping regular-expression match."""

    @override
    def _initialize(self) -> None:
        """Compile the validated catalog once for reuse across scanner calls."""
        try:
            self._rules = tuple(
                _compile_rule(entity.name, pattern)
                for entity in self.config.entities
                for pattern in entity.patterns
            )
        except (RecursionError, ValueError, re.error):
            raise PrivacyGuardError(ErrorCode.SCANNER_CONFIG_INVALID) from None

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        profile: str | None = None,
        *,
        scanner_name: str = "regex",
    ) -> Self:
        """Load, validate, select, and compile a complete YAML catalog."""
        try:
            raw = _read_bounded_file(path)
            value = _load_yaml(raw)
            catalogs = _parse_catalogs(value)
            for entities in catalogs.values():
                for entity in entities:
                    for pattern in entity.patterns:
                        _compile_rule(entity.name, pattern)
            entities = _select_catalog(catalogs, profile)
            config = RegexScannerConfig(
                name=_validate_name(scanner_name),
                entity_types=frozenset(entity.name for entity in entities),
                entities=entities,
            )
            return cls(config)
        except PrivacyGuardError:
            raise
        except (
            OSError,
            RecursionError,
            UnicodeError,
            ValueError,
            TypeError,
            ValidationError,
            yaml.YAMLError,
            re.error,
        ):
            raise PrivacyGuardError(ErrorCode.SCANNER_CONFIG_INVALID) from None

    @override
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        for rule in self._rules:
            match_count = 0
            next_position = 0
            while next_position <= len(text_block):
                budget.remaining_seconds()
                match = rule.expression.search(text_block, next_position)
                budget.remaining_seconds()
                if match is None:
                    break
                start, end = match.span()
                if start == end:
                    raise InternalPrivacyGuardError(ErrorCode.SCANNER_CONFIG_INVALID)
                if match.span(rule.marker_name) == (-1, -1):
                    raise InternalPrivacyGuardError(ErrorCode.SCANNER_CONFIG_INVALID)
                match_count += 1
                if match_count > MAX_MATCHES_PER_PATTERN:
                    raise ScannerFindingLimitExceeded
                findings.append(
                    Finding(
                        entity=rule.entity_name,
                        metadata={PATTERN_NAME_METADATA_KEY: rule.pattern_name},
                        scanner_name=self.scanner_name,
                        start_offset=start,
                        end_offset=end,
                        confidence=rule.confidence,
                    )
                )
                if len(findings) > MAX_FINDINGS_PER_BLOCK:
                    raise ScannerFindingLimitExceeded
                next_position = start + 1
        return tuple(findings)


__all__ = ["RegexEntity", "RegexPattern", "RegexScanner", "RegexScannerConfig"]


@dataclass(frozen=True)
class _CompiledRule:
    entity_name: str
    pattern_name: str
    confidence: Confidence
    marker_name: str
    expression: re.Pattern[str]


def _validate_name(value: str) -> str:
    if not isinstance(value, str) or _NAME_PATTERN.fullmatch(value) is None:
        raise ValueError("name is invalid")
    if len(value.encode("ascii")) > MAX_REGEX_NAME_BYTES:
        raise ValueError("name is too long")
    return value


def _read_bounded_file(path: str | Path) -> bytes:
    """Bound allocation before handing configuration bytes to PyYAML."""
    with Path(path).open("rb") as stream:
        return stream.read(MAX_SCANNER_CONFIG_BYTES + 1)


def _compile_rule(entity_name: str, pattern: RegexPattern) -> _CompiledRule:
    flags = 0
    if pattern.ignore_case:
        flags |= re.IGNORECASE
    if pattern.multiline:
        flags |= re.MULTILINE
    if pattern.dot_all:
        flags |= re.DOTALL
    if pattern.ascii:
        flags |= re.ASCII
    if _contains_inline_flags(pattern.regex):
        raise ValueError("inline flags are unsupported")
    unmarked = re.compile(pattern.regex, flags)
    if unmarked.groupindex:
        raise ValueError("named groups are reserved")
    if unmarked.search("") is not None:
        raise ValueError("regex must not match empty input")
    marker_name = pattern.name.replace("-", "_")
    expression = re.compile(f"(?:{pattern.regex})(?P<{marker_name}>)", flags)
    return _CompiledRule(
        entity_name=entity_name,
        pattern_name=pattern.name,
        confidence=pattern.confidence,
        marker_name=marker_name,
        expression=expression,
    )


def _contains_inline_flags(expression: str) -> bool:
    escaped = False
    in_character_class = False
    index = 0
    while index < len(expression):
        character = expression[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "[":
            in_character_class = True
        elif character == "]" and in_character_class:
            in_character_class = False
        elif not in_character_class and expression.startswith("(?", index):
            suffix = expression[index + 2 :]
            flag_match = re.match(r"[A-Za-z0-9-]+(?=[:)])", suffix)
            if flag_match is not None:
                return True
        index += 1
    return False


def _load_yaml(raw: bytes) -> object:
    if not raw or len(raw) > MAX_SCANNER_CONFIG_BYTES:
        raise ValueError("configuration size is invalid")
    text = raw.decode("utf-8", errors="strict")
    _validate_yaml_events(text)
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    if root is None:
        raise ValueError("configuration is empty")
    _validate_yaml_node(root, depth=1, count=[0])
    return yaml.safe_load(text)


def _validate_yaml_events(text: str) -> None:
    depth = 0
    node_count = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, AliasEvent) or (
            isinstance(event, NodeEvent) and event.anchor is not None
        ):
            raise ValueError("anchors and aliases are unsupported")
        if isinstance(event, NodeEvent):
            node_count += 1
            if node_count > MAX_SCANNER_CONFIG_NODES:
                raise ValueError("configuration has too many nodes")
            if event.tag is not None and not event.tag.startswith("tag:yaml.org,2002:"):
                raise ValueError("YAML tag is unsupported")
        if isinstance(event, ScalarEvent) and (
            len(event.value.encode("utf-8")) > MAX_SCANNER_CONFIG_SCALAR_BYTES
        ):
            raise ValueError("configuration scalar is too large")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > MAX_SCANNER_CONFIG_NESTING:
                raise ValueError("configuration nesting is too deep")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1


def _validate_yaml_node(node: Node, *, depth: int, count: list[int]) -> None:
    count[0] += 1
    if depth > MAX_SCANNER_CONFIG_NESTING or count[0] > MAX_SCANNER_CONFIG_NODES:
        raise ValueError("configuration structure is too large")
    if isinstance(node, ScalarNode):
        if len(node.value.encode("utf-8")) > MAX_SCANNER_CONFIG_SCALAR_BYTES:
            raise ValueError("configuration scalar is too large")
        return
    if isinstance(node, SequenceNode):
        for child in node.value:
            _validate_yaml_node(child, depth=depth + 1, count=count)
        return
    if isinstance(node, MappingNode):
        keys: set[tuple[str, str]] = set()
        for key, value in node.value:
            if not isinstance(key, ScalarNode):
                raise ValueError("mapping keys must be scalars")
            identity = (key.tag, key.value)
            if identity in keys:
                raise ValueError("mapping keys must be unique")
            keys.add(identity)
            _validate_yaml_node(key, depth=depth + 1, count=count)
            _validate_yaml_node(value, depth=depth + 1, count=count)
        return
    raise ValueError("YAML node is unsupported")


def _parse_catalogs(value: object) -> dict[str | None, tuple[RegexEntity, ...]]:
    if isinstance(value, list):
        return {None: _parse_entities(value)}
    if not _is_object_mapping(value) or set(value) != {"profiles"}:
        raise ValueError("configuration shape is invalid")
    profiles = value["profiles"]
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("profiles must be a non-empty mapping")
    if len(profiles) > MAX_REGEX_PROFILES:
        raise ValueError("too many profiles")
    catalogs: dict[str | None, tuple[RegexEntity, ...]] = {}
    for name, entities in profiles.items():
        if not isinstance(name, str):
            raise ValueError("profile name must be a string")
        catalogs[_validate_name(name)] = _parse_entities(entities)
    _validate_document_totals(catalogs)
    return catalogs


def _is_object_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _parse_entities(value: object) -> tuple[RegexEntity, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("entity catalog must be a non-empty list")
    entities = tuple(RegexEntity.model_validate(item) for item in value)
    names = [entity.name for entity in entities]
    if len(names) != len(set(names)):
        raise ValueError("entity names must be unique")
    if len(entities) > MAX_REGEX_ENTITIES_PER_PROFILE:
        raise ValueError("too many entities")
    pattern_count = sum(len(entity.patterns) for entity in entities)
    if pattern_count > MAX_REGEX_PATTERNS_PER_PROFILE:
        raise ValueError("too many patterns")
    return entities


def _validate_document_totals(
    catalogs: Mapping[str | None, tuple[RegexEntity, ...]],
) -> None:
    if sum(len(entities) for entities in catalogs.values()) > MAX_REGEX_ENTITIES_TOTAL:
        raise ValueError("too many entities in document")
    if (
        sum(
            len(entity.patterns)
            for entities in catalogs.values()
            for entity in entities
        )
        > MAX_REGEX_PATTERNS_TOTAL
    ):
        raise ValueError("too many patterns in document")


def _select_catalog(
    catalogs: Mapping[str | None, tuple[RegexEntity, ...]], profile: str | None
) -> tuple[RegexEntity, ...]:
    if None in catalogs:
        if profile is not None:
            raise ValueError("profile is invalid for single-profile configuration")
        return catalogs[None]
    if profile is None:
        raise ValueError("profile is required")
    try:
        return catalogs[_validate_name(profile)]
    except KeyError:
        raise ValueError("profile does not exist") from None


_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
