# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict canonical model-request schema and encoding."""

from __future__ import annotations

import json
import math
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.string_validators import ScalarString


class CanonicalRole(StrEnum):
    """Roles supported by the pinned provider schema."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class CanonicalFunctionCallV1(StrictDomainModel):
    """One model-produced function call without lossy argument parsing."""

    id: ScalarString
    name: ScalarString
    arguments: ScalarString


class CanonicalMessageV1(StrictDomainModel):
    """One ordered, provider-visible message."""

    role: CanonicalRole
    content: ScalarString | None
    name: ScalarString | None = None
    tool_call_id: ScalarString | None = None
    tool_calls: tuple[CanonicalFunctionCallV1, ...] = ()

    @model_validator(mode="after")
    def _role_fields_are_consistent(self) -> CanonicalMessageV1:
        if self.role is CanonicalRole.TOOL:
            if self.content is None or self.tool_call_id is None or self.tool_calls:
                raise ValueError("tool messages require content and tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("only tool messages may carry tool_call_id")
        if self.tool_calls and self.role is not CanonicalRole.ASSISTANT:
            raise ValueError("only assistant messages may carry tool calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("messages require content or tool calls")
        return self


class CanonicalToolV1(StrictDomainModel):
    """One complete function-tool definition."""

    name: ScalarString
    description: ScalarString
    input_schema: dict[str, object]

    @field_validator("input_schema")
    @classmethod
    def _schema_is_canonical_json(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_json_value(value)
        return value


class CanonicalToolChoiceV1(StrictDomainModel):
    """Pinned OpenAI tool-selection semantics."""

    mode: Literal["auto", "none", "required", "function"]
    function_name: ScalarString | None = None

    @model_validator(mode="after")
    def _function_name_matches_mode(self) -> CanonicalToolChoiceV1:
        if (self.mode == "function") != (self.function_name is not None):
            raise ValueError("function tool choice requires exactly one name")
        return self


class CanonicalGenerationV1(StrictDomainModel):
    """Semantic generation fields accepted from the pinned Pi serializer."""

    temperature: float | None = Field(default=None, allow_inf_nan=False)
    max_tokens: int = Field(ge=1)

    @field_validator("temperature", mode="before")
    @classmethod
    def _normalize_temperature(cls, value: object) -> float | None:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("temperature must be numeric")
        normalized = float(value)
        return 0.0 if normalized == 0 else normalized


class ModelRequestV1(StrictDomainModel):
    """Validated semantic view of one supported provider request."""

    schema_version: Literal["model-request.v1"] = "model-request.v1"
    model: ScalarString
    messages: tuple[CanonicalMessageV1, ...]
    tools: tuple[CanonicalToolV1, ...]
    tool_choice: CanonicalToolChoiceV1
    generation: CanonicalGenerationV1


def canonical_json_bytes(value: StrictDomainModel) -> bytes:
    """Encode a validated model with stable UTF-8 JSON semantics."""
    return json.dumps(
        value.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            key.encode("utf-8", errors="strict")
            _validate_json_value(item)
        return
    raise ValueError("value is not canonical JSON")


__all__ = [
    "CanonicalFunctionCallV1",
    "CanonicalGenerationV1",
    "CanonicalMessageV1",
    "CanonicalRole",
    "CanonicalToolChoiceV1",
    "CanonicalToolV1",
    "ModelRequestV1",
    "canonical_json_bytes",
]
