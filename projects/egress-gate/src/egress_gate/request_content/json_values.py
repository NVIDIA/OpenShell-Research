# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared JSON value kinds used by public documents and the private parser."""

from enum import StrEnum

from pydantic import Field

from egress_gate.base import StrictDomainModel


class JsonNodeKind(StrEnum):
    """The JSON value kind at one immutable document node."""

    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"


class JsonNode(StrictDomainModel):
    """Opaque immutable reference to one node in a ``JsonDocument``."""

    id: str
    path: tuple[str | int, ...] = Field(repr=False)
    kind: JsonNodeKind


class JsonTextNode(StrictDomainModel):
    """One selected JSON string with a stable document-local identity."""

    id: str
    path: tuple[str | int, ...] = Field(repr=False)
    text: str = Field(repr=False)


__all__ = ["JsonNode", "JsonNodeKind", "JsonTextNode"]
