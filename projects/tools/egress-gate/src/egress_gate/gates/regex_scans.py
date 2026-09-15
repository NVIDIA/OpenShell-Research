# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public action and source configuration for regex request scans."""

from __future__ import annotations

from string import Formatter
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    MAX_DIAGNOSTIC_TEXT_BYTES,
    MAX_JSON_SELECTORS,
    MAX_PROTO_HEADERS,
)
from egress_gate.request import HeaderName
from egress_gate.request_content import (
    JsonMessageMapConfig,
    JsonSelector,
    MessageBlockKind,
    MessageRole,
)
from egress_gate.string_validators import ScalarString, validate_scalar_string


class RegexDetectAction(StrictDomainModel):
    """Report matches and continue without changing the request."""

    kind: Literal["detect"]


class RegexDenyAction(StrictDomainModel):
    """Deny the request when the scan finds a match."""

    kind: Literal["deny"]


class RegexReplaceAction(StrictDomainModel):
    """Replace body matches with a constrained template."""

    kind: Literal["replace"]
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


RegexReadOnlyAction: TypeAlias = Annotated[
    RegexDetectAction | RegexDenyAction,
    Field(discriminator="kind"),
]
RegexBodyAction: TypeAlias = Annotated[
    RegexDetectAction | RegexDenyAction | RegexReplaceAction,
    Field(discriminator="kind"),
]


class RegexBodyScan(StrictDomainModel):
    """Scan the UTF-8 request body and apply a body-compatible action."""

    kind: Literal["body"]
    action: RegexBodyAction


class RegexPathScan(StrictDomainModel):
    """Scan the request path and detect or deny matches."""

    kind: Literal["path"]
    action: RegexReadOnlyAction


class RegexQueryScan(StrictDomainModel):
    """Scan the raw request query and detect or deny matches."""

    kind: Literal["query"]
    action: RegexReadOnlyAction


class RegexHeaderScan(StrictDomainModel):
    """Scan values from named request headers and detect or deny matches."""

    kind: Literal["header"]
    names: tuple[HeaderName, ...] = Field(min_length=1, max_length=MAX_PROTO_HEADERS)
    action: RegexReadOnlyAction

    @field_validator("names", mode="before")
    @classmethod
    def _names_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _names_are_unique(self) -> Self:
        normalized = tuple(name.casefold() for name in self.names)
        if len(normalized) != len(set(normalized)):
            raise ValueError("header scan names must be unique")
        return self


class RegexJsonFieldsScan(StrictDomainModel):
    """Scan selected JSON string values and optionally replace their matches."""

    kind: Literal["json-fields"]
    selectors: tuple[JsonSelector, ...] = Field(
        min_length=1,
        max_length=MAX_JSON_SELECTORS,
    )
    action: RegexBodyAction

    @field_validator("selectors", mode="before")
    @classmethod
    def _selectors_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value


class RegexMessageBlocksScan(StrictDomainModel):
    """Scan normalized text-bearing JSON message blocks."""

    kind: Literal["message-blocks"]
    message_mapping: JsonMessageMapConfig
    roles: tuple[MessageRole, ...] | None = None
    block_kinds: tuple[MessageBlockKind, ...] | None = None
    action: RegexBodyAction

    @field_validator("roles", mode="before")
    @classmethod
    def _parse_roles(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, list | tuple):
            return value
        return tuple(
            item
            if isinstance(item, MessageRole)
            else MessageRole(validate_scalar_string(item))
            for item in value
        )

    @field_validator("block_kinds", mode="before")
    @classmethod
    def _parse_block_kinds(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, list | tuple):
            return value
        return tuple(
            item
            if isinstance(item, MessageBlockKind)
            else MessageBlockKind(validate_scalar_string(item))
            for item in value
        )

    @model_validator(mode="after")
    def _filters_are_unique(self) -> Self:
        for values in (self.roles, self.block_kinds):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("message block filters must be unique")
        return self


RegexScan: TypeAlias = Annotated[
    RegexBodyScan
    | RegexPathScan
    | RegexQueryScan
    | RegexHeaderScan
    | RegexJsonFieldsScan
    | RegexMessageBlocksScan,
    Field(discriminator="kind"),
]


__all__ = [
    "RegexBodyAction",
    "RegexBodyScan",
    "RegexDenyAction",
    "RegexDetectAction",
    "RegexHeaderScan",
    "RegexJsonFieldsScan",
    "RegexMessageBlocksScan",
    "RegexPathScan",
    "RegexQueryScan",
    "RegexReadOnlyAction",
    "RegexReplaceAction",
    "RegexScan",
]
