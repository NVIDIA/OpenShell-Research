"""Validated Slop Cop configuration."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import regex
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

MAX_SOURCE_BYTES = 1_048_576
MAX_SIGNALS_PER_RULE_FILE = 5_000
MAX_REGEX_PATTERN_LENGTH = 500
MAX_EXTERNAL_RESPONSE_BYTES = 1_048_576
DEFAULT_EXTERNAL_RESPONSE_BYTES = 65_536
MAX_EXTERNAL_CONCURRENCY = 4
MAX_EXTERNAL_FILE_SECONDS = 60.0

RuleId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$",
    ),
]
CategoryId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
ServiceName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)]


class StrictModel(BaseModel):
    """Base for immutable configuration records with no ignored keys."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ErrorPolicy(StrEnum):
    FAIL = "fail"
    ADVISORY = "advisory"


class DensityUnit(StrEnum):
    WORD = "word"
    SENTENCE = "sentence"
    PARAGRAPH = "paragraph"


class ContextConfig(StrictModel):
    scan_blockquotes: bool = False
    scan_headings: bool = True
    scan_captions: bool = True


class DocumentDensityPolicy(StrictModel):
    unit: DensityUnit
    interval: int = Field(ge=1, le=1_000_000)
    allowed_units: int = Field(ge=0, le=MAX_SIGNALS_PER_RULE_FILE)


class PassageDensityPolicy(StrictModel):
    unit: DensityUnit
    window: int = Field(ge=1, le=100_000)
    allowed_units: int = Field(ge=0, le=MAX_SIGNALS_PER_RULE_FILE)
    first_cost: float = Field(ge=0, le=100)
    repeat_cost: float = Field(ge=0, le=100)
    cap: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_costs(self) -> PassageDensityPolicy:
        if self.cap == 0 and (self.first_cost or self.repeat_cost):
            raise ValueError("density costs must be zero when density cap is zero")
        if self.cap > 0 and self.first_cost > self.cap:
            raise ValueError("density first_cost cannot exceed density cap")
        return self


class CategoryPolicy(StrictModel):
    cap: float = Field(ge=0, le=100)
    density: PassageDensityPolicy | None = None

    @model_validator(mode="after")
    def validate_density_cap(self) -> CategoryPolicy:
        if self.density is not None and self.density.cap > self.cap:
            raise ValueError("category density cap cannot exceed category cap")
        return self


class RulePolicy(StrictModel):
    enabled: bool = True
    severity: Severity
    blocking: bool = False
    on_error: ErrorPolicy = ErrorPolicy.FAIL
    service: ServiceName | None = None
    max_signal_units: int = Field(ge=1, le=MAX_SIGNALS_PER_RULE_FILE)
    fixed_allowance: int = Field(ge=0, le=MAX_SIGNALS_PER_RULE_FILE)
    first_cost: float = Field(ge=0, le=100)
    repeat_cost: float = Field(ge=0, le=100)
    cap: float = Field(ge=0, le=100)
    document_density: DocumentDensityPolicy | None = None
    density: PassageDensityPolicy | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> RulePolicy:
        if self.first_cost > self.cap:
            raise ValueError("first_cost cannot exceed rule cap")
        if self.severity is Severity.INFO and (self.blocking or self.cap != 0):
            raise ValueError("info rules must be advisory with zero cost")
        if self.severity is Severity.ERROR and not self.blocking:
            raise ValueError("error rules must be blocking")
        if self.blocking and self.severity is not Severity.ERROR:
            raise ValueError("blocking rules must use error severity")
        if self.cap == 0 and (self.first_cost or self.repeat_cost):
            raise ValueError("rule costs must be zero when rule cap is zero")
        if self.density is not None and self.density.cap > self.cap:
            raise ValueError("density cap cannot exceed rule cap")
        return self


class ServiceConfig(StrictModel):
    url: str = Field(min_length=1, max_length=2_048)
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    timeout_seconds: float = Field(default=20.0, gt=0, le=60.0)
    max_response_bytes: int = Field(
        default=DEFAULT_EXTERNAL_RESPONSE_BYTES,
        ge=1,
        le=MAX_EXTERNAL_RESPONSE_BYTES,
    )
    max_attempts: int = Field(default=1, ge=1, le=3)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
            raise ValueError(
                "service URL must be an absolute origin without credentials or fragment"
            )
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("service URL must use HTTPS; HTTP is allowed only for loopback tests")
        return value


class VocabularyConfig(StrictModel):
    allowed_terms: tuple[str, ...] = ()

    @field_validator("allowed_terms")
    @classmethod
    def validate_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value or len(value) > 128 for value in cleaned):
            raise ValueError("allowed terms must contain 1 to 128 characters")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("allowed terms must be unique without regard to case")
        return cleaned


class DeclarativeRuleBase(StrictModel):
    id: RuleId
    version: int = Field(ge=1)
    category: CategoryId
    severity: Severity
    title: ShortText
    rationale: ShortText
    advice: ShortText
    enabled: bool = True
    blocking: bool = False
    on_error: ErrorPolicy = ErrorPolicy.FAIL
    max_signal_units: int = Field(ge=1, le=MAX_SIGNALS_PER_RULE_FILE)
    fixed_allowance: int = Field(ge=0, le=MAX_SIGNALS_PER_RULE_FILE)
    first_cost: float = Field(ge=0, le=100)
    repeat_cost: float = Field(ge=0, le=100)
    cap: float = Field(ge=0, le=100)
    document_density: DocumentDensityPolicy | None = None
    density: PassageDensityPolicy | None = None
    score_group: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_scoring(self) -> DeclarativeRuleBase:
        RulePolicy.model_validate(
            {
                "enabled": self.enabled,
                "severity": self.severity,
                "blocking": self.blocking,
                "on_error": self.on_error,
                "max_signal_units": self.max_signal_units,
                "fixed_allowance": self.fixed_allowance,
                "first_cost": self.first_cost,
                "repeat_cost": self.repeat_cost,
                "cap": self.cap,
                "document_density": self.document_density,
                "density": self.density,
            }
        )
        return self


class PhraseRuleConfig(DeclarativeRuleBase):
    phrases: tuple[str, ...] = Field(min_length=1, max_length=100)

    @field_validator("phrases")
    @classmethod
    def validate_phrases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value or len(value) > 200 for value in cleaned):
            raise ValueError("phrases must contain 1 to 200 characters")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("phrases must be unique without regard to case")
        return cleaned


class RegexFlag(StrEnum):
    IGNORECASE = "IGNORECASE"
    MULTILINE = "MULTILINE"
    DOTALL = "DOTALL"
    VERBOSE = "VERBOSE"


_UNSAFE_REGEX = re.compile(
    r"(?:\\[1-9]|\\g[<{]|\(\?R|\(\?0|\(\?&|\(\?P>|\(\?\(|\(\?<=[^)]|\(\?<![^)]|\(\?[aiLmsux-]+[:)])"
)


class RegexRuleConfig(DeclarativeRuleBase):
    pattern: str = Field(min_length=1, max_length=MAX_REGEX_PATTERN_LENGTH)
    flags: tuple[RegexFlag, ...] = ()

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        if _UNSAFE_REGEX.search(value):
            raise ValueError("regex contains an unsupported construct")
        try:
            regex.compile(value)
        except regex.error as error:
            raise ValueError(f"invalid regex: {error}") from error
        return value

    @field_validator("flags")
    @classmethod
    def validate_flags(cls, values: tuple[RegexFlag, ...]) -> tuple[RegexFlag, ...]:
        if len(set(values)) != len(values):
            raise ValueError("regex flags must be unique")
        return values


class CustomRulesConfig(StrictModel):
    phrase: tuple[PhraseRuleConfig, ...] = ()
    regex: tuple[RegexRuleConfig, ...] = ()


class SlopCopConfig(StrictModel):
    schema_version: Literal[1]
    profile: Literal["dev-notes"]
    threshold: int = Field(ge=0, le=100)
    paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    contexts: ContextConfig = ContextConfig()
    categories: dict[CategoryId, CategoryPolicy]
    rules: dict[RuleId, RulePolicy]
    vocabulary: VocabularyConfig = VocabularyConfig()
    custom_rules: CustomRulesConfig = CustomRulesConfig()
    services: dict[ServiceName, ServiceConfig] = Field(default_factory=dict)
    source_max_bytes: int = Field(default=MAX_SOURCE_BYTES, ge=1, le=MAX_SOURCE_BYTES)
    external_concurrency: int = Field(
        default=MAX_EXTERNAL_CONCURRENCY, ge=1, le=MAX_EXTERNAL_CONCURRENCY
    )
    external_file_timeout_seconds: float = Field(
        default=MAX_EXTERNAL_FILE_SECONDS, gt=0, le=MAX_EXTERNAL_FILE_SECONDS
    )

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for path in paths:
            pure = PurePosixPath(path)
            if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path:
                raise ValueError("scan paths must be nonempty relative POSIX globs without '..'")
        return paths

    @model_validator(mode="after")
    def validate_references(self) -> SlopCopConfig:
        declarative = (*self.custom_rules.phrase, *self.custom_rules.regex)
        all_ids = [*self.rules, *(rule.id for rule in declarative)]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("rule IDs must be unique across Python and declarative rules")
        for rule in declarative:
            if rule.category not in self.categories:
                raise ValueError(f"rule {rule.id!r} references unknown category {rule.category!r}")
        for rule_id, policy in self.rules.items():
            if policy.service is not None and policy.service not in self.services:
                raise ValueError(f"rule {rule_id!r} references unknown service {policy.service!r}")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


_CONFIG_ADAPTER = TypeAdapter(SlopCopConfig)


def load_config(path: str | Path) -> SlopCopConfig:
    """Load and strictly validate a UTF-8 TOML configuration file."""

    config_path = Path(path)
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load configuration {config_path}: {error}") from error
    # TOML has no tuple or enum scalar types. Allow those representation
    # conversions while retaining the model's bounded values and forbidden keys.
    return _CONFIG_ADAPTER.validate_python(data, strict=False)


__all__ = [
    "CategoryPolicy",
    "ContextConfig",
    "DensityUnit",
    "DocumentDensityPolicy",
    "ErrorPolicy",
    "PassageDensityPolicy",
    "PhraseRuleConfig",
    "RegexFlag",
    "RegexRuleConfig",
    "RulePolicy",
    "ServiceConfig",
    "Severity",
    "SlopCopConfig",
    "load_config",
]
