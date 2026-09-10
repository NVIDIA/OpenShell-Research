# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Registered Pi and provider request-shape adapters."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, Protocol, TypeAlias

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
    HarnessAdmissionRequest,
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


class PiImageContentV1(StrictDomainModel):
    """One Pi image content block."""

    type: Literal["image"]
    data: ScalarString
    mimeType: ScalarString


class PiToolResultV1(StrictDomainModel):
    """Provider-relevant fields from one Pi tool-result message."""

    schema_version: Literal["openshell.pi-tool-result.v1"]
    tool_call_id: ScalarString
    tool_name: ScalarString
    content: tuple[PiTextContentV1 | PiImageContentV1, ...]
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


class PiAssistantMessageV1(StrictDomainModel):
    """Replaceable assistant text and immutable tool calls."""

    schema_version: Literal["openshell.pi-assistant-message.v1"]
    text: ScalarString
    tool_calls: tuple[PiAssistantToolCallV1, ...]

    @field_validator("tool_calls", mode="before")
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


class PreparedHarnessRequest:
    """Parsed Pi request plus its canonical Gate projection."""

    def __init__(
        self,
        *,
        native: HarnessNative,
        projected_body: bytes,
    ) -> None:
        self.native = native
        self.projected_body = projected_body


class HarnessAdapter(Protocol):
    """Fixed-authority translation for one registered harness hook."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest: ...

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, HarnessNative]: ...

    def attestation_subject(
        self,
        prepared: PreparedHarnessRequest,
        final: HarnessNative,
    ) -> tuple[str, int] | None: ...


class _AppendHarnessAdapter:
    def attestation_subject(
        self,
        prepared: PreparedHarnessRequest,
        final: HarnessNative,
    ) -> None:
        return None


class PiMessageV1Adapter(_AppendHarnessAdapter):
    """Strict adapter for one text-bearing Pi origin."""

    def __init__(self, accepted_origin: PiMessageOrigin) -> None:
        self._accepted_origin = accepted_origin

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_body(
            request.request_body, timeout, accepted_origin=self._accepted_origin
        )
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, PiMessageV1]:
        updated = _parse_pi_body(
            projected_body, timeout, accepted_origin=self._accepted_origin
        )
        encoded = canonical_json_bytes(updated)
        replacement = (
            None
            if canonical_json_bytes(updated) == canonical_json_bytes(prepared.native)
            else encoded
        )
        return replacement, updated


class PiAssistantMessageV1Adapter(_AppendHarnessAdapter):
    """Strict adapter for Pi assistant text and tool calls."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_assistant_message(request.request_body, timeout)
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, PiAssistantMessageV1]:
        updated = _parse_pi_assistant_message(projected_body, timeout)
        if not isinstance(prepared.native, PiAssistantMessageV1):
            raise AdmissionMutationError("assistant admission state is invalid")
        if updated.tool_calls != prepared.native.tool_calls:
            raise AdmissionMutationError("admission changed assistant tool calls")
        encoded = canonical_json_bytes(updated)
        replacement = (
            None if encoded == canonical_json_bytes(prepared.native) else encoded
        )
        return replacement, updated


class PiToolResultV1Adapter(_AppendHarnessAdapter):
    """Strict adapter for Pi tool-result content blocks."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_tool_result(request.request_body, timeout)
        if any(block.type == "image" for block in native.content):
            raise AdmissionShapeError("Pi tool-result images are unsupported")
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, PiToolResultV1]:
        updated = _parse_pi_tool_result(projected_body, timeout)
        if not isinstance(prepared.native, PiToolResultV1):
            raise AdmissionMutationError("tool-result admission state is invalid")
        immutable_before = (
            prepared.native.schema_version,
            prepared.native.tool_call_id,
            prepared.native.tool_name,
            prepared.native.is_error,
        )
        immutable_after = (
            updated.schema_version,
            updated.tool_call_id,
            updated.tool_name,
            updated.is_error,
        )
        if immutable_after != immutable_before:
            raise AdmissionMutationError("admission changed tool-result metadata")
        encoded = canonical_json_bytes(updated)
        replacement = (
            None if encoded == canonical_json_bytes(prepared.native) else encoded
        )
        return replacement, updated


class PiProviderContextV1Adapter:
    """Strict adapter for the complete ordered provider context."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_provider_context(request.request_body, timeout)
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, PiProviderContextV1]:
        updated = _parse_pi_provider_context(projected_body, timeout)
        if not isinstance(prepared.native, PiProviderContextV1):
            raise AdmissionMutationError("provider-context admission state is invalid")
        before = tuple(
            (entry.role, getattr(entry, "tool_call_id", None))
            for entry in prepared.native.entries
        )
        after = tuple(
            (entry.role, getattr(entry, "tool_call_id", None))
            for entry in updated.entries
        )
        if after != before:
            raise AdmissionMutationError("admission changed provider-context structure")
        encoded = canonical_json_bytes(updated)
        replacement = (
            None if encoded == canonical_json_bytes(prepared.native) else encoded
        )
        return replacement, updated

    def attestation_subject(
        self,
        prepared: PreparedHarnessRequest,
        final: HarnessNative,
    ) -> tuple[str, int]:
        if not isinstance(final, PiProviderContextV1):
            raise AdmissionMutationError("provider-context admission state is invalid")
        return context_entries_subject(final.entries)


class HarnessAdapterRegistry:
    """Small explicit registry for supported harness admission shapes."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str, str], HarnessAdapter] = {}

    def register(
        self,
        harness: str,
        hook: AdmissionHook,
        schema_version: str,
        adapter: HarnessAdapter,
    ) -> None:
        key = (harness, hook.value, schema_version)
        if key in self._adapters:
            raise ValueError("harness adapter is already registered")
        self._adapters[key] = adapter

    def resolve(self, context: HarnessAdmissionContext) -> HarnessAdapter:
        key = (context.harness, context.hook.value, context.schema_version)
        try:
            return self._adapters[key]
        except KeyError:
            raise AdmissionShapeError(
                "harness admission shape is unsupported"
            ) from None


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

    @field_validator("content", "tool_calls", mode="before")
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
        if self.content is None and not self.tool_calls:
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


def create_pi_adapter_registry() -> HarnessAdapterRegistry:
    """Return the built-in Pi v1 admission registry."""
    registry = HarnessAdapterRegistry()
    for hook, origin in (
        (AdmissionHook.USER_MESSAGE, "user"),
        (AdmissionHook.SYSTEM_CONTEXT, "system"),
        (AdmissionHook.COMPACTION_SUMMARY, "compaction_summary"),
    ):
        registry.register(
            "pi",
            hook,
            "openshell.pi-message.v1",
            PiMessageV1Adapter(origin),
        )
    registry.register(
        "pi",
        AdmissionHook.TOOL_RESULT,
        "openshell.pi-tool-result.v1",
        PiToolResultV1Adapter(),
    )
    registry.register(
        "pi",
        AdmissionHook.ASSISTANT_MESSAGE,
        "openshell.pi-assistant-message.v1",
        PiAssistantMessageV1Adapter(),
    )
    registry.register(
        "pi",
        AdmissionHook.PROVIDER_CONTEXT,
        "openshell.pi-provider-context.v1",
        PiProviderContextV1Adapter(),
    )
    return registry


def _parse_pi_body(
    body: bytes, timeout: Timeout, *, accepted_origin: PiMessageOrigin = "user"
) -> PiMessageV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi request body is unsupported") from None
    if parsed.origin != accepted_origin:
        raise AdmissionShapeError("Pi message origin is unsupported")
    if canonical_json_bytes(parsed) != body:
        raise AdmissionShapeError("Pi request body is not canonical JSON")
    return parsed


def _parse_pi_assistant_message(body: bytes, timeout: Timeout) -> PiAssistantMessageV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_ASSISTANT_MESSAGE_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi assistant-message body is unsupported") from None
    return parsed


def _parse_pi_tool_result(body: bytes, timeout: Timeout) -> PiToolResultV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_TOOL_RESULT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi tool-result body is unsupported") from None
    return parsed


def _parse_pi_provider_context(body: bytes, timeout: Timeout) -> PiProviderContextV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_PROVIDER_CONTEXT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi provider-context body is unsupported") from None
    if canonical_json_bytes(parsed) != body:
        raise AdmissionShapeError("Pi provider-context body is not canonical JSON")
    return parsed


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


_PI_ADAPTER = TypeAdapter(PiMessageV1)
_PI_TOOL_RESULT_ADAPTER = TypeAdapter(PiToolResultV1)
_PI_ASSISTANT_MESSAGE_ADAPTER = TypeAdapter(PiAssistantMessageV1)
_PI_PROVIDER_CONTEXT_ADAPTER = TypeAdapter(PiProviderContextV1)
_PROVIDER_ADAPTER = TypeAdapter(_ProviderRequest)


__all__ = [
    "AdmissionMutationError",
    "AdmissionShapeError",
    "AttestedEntries",
    "ContextEntryV1",
    "HarnessAdapter",
    "HarnessAdapterRegistry",
    "PiMessageV1",
    "PiImageContentV1",
    "PiAssistantMessageV1",
    "PiAssistantMessageV1Adapter",
    "PiAssistantToolCallV1",
    "PiTextContentV1",
    "PiToolResultV1",
    "PiToolResultV1Adapter",
    "PiMessageV1Adapter",
    "PiProviderContextV1",
    "PiProviderContextV1Adapter",
    "PreparedHarnessRequest",
    "ProviderShapeError",
    "ToolContextEntryV1",
    "UserContextEntryV1",
    "extract_provider_entries",
    "context_entries_subject",
    "create_pi_adapter_registry",
]
