"""Deterministic matching over normalized OpenShell request facts."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Annotated, ClassVar, Literal, Self, TypeAlias, TypeVar

from pydantic import Field, ValidationInfo, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_PROTO_TARGET_BYTES
from egress_gate.errors import GateLimitExceededError
from egress_gate.gates.base import Gate, GateCapabilities, GateConfig
from egress_gate.request import HttpRequest
from egress_gate.result import (
    Finding,
    FindingTypeDefinition,
    GateEvaluation,
    GateName,
    ReasonCode,
)
from egress_gate.string_validators import ScalarString, validate_scalar_string
from egress_gate.timeout import Timeout

_MAX_RULES = 256
_MAX_MATCH_VALUES = 64
_MAX_GLOB_WILDCARDS = 64
_MAX_GLOB_STEPS = (4 * MAX_PROTO_TARGET_BYTES) + 1
_HEADER_TOKEN_CHARS = frozenset("!#$%&'*+-.^_`|~")
_UniqueValueT = TypeVar("_UniqueValueT", str, int)


def _require_non_empty_tuple(
    value: object,
    field_name: str,
    *,
    max_items: int | None = None,
) -> tuple[object, ...]:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    values = tuple(value)
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{field_name} exceeds the size limit")
    return values


def _require_unique(
    values: tuple[_UniqueValueT, ...],
    field_name: str,
) -> tuple[_UniqueValueT, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _validate_non_empty_string(value: object, field_name: str) -> str:
    validated = validate_scalar_string(value)
    if not validated:
        raise ValueError(f"{field_name} must not be empty")
    return validated


def _validate_path_value(value: object) -> str:
    validated = _validate_non_empty_string(value, "path value")
    if len(validated.encode("utf-8")) > MAX_PROTO_TARGET_BYTES:
        raise ValueError("path value exceeds the size limit")
    if "?" in validated or "#" in validated:
        raise ValueError("path value must not contain query or fragment delimiters")
    return validated


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(character) + (ord("a") - ord("A")))
        if "A" <= character <= "Z"
        else character
        for character in value
    )


def _is_ascii_alpha(character: str) -> bool:
    return ("A" <= character <= "Z") or ("a" <= character <= "z")


def _is_ascii_digit(character: str) -> bool:
    return "0" <= character <= "9"


def _canonical_scheme(value: object) -> str:
    scheme = _validate_non_empty_string(value, "scheme")
    if not scheme.isascii() or not _is_ascii_alpha(scheme[0]):
        raise ValueError("scheme is invalid")
    if any(
        not (
            _is_ascii_alpha(character)
            or _is_ascii_digit(character)
            or character in "+-."
        )
        for character in scheme[1:]
    ):
        raise ValueError("scheme is invalid")
    return _ascii_lower(scheme)


def _canonical_header_name(value: object) -> str:
    name = _validate_non_empty_string(value, "header name")
    if not name.isascii() or any(
        not (
            _is_ascii_alpha(character)
            or _is_ascii_digit(character)
            or character in _HEADER_TOKEN_CHARS
        )
        for character in name
    ):
        raise ValueError("header name is invalid")
    return _ascii_lower(name)


def _validate_method(value: object) -> str:
    method = _validate_non_empty_string(value, "method")
    if not method.isascii() or any(
        not (
            _is_ascii_alpha(character)
            or _is_ascii_digit(character)
            or character in _HEADER_TOKEN_CHARS
        )
        for character in method
    ):
        raise ValueError("method is invalid")
    return method


def _canonical_dns_host(host: str) -> str:
    if not host.isascii() or "@" in host or ":" in host or "/" in host:
        raise ValueError("host is not an exact authority host")
    if host.endswith("."):
        name = host[:-1]
    else:
        name = host
    if not name or len(name) > 253:
        raise ValueError("host is invalid")
    labels = name.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise ValueError("host is invalid")
        if label[0] == "-" or label[-1] == "-":
            raise ValueError("host is invalid")
        if any(
            not (
                _is_ascii_alpha(character)
                or _is_ascii_digit(character)
                or character == "-"
            )
            for character in label
        ):
            raise ValueError("host is invalid")
    return _ascii_lower(name)


def _canonical_host(value: object) -> str:
    host = _validate_non_empty_string(value, "host")
    if "[" in host or "]" in host or "@" in host or "%" in host:
        raise ValueError("host is not an exact authority host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return _canonical_dns_host(host)


def _try_canonical_scheme(value: object) -> str | None:
    try:
        return _canonical_scheme(value)
    except ValueError:
        return None


def _try_canonical_host(value: object) -> str | None:
    try:
        return _canonical_host(value)
    except ValueError:
        return None


class ExactPath(StrictDomainModel):
    """Match the request path with case-sensitive exact equality."""

    type: Literal["exact"]
    value: ScalarString

    @field_validator("value")
    @classmethod
    def _value_is_bounded_and_non_empty(cls, value: str) -> str:
        return _validate_path_value(value)


class PrefixPath(StrictDomainModel):
    """Match the request path with a case-sensitive string prefix."""

    type: Literal["prefix"]
    value: ScalarString

    @field_validator("value")
    @classmethod
    def _value_is_bounded_and_non_empty(cls, value: str) -> str:
        return _validate_path_value(value)


class GlobPath(StrictDomainModel):
    """Match a path with literal characters and ``*`` matching any characters."""

    type: Literal["glob"]
    value: ScalarString

    @field_validator("value")
    @classmethod
    def _value_is_bounded_and_non_empty(cls, value: str) -> str:
        validated = _validate_path_value(value)
        if validated.count("*") > _MAX_GLOB_WILDCARDS:
            raise ValueError("glob path exceeds the wildcard limit")
        return validated


RequestRulePath: TypeAlias = Annotated[
    ExactPath | PrefixPath | GlobPath,
    Field(discriminator="type"),
]


class RequestRuleMatch(StrictDomainModel):
    """The non-empty conjunction of one request rule's match predicates."""

    schemes: tuple[ScalarString, ...] | None = None
    hosts: tuple[ScalarString, ...] | None = None
    ports: tuple[int, ...] | None = None
    methods: tuple[ScalarString, ...] | None = None
    path: RequestRulePath | None = None
    headers_present: tuple[ScalarString, ...] | None = None
    process_binaries: tuple[ScalarString, ...] | None = None
    ancestor_binaries: tuple[ScalarString, ...] | None = None

    @field_validator(
        "schemes",
        "hosts",
        "ports",
        "methods",
        "headers_present",
        "process_binaries",
        "ancestor_binaries",
        mode="before",
    )
    @classmethod
    def _values_are_non_empty_tuples(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "match field")
        return _require_non_empty_tuple(
            value,
            str(field_name),
            max_items=_MAX_MATCH_VALUES,
        )

    @field_validator("schemes")
    @classmethod
    def _schemes_are_canonical(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return _require_unique(
            tuple(_canonical_scheme(item) for item in value),
            "schemes",
        )

    @field_validator("hosts")
    @classmethod
    def _hosts_are_canonical(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return _require_unique(
            tuple(_canonical_host(item) for item in value),
            "hosts",
        )

    @field_validator("methods")
    @classmethod
    def _methods_are_http_tokens(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return _require_unique(
            tuple(_validate_method(item) for item in value),
            "methods",
        )

    @field_validator("process_binaries", "ancestor_binaries")
    @classmethod
    def _case_sensitive_values_are_non_empty(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return _require_unique(
            tuple(_validate_non_empty_string(item, "match value") for item in value),
            "match value",
        )

    @field_validator("headers_present")
    @classmethod
    def _headers_are_canonical(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return _require_unique(
            tuple(_canonical_header_name(item) for item in value),
            "headers_present",
        )

    @field_validator("ports")
    @classmethod
    def _ports_are_bounded(
        cls,
        value: tuple[int, ...] | None,
    ) -> tuple[int, ...] | None:
        if value is None:
            return None
        if any(isinstance(port, bool) or port < 1 or port > 65535 for port in value):
            raise ValueError("ports are invalid")
        return _require_unique(value, "ports")

    @model_validator(mode="after")
    def _match_is_non_empty(self) -> Self:
        if not any(
            value is not None
            for value in (
                self.schemes,
                self.hosts,
                self.ports,
                self.methods,
                self.path,
                self.headers_present,
                self.process_binaries,
                self.ancestor_binaries,
            )
        ):
            raise ValueError("rule match must contain at least one condition")
        return self


class _RequestRuleBase(StrictDomainModel):
    name: GateName
    match: RequestRuleMatch


class AllowRequestRule(_RequestRuleBase):
    """A matching rule that terminally allows the current request."""

    decision: Literal["allow"]


class DenyRequestRule(_RequestRuleBase):
    """A matching rule that terminally denies the current request."""

    decision: Literal["deny"]
    reason_code: ReasonCode


RequestRule: TypeAlias = Annotated[
    AllowRequestRule | DenyRequestRule,
    Field(discriminator="decision"),
]


class RequestRulesConfig(GateConfig):
    """Exact policy configuration for the ``request-rules`` gate."""

    gate: Literal["request-rules"] = "request-rules"
    rules: tuple[RequestRule, ...] = Field(repr=False)

    @field_validator("rules", mode="before")
    @classmethod
    def _rules_are_bounded_tuple(cls, value: object) -> object:
        return _require_non_empty_tuple(value, "rules", max_items=_MAX_RULES)

    @model_validator(mode="after")
    def _rule_names_are_unique(self) -> Self:
        names = tuple(rule.name for rule in self.rules)
        if len(names) != len(set(names)):
            raise ValueError("rule names must be unique")
        return self


class RequestRulesGate(Gate[RequestRulesConfig, None]):
    """Apply deny-first, ordered deterministic rules to one HTTP request."""

    capabilities = GateCapabilities(
        reads_target=True,
        reads_context=True,
        reads_headers=True,
        produces_findings=True,
        may_allow=True,
        may_deny=True,
    )
    finding_types: ClassVar[tuple[FindingTypeDefinition, ...]] = (
        FindingTypeDefinition(type="request_rule_match"),
    )

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        winning_rule = _first_matching_rule(
            (rule for rule in self.config.rules if rule.decision == "deny"),
            request,
            timeout,
        )
        if winning_rule is None:
            winning_rule = _first_matching_rule(
                (rule for rule in self.config.rules if rule.decision == "allow"),
                request,
                timeout,
            )
        if winning_rule is None:
            return GateEvaluation.proceed()

        finding = Finding(
            type="request_rule_match",
            label=winning_rule.name,
            count=1,
            severity=winning_rule.decision,
        )
        if winning_rule.decision == "deny":
            return GateEvaluation.deny(
                winning_rule.reason_code,
                findings=(finding,),
            )
        return GateEvaluation.allow(findings=(finding,))


def _first_matching_rule(
    rules: Iterable[AllowRequestRule | DenyRequestRule],
    request: HttpRequest,
    timeout: Timeout,
) -> AllowRequestRule | DenyRequestRule | None:
    for rule in rules:
        timeout.raise_if_expired()
        if _rule_matches(rule, request, timeout):
            return rule
    return None


def _rule_matches(
    rule: AllowRequestRule | DenyRequestRule,
    request: HttpRequest,
    timeout: Timeout,
) -> bool:
    match = rule.match
    target = request.target

    if match.schemes is not None:
        timeout.raise_if_expired()
        scheme = _try_canonical_scheme(target.scheme)
        if scheme is None or scheme not in match.schemes:
            return False
    if match.hosts is not None:
        timeout.raise_if_expired()
        host = _try_canonical_host(target.host)
        if host is None or host not in match.hosts:
            return False
    if match.ports is not None:
        timeout.raise_if_expired()
        if target.port not in match.ports:
            return False
    if match.methods is not None:
        timeout.raise_if_expired()
        if target.method not in match.methods:
            return False
    if match.path is not None:
        timeout.raise_if_expired()
        if not _path_matches(match.path, target.path, timeout):
            return False
    if match.headers_present is not None:
        header_names: set[str] = set()
        for header in request.headers:
            timeout.raise_if_expired()
            try:
                header_names.add(_canonical_header_name(header.name))
            except ValueError:
                continue
        for name in match.headers_present:
            timeout.raise_if_expired()
            if name not in header_names:
                return False
    process = request.context.originating_process
    if match.process_binaries is not None:
        timeout.raise_if_expired()
        if process is None or process.binary not in match.process_binaries:
            return False
    if match.ancestor_binaries is not None:
        timeout.raise_if_expired()
        if process is None:
            return False
        for ancestor in process.ancestors:
            timeout.raise_if_expired()
            if ancestor in match.ancestor_binaries:
                break
        else:
            return False
    return True


def _path_matches(
    path_match: ExactPath | PrefixPath | GlobPath,
    path: str,
    timeout: Timeout,
) -> bool:
    if isinstance(path_match, ExactPath):
        return path == path_match.value
    if isinstance(path_match, PrefixPath):
        return path.startswith(path_match.value)
    return _glob_matches(path_match.value, path, timeout)


def _glob_matches(pattern: str, value: str, timeout: Timeout) -> bool:
    """Match literal characters and ``*`` in linear time with a shared deadline."""

    pattern_index = 0
    value_index = 0
    star_index = -1
    star_value_index = -1
    steps = 0

    while value_index < len(value):
        timeout.raise_if_expired()
        steps += 1
        if steps > _MAX_GLOB_STEPS:
            raise GateLimitExceededError("glob matching exceeds the size limit")
        if (
            pattern_index < len(pattern)
            and pattern[pattern_index] != "*"
            and pattern[pattern_index] == value[value_index]
        ):
            pattern_index += 1
            value_index += 1
        elif pattern_index < len(pattern) and pattern[pattern_index] == "*":
            star_index = pattern_index
            star_value_index = value_index
            pattern_index += 1
        elif star_index >= 0:
            pattern_index = star_index + 1
            star_value_index += 1
            value_index = star_value_index
        else:
            return False

    while pattern_index < len(pattern) and pattern[pattern_index] == "*":
        timeout.raise_if_expired()
        steps += 1
        if steps > _MAX_GLOB_STEPS:
            raise GateLimitExceededError("glob matching exceeds the size limit")
        pattern_index += 1
    timeout.raise_if_expired()
    return pattern_index == len(pattern)


__all__ = [
    "AllowRequestRule",
    "DenyRequestRule",
    "ExactPath",
    "GlobPath",
    "PrefixPath",
    "RequestRule",
    "RequestRuleMatch",
    "RequestRulePath",
    "RequestRulesConfig",
    "RequestRulesGate",
]
