# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pi admission shapes and provider request validation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, TypeAlias

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from egress_gate.admission.canonical import canonical_json_bytes
from egress_gate.admission.models import (
    AdmissionHook,
    HarnessAdmissionContext,
)
from egress_gate.base import StrictDomainModel
from egress_gate.errors import BodyFormatError, GateInputError
from egress_gate.request import HttpRequest
from egress_gate.request_content import JsonDocument
from egress_gate.string_validators import ScalarString
from egress_gate.timeout import Timeout


class AdmissionShapeError(ValueError):
    """A content-safe signal that an admission shape is unsupported."""


class AdmissionMutationError(ValueError):
    """A content-safe signal that a Gate changed a read-only field."""


class ProviderShapeError(ValueError):
    """A content-safe signal that a provider request is unsupported."""


PiMessageOrigin: TypeAlias = Literal["user", "system", "compaction_summary"]


class PiMessageV1(StrictDomainModel):
    """Text-bearing message submitted by the managed Pi harness."""

    schema_version: Literal["openshell.pi-message.v1"]
    origin: PiMessageOrigin
    text: ScalarString


class PiTextContentV1(StrictDomainModel):
    """One Pi text content block."""

    type: Literal["text"]
    text: ScalarString


class PiToolResultV1(StrictDomainModel):
    """Provider-relevant fields from one Pi tool-result message."""

    schema_version: Literal["openshell.pi-tool-result.v1"]
    tool_call_id: ScalarString
    tool_name: ScalarString
    content: tuple[PiTextContentV1, ...]
    is_error: bool

    @field_validator("content", mode="before")
    @classmethod
    def _content_is_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class PiAssistantToolCallV1(StrictDomainModel):
    """One immutable Pi assistant tool call."""

    id: ScalarString
    name: ScalarString
    arguments: dict[str, object]
    thought_signature: ScalarString | None = None


class PiThinkingContentV1(StrictDomainModel):
    """Reasoning text with immutable provider replay metadata."""

    text: ScalarString
    signature: ScalarString | None = None


class PiAssistantMessageV1(StrictDomainModel):
    """Replaceable assistant text and immutable tool calls."""

    schema_version: Literal["openshell.pi-assistant-message.v1"]
    text: ScalarString
    tool_calls: tuple[PiAssistantToolCallV1, ...]
    thinking: tuple[PiThinkingContentV1, ...] = ()

    @field_validator("tool_calls", "thinking", mode="before")
    @classmethod
    def _tool_calls_are_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class UserContextEntryV1(StrictDomainModel):
    """One ordered user entry sent to a provider."""

    role: Literal["user"]
    text: ScalarString


class ToolContextEntryV1(StrictDomainModel):
    """One ordered tool entry sent to a provider."""

    role: Literal["tool"]
    tool_call_id: ScalarString
    text: ScalarString


ContextEntryV1: TypeAlias = UserContextEntryV1 | ToolContextEntryV1


class PiProviderContextV1(StrictDomainModel):
    """Every provider-visible user and tool entry in order."""

    schema_version: Literal["openshell.pi-provider-context.v1"]
    entries: tuple[ContextEntryV1, ...] = Field(min_length=1)

    @field_validator("entries", mode="before")
    @classmethod
    def _entries_are_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


HarnessNative: TypeAlias = (
    PiMessageV1 | PiToolResultV1 | PiAssistantMessageV1 | PiProviderContextV1
)
AttestedEntries: TypeAlias = tuple[ContextEntryV1, ...]


def parse_pi_request(
    body: bytes, context: HarnessAdmissionContext, timeout: Timeout
) -> HarnessNative:
    """Validate one fixed Pi hook/schema pair before and after policy execution."""
    if context.harness != "pi":
        raise AdmissionShapeError("harness admission shape is unsupported")
    model = _PI_SHAPES[context.hook]
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        native = model.model_validate(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi request body is unsupported") from None
    if native.schema_version != context.schema_version:
        raise AdmissionShapeError("Pi request schema is unsupported")
    if isinstance(native, PiMessageV1) and native.origin != _PI_ORIGINS[context.hook]:
        raise AdmissionShapeError("Pi message origin is unsupported")
    if isinstance(native, PiMessageV1 | PiProviderContextV1):
        if canonical_json_bytes(native) != body:
            raise AdmissionShapeError("Pi request body is not canonical JSON")
    return native


def validate_pi_replacement(before: HarnessNative, after: HarnessNative) -> None:
    """Allow text replacement without changing executable fields or entry identity."""
    if isinstance(before, PiAssistantMessageV1) and isinstance(
        after, PiAssistantMessageV1
    ):
        if before.tool_calls != after.tool_calls:
            raise AdmissionMutationError("admission changed assistant tool calls")
        if len(before.thinking) != len(after.thinking):
            raise AdmissionMutationError("admission changed reasoning structure")
        for original, replacement in zip(before.thinking, after.thinking):
            if original.signature != replacement.signature or (
                original.signature
                not in (None, "reasoning", "reasoning_content", "reasoning_text")
                and original.text != replacement.text
            ):
                raise AdmissionMutationError("admission changed reasoning replay data")
    elif isinstance(before, PiToolResultV1) and isinstance(after, PiToolResultV1):
        if before.model_dump(exclude={"content"}) != after.model_dump(
            exclude={"content"}
        ):
            raise AdmissionMutationError("admission changed tool-result metadata")
    elif isinstance(before, PiProviderContextV1) and isinstance(
        after, PiProviderContextV1
    ):
        if tuple(
            (e.role, getattr(e, "tool_call_id", None)) for e in before.entries
        ) != tuple((e.role, getattr(e, "tool_call_id", None)) for e in after.entries):
            raise AdmissionMutationError("admission changed provider-context structure")


class _ProviderCacheControl(StrictDomainModel):
    type: Literal["ephemeral"]
    ttl: Literal["1h"] | None = None


class _ProviderTextBlock(StrictDomainModel):
    type: Literal["text"]
    text: ScalarString
    cache_control: _ProviderCacheControl | None = None


class _ProviderFunction(StrictDomainModel):
    name: ScalarString
    arguments: ScalarString


class _ProviderToolCall(StrictDomainModel):
    id: ScalarString
    type: Literal["function"]
    function: _ProviderFunction


class _ProviderMessage(StrictDomainModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: ScalarString | tuple[_ProviderTextBlock, ...] | None = None
    name: ScalarString | None = None
    tool_call_id: ScalarString | None = None
    tool_calls: tuple[_ProviderToolCall, ...] = ()
    reasoning_content: ScalarString | None = None
    reasoning: ScalarString | None = None
    reasoning_text: ScalarString | None = None
    # Provider-owned replay objects are inspected by request policy, never rewritten.
    reasoning_details: tuple[dict[str, object], ...] = ()

    @field_validator("content", "tool_calls", "reasoning_details", mode="before")
    @classmethod
    def _provider_sequences_are_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _role_fields_are_consistent(self) -> _ProviderMessage:
        if self.role == "tool":
            if self.content is None or self.tool_call_id is None or self.tool_calls:
                raise ValueError("tool messages require content and tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("only tool messages may carry tool_call_id")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages may carry tool calls")
        has_reasoning = any(
            (
                self.reasoning,
                self.reasoning_text,
                self.reasoning_content,
                self.reasoning_details,
            )
        )
        if has_reasoning and self.role != "assistant":
            raise ValueError("only assistant messages may carry reasoning")
        if self.content is None and not self.tool_calls and not has_reasoning:
            raise ValueError("messages require content or tool calls")
        return self

    @model_validator(mode="after")
    def _optional_fields_have_one_representation(self) -> _ProviderMessage:
        if "content" not in self.model_fields_set:
            raise ValueError("provider messages must include content")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("provider message name cannot be null")
        if "tool_call_id" in self.model_fields_set and self.tool_call_id is None:
            raise ValueError("provider tool-call ID cannot be null")
        if "tool_calls" in self.model_fields_set and not self.tool_calls:
            raise ValueError("provider tool calls cannot be empty")
        if (
            "reasoning_content" in self.model_fields_set
            and self.reasoning_content is None
        ):
            raise ValueError("provider reasoning content cannot be null")
        return self


class _ProviderFunctionDefinition(StrictDomainModel):
    name: ScalarString
    description: ScalarString
    parameters: dict[str, object]
    strict: bool | None = None

    @model_validator(mode="after")
    def _optional_strict_is_not_null(self) -> _ProviderFunctionDefinition:
        if "strict" in self.model_fields_set and self.strict is None:
            raise ValueError("provider function strict cannot be null")
        return self


class _ProviderTool(StrictDomainModel):
    type: Literal["function"]
    function: _ProviderFunctionDefinition
    cache_control: _ProviderCacheControl | None = None


class _ProviderNamedChoiceFunction(StrictDomainModel):
    name: ScalarString


class _ProviderNamedToolChoice(StrictDomainModel):
    type: Literal["function"]
    function: _ProviderNamedChoiceFunction


class _ProviderStreamOptions(StrictDomainModel):
    include_usage: Literal[True]


class _ProviderReasoning(StrictDomainModel):
    effort: ScalarString | None = None
    enabled: bool | None = None


class _ProviderRequest(StrictDomainModel):
    model: ScalarString
    messages: tuple[_ProviderMessage, ...]
    tools: tuple[_ProviderTool, ...] = ()
    tool_choice: Literal["auto", "none", "required"] | _ProviderNamedToolChoice = "auto"
    temperature: int | float | None = Field(default=None, allow_inf_nan=False)
    top_p: int | float | None = Field(default=None, allow_inf_nan=False)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    stream: Literal[True]
    stream_options: _ProviderStreamOptions | None = None
    store: Literal[False] | None = None
    prompt_cache_key: ScalarString | None = None
    prompt_cache_retention: Literal["24h"] | None = None
    reasoning_effort: ScalarString | None = None
    reasoning: _ProviderReasoning | None = None
    enable_thinking: bool | None = None

    @field_validator("messages", "tools", mode="before")
    @classmethod
    def _provider_collections_are_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list | tuple) else value

    @model_validator(mode="after")
    def _compatibility_fields_have_one_representation(self) -> _ProviderRequest:
        if (self.max_completion_tokens is None) == (self.max_tokens is None):
            raise ValueError("provider request requires exactly one max-token field")
        for field_name in ("store", "enable_thinking", "stream_options"):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"provider request {field_name} cannot be null")
        return self


def extract_provider_entries(request: HttpRequest, timeout: Timeout) -> AttestedEntries:
    """Validate Chat Completions and extract only the receipt-covered context."""
    _validate_json_request(request)
    value = _load_json(request.body, ProviderShapeError, timeout)
    try:
        provider = _PROVIDER_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise ProviderShapeError("provider request body is unsupported") from None
    entries: list[ContextEntryV1] = []
    for message in provider.messages:
        content = (
            "\n".join(block.text for block in message.content)
            if isinstance(message.content, tuple)
            else message.content
        )
        if message.role == "user" and content is not None:
            entries.append(UserContextEntryV1(role="user", text=content))
        elif message.role == "tool" and content is not None:
            # The role validator requires this ID for every tool message.
            assert message.tool_call_id is not None
            entries.append(
                ToolContextEntryV1(
                    role="tool",
                    tool_call_id=_provider_tool_call_id(message.tool_call_id),
                    text=content,
                )
            )
    if not entries:
        raise ProviderShapeError("provider request has no attested context entries")
    return tuple(entries)


def context_entries_subject(entries: AttestedEntries) -> tuple[str, int]:
    """Return the v2 hash and count for one ordered entry list."""
    body = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest(), len(entries)


def _validate_json_request(request: HttpRequest) -> None:
    if request.target.method.upper() != "POST":
        raise ProviderShapeError("provider request method is unsupported")
    content_types = [
        header.value.strip().lower()
        for header in request.headers
        if header.name.lower() == "content-type"
    ]
    if content_types != ["application/json"]:
        raise ProviderShapeError("provider request requires one JSON content type")
    if any(header.name.lower() == "content-encoding" for header in request.headers):
        raise ProviderShapeError("provider request content encoding is unsupported")


def _provider_tool_call_id(value: str) -> str:
    return value.split("|", 1)[0]


def _load_json(body: bytes, error_type: type[ValueError], timeout: Timeout) -> object:
    try:
        JsonDocument.parse(body, timeout=timeout)
    except (BodyFormatError, GateInputError):
        raise error_type("request body is not canonical JSON") from None
    try:
        text = body.decode("utf-8", errors="strict")
        return json.loads(text, parse_float=_finite_json_float)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise error_type("request body is not canonical JSON") from None


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


_PI_ORIGINS = {
    AdmissionHook.USER_MESSAGE: "user",
    AdmissionHook.SYSTEM_CONTEXT: "system",
    AdmissionHook.COMPACTION_SUMMARY: "compaction_summary",
}
_PI_SHAPES: dict[AdmissionHook, type[HarnessNative]] = {
    AdmissionHook.USER_MESSAGE: PiMessageV1,
    AdmissionHook.SYSTEM_CONTEXT: PiMessageV1,
    AdmissionHook.COMPACTION_SUMMARY: PiMessageV1,
    AdmissionHook.TOOL_RESULT: PiToolResultV1,
    AdmissionHook.ASSISTANT_MESSAGE: PiAssistantMessageV1,
    AdmissionHook.PROVIDER_CONTEXT: PiProviderContextV1,
}
_PROVIDER_ADAPTER = TypeAdapter(_ProviderRequest)


__all__ = [
    "AdmissionMutationError",
    "AdmissionShapeError",
    "AttestedEntries",
    "ContextEntryV1",
    "PiMessageV1",
    "PiAssistantMessageV1",
    "PiAssistantToolCallV1",
    "PiTextContentV1",
    "PiToolResultV1",
    "PiProviderContextV1",
    "ProviderShapeError",
    "ToolContextEntryV1",
    "UserContextEntryV1",
    "parse_pi_request",
    "validate_pi_replacement",
    "extract_provider_entries",
    "context_entries_subject",
]
