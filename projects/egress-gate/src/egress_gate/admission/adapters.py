# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Registered Pi and provider request-shape adapters."""

from __future__ import annotations

import json
from typing import Literal, Protocol, TypeAlias

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from egress_gate.admission.canonical import (
    CanonicalFunctionCallV1,
    CanonicalGenerationV1,
    CanonicalMessageV1,
    CanonicalRole,
    CanonicalToolChoiceV1,
    CanonicalToolV1,
    ModelRequestV1,
    canonical_json_bytes,
)
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


class PiMessageV1(StrictDomainModel):
    """Rendered text submitted by the managed Pi harness."""

    schema_version: Literal["openshell.pi-message.v1"]
    origin: Literal["user"]
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


AttestedCandidate: TypeAlias = PiMessageV1 | CanonicalMessageV1


class PreparedHarnessRequest:
    """Parsed Pi request plus its canonical Gate projection."""

    def __init__(
        self,
        *,
        native: PiMessageV1 | PiToolResultV1,
        projected_body: bytes,
        original_body: bytes,
    ) -> None:
        self.native = native
        self.projected_body = projected_body
        self.original_body = original_body


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
    ) -> tuple[bytes | None, AttestedCandidate]: ...


class PiMessageV1Adapter:
    """Strict user-message adapter."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_body(request.request_body, timeout)
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
            original_body=request.request_body,
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, PiMessageV1]:
        updated = _parse_pi_body(projected_body, timeout)
        encoded = canonical_json_bytes(updated)
        replacement = (
            None
            if canonical_json_bytes(updated) == canonical_json_bytes(prepared.native)
            else encoded
        )
        return replacement, updated


class PiToolResultV1Adapter:
    """Strict adapter for Pi tool-result content blocks."""

    def prepare(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> PreparedHarnessRequest:
        native = _parse_pi_tool_result(request.request_body, timeout)
        _tool_result_attested_candidate(native)
        return PreparedHarnessRequest(
            native=native,
            projected_body=canonical_json_bytes(native),
            original_body=request.request_body,
        )

    def validate_result(
        self,
        prepared: PreparedHarnessRequest,
        projected_body: bytes,
        context: HarnessAdmissionContext,
        timeout: Timeout,
    ) -> tuple[bytes | None, AttestedCandidate]:
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
        return replacement, _tool_result_attested_candidate(updated)


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

    @property
    def bindings(self) -> tuple[tuple[str, str, str], ...]:
        """Return registered harness, hook, and schema bindings."""
        return tuple(self._adapters)


class _ProviderTextBlock(StrictDomainModel):
    type: Literal["text"]
    text: ScalarString


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
    max_completion_tokens: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    stream: Literal[True]
    stream_options: _ProviderStreamOptions
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
        for field_name in ("store", "enable_thinking"):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"provider request {field_name} cannot be null")
        return self

    @property
    def output_token_limit(self) -> int:
        value = self.max_completion_tokens or self.max_tokens
        if value is None:
            raise ValueError("provider request has no max-token field")
        return value


class _ResponsesInputText(StrictDomainModel):
    type: Literal["input_text"]
    text: ScalarString


class _ResponsesOutputText(StrictDomainModel):
    type: Literal["output_text"]
    text: ScalarString
    annotations: tuple[object, ...]

    @field_validator("annotations", mode="before")
    @classmethod
    def _annotations_are_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class _ResponsesInputMessage(StrictDomainModel):
    role: Literal["system", "developer", "user"]
    content: ScalarString | tuple[_ResponsesInputText, ...]
    type: Literal["message"] | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _content_is_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _optional_type_is_not_null(self) -> _ResponsesInputMessage:
        if "type" in self.model_fields_set and self.type is None:
            raise ValueError("Responses input message type cannot be null")
        return self


class _ResponsesAssistantMessage(StrictDomainModel):
    type: Literal["message"]
    role: Literal["assistant"]
    content: tuple[_ResponsesOutputText, ...]
    status: Literal["completed"]
    id: ScalarString
    phase: Literal["commentary", "final_answer"] | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _content_is_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class _ResponsesFunctionCall(StrictDomainModel):
    type: Literal["function_call"]
    call_id: ScalarString
    name: ScalarString
    arguments: ScalarString
    id: ScalarString | None = None
    namespace: ScalarString | None = None


class _ResponsesFunctionCallOutput(StrictDomainModel):
    type: Literal["function_call_output"]
    call_id: ScalarString
    output: ScalarString | tuple[_ResponsesInputText, ...]

    @field_validator("output", mode="before")
    @classmethod
    def _output_is_a_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class _ResponsesReasoningSummary(StrictDomainModel):
    type: Literal["summary_text"]
    text: ScalarString


class _ResponsesReasoningContent(StrictDomainModel):
    type: Literal["reasoning_text"]
    text: ScalarString


class _ResponsesReasoning(StrictDomainModel):
    type: Literal["reasoning"]
    id: ScalarString
    summary: tuple[_ResponsesReasoningSummary, ...]
    content: tuple[_ResponsesReasoningContent, ...] | None = None
    encrypted_content: ScalarString | None = None
    status: Literal["in_progress", "completed", "incomplete"] | None = None

    @field_validator("summary", "content", mode="before")
    @classmethod
    def _sequences_are_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


_ResponsesInputItem: TypeAlias = (
    _ResponsesInputMessage
    | _ResponsesAssistantMessage
    | _ResponsesFunctionCall
    | _ResponsesFunctionCallOutput
    | _ResponsesReasoning
)


class _ResponsesTool(StrictDomainModel):
    type: Literal["function"]
    name: ScalarString
    description: ScalarString
    parameters: dict[str, object]
    strict: bool | None = None


class _ResponsesNamedToolChoice(StrictDomainModel):
    type: Literal["function"]
    name: ScalarString


class _ResponsesReasoningOptions(StrictDomainModel):
    effort: ScalarString
    summary: Literal["auto", "detailed", "concise"] | None = None


class _ResponsesPromptCacheOptions(StrictDomainModel):
    mode: Literal["explicit"]


class _ResponsesRequest(StrictDomainModel):
    model: ScalarString
    input: tuple[_ResponsesInputItem, ...]
    stream: Literal[True]
    store: Literal[False]
    max_output_tokens: int = Field(ge=1)
    tools: tuple[_ResponsesTool, ...] = ()
    tool_choice: Literal["auto", "none", "required"] | _ResponsesNamedToolChoice = (
        "auto"
    )
    temperature: int | float | None = Field(default=None, allow_inf_nan=False)
    prompt_cache_key: ScalarString | None = None
    prompt_cache_retention: Literal["24h"] | None = None
    prompt_cache_options: _ResponsesPromptCacheOptions | None = None
    reasoning: _ResponsesReasoningOptions | None = None
    include: tuple[Literal["reasoning.encrypted_content"], ...] = ()
    service_tier: Literal["auto", "default", "flex", "scale", "priority"] | None = None

    @field_validator("input", "tools", "include", mode="before")
    @classmethod
    def _collections_are_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list | tuple) else value


class ProviderRequestAdapter(Protocol):
    """Validate and project a provider request for rendered-prompt extraction."""

    schema_version: str

    def canonicalize(
        self, request: HttpRequest, timeout: Timeout
    ) -> ModelRequestV1: ...

    def latest_attested_candidate(
        self, request: HttpRequest, timeout: Timeout
    ) -> AttestedCandidate: ...


class OpenAIChatCompletionsV1Adapter:
    """Pinned OpenAI-compatible Chat Completions request adapter."""

    schema_version = "openai.chat-completions.v1"

    def canonicalize(self, request: HttpRequest, timeout: Timeout) -> ModelRequestV1:
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
        value = _load_json(request.body, ProviderShapeError, timeout)
        try:
            provider = _PROVIDER_ADAPTER.validate_python(value, strict=True)
        except ValidationError:
            raise ProviderShapeError("provider request body is unsupported") from None
        if not isinstance(provider, _ProviderRequest):
            raise ProviderShapeError("provider request body is unsupported")
        messages = tuple(
            _provider_message_to_canonical(item) for item in provider.messages
        )
        tools = tuple(
            CanonicalToolV1(
                name=item.function.name,
                description=item.function.description,
                input_schema=item.function.parameters,
            )
            for item in provider.tools
        )
        if isinstance(provider.tool_choice, str):
            tool_choice = CanonicalToolChoiceV1(mode=provider.tool_choice)
        else:
            tool_choice = CanonicalToolChoiceV1(
                mode="function",
                function_name=provider.tool_choice.function.name,
            )
        return ModelRequestV1(
            model=provider.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            generation=CanonicalGenerationV1(
                temperature=provider.temperature,
                max_tokens=provider.output_token_limit,
            ),
        )

    def latest_attested_candidate(
        self, request: HttpRequest, timeout: Timeout
    ) -> AttestedCandidate:
        """Extract the latest user or tool context addition."""
        canonical = self.canonicalize(request, timeout)
        for message in reversed(canonical.messages):
            if message.role is CanonicalRole.USER and message.content is not None:
                return PiMessageV1(
                    schema_version="openshell.pi-message.v1",
                    origin="user",
                    text=message.content,
                )
            if message.role is CanonicalRole.TOOL and message.content is not None:
                if message.tool_call_id is None:
                    raise ProviderShapeError("provider tool result has no call ID")
                return message
        raise ProviderShapeError("provider request has no attested context addition")


class OpenAIResponsesV1Adapter:
    """Pinned OpenAI-compatible Responses request adapter."""

    schema_version = "openai.responses.v1"

    def canonicalize(self, request: HttpRequest, timeout: Timeout) -> ModelRequestV1:
        provider = self._parse(request, timeout)
        messages: list[CanonicalMessageV1] = []
        for item in provider.input:
            if isinstance(item, _ResponsesInputMessage):
                messages.append(
                    CanonicalMessageV1(
                        role=CanonicalRole(item.role),
                        content=_responses_text(item.content),
                    )
                )
            elif isinstance(item, _ResponsesAssistantMessage):
                messages.append(
                    CanonicalMessageV1(
                        role=CanonicalRole.ASSISTANT,
                        content="\n".join(block.text for block in item.content),
                    )
                )
            elif isinstance(item, _ResponsesFunctionCall):
                messages.append(
                    CanonicalMessageV1(
                        role=CanonicalRole.ASSISTANT,
                        content=None,
                        tool_calls=(
                            CanonicalFunctionCallV1(
                                id=item.call_id,
                                name=item.name,
                                arguments=item.arguments,
                            ),
                        ),
                    )
                )
            elif isinstance(item, _ResponsesFunctionCallOutput):
                messages.append(_responses_tool_result(item))
        tools = tuple(
            CanonicalToolV1(
                name=item.name,
                description=item.description,
                input_schema=item.parameters,
            )
            for item in provider.tools
        )
        if isinstance(provider.tool_choice, str):
            tool_choice = CanonicalToolChoiceV1(mode=provider.tool_choice)
        else:
            tool_choice = CanonicalToolChoiceV1(
                mode="function", function_name=provider.tool_choice.name
            )
        return ModelRequestV1(
            model=provider.model,
            messages=tuple(messages),
            tools=tools,
            tool_choice=tool_choice,
            generation=CanonicalGenerationV1(
                temperature=provider.temperature,
                max_tokens=provider.max_output_tokens,
            ),
        )

    def latest_attested_candidate(
        self, request: HttpRequest, timeout: Timeout
    ) -> AttestedCandidate:
        """Extract the latest user or function-call output context addition."""
        provider = self._parse(request, timeout)
        for item in reversed(provider.input):
            if isinstance(item, _ResponsesInputMessage) and item.role == "user":
                return PiMessageV1(
                    schema_version="openshell.pi-message.v1",
                    origin="user",
                    text=_responses_text(item.content),
                )
            if isinstance(item, _ResponsesFunctionCallOutput):
                return _responses_tool_result(item)
        raise ProviderShapeError("provider request has no attested context addition")

    def _parse(self, request: HttpRequest, timeout: Timeout) -> _ResponsesRequest:
        _validate_json_request(request)
        value = _load_json(request.body, ProviderShapeError, timeout)
        try:
            provider = _RESPONSES_PROVIDER_ADAPTER.validate_python(value, strict=True)
        except ValidationError:
            raise ProviderShapeError("provider request body is unsupported") from None
        if not isinstance(provider, _ResponsesRequest):
            raise ProviderShapeError("provider request body is unsupported")
        return provider


class ProviderAdapterRegistry:
    """Explicit versioned provider-adapter registry."""

    def __init__(self) -> None:
        self._adapters: dict[str, ProviderRequestAdapter] = {}

    def register(self, adapter: ProviderRequestAdapter) -> None:
        if adapter.schema_version in self._adapters:
            raise ValueError("provider adapter is already registered")
        self._adapters[adapter.schema_version] = adapter

    def resolve(self, schema_version: str) -> ProviderRequestAdapter:
        try:
            return self._adapters[schema_version]
        except KeyError:
            raise ProviderShapeError("provider adapter is unsupported") from None

    def resolve_request(
        self, request: HttpRequest, timeout: Timeout
    ) -> ProviderRequestAdapter:
        """Select the adapter from the mutually exclusive top-level request shape."""
        value = _load_json(request.body, ProviderShapeError, timeout)
        if not isinstance(value, dict):
            raise ProviderShapeError("provider request body is unsupported")
        if "messages" in value and "input" not in value:
            return self.resolve(OpenAIChatCompletionsV1Adapter.schema_version)
        if "input" in value and "messages" not in value:
            return self.resolve(OpenAIResponsesV1Adapter.schema_version)
        raise ProviderShapeError("provider request body is unsupported")


def create_pi_adapter_registry() -> HarnessAdapterRegistry:
    """Return the built-in Pi v1 admission registry."""
    registry = HarnessAdapterRegistry()
    registry.register(
        "pi",
        AdmissionHook.USER_MESSAGE,
        "openshell.pi-message.v1",
        PiMessageV1Adapter(),
    )
    registry.register(
        "pi",
        AdmissionHook.TOOL_RESULT,
        "openshell.pi-tool-result.v1",
        PiToolResultV1Adapter(),
    )
    return registry


def create_provider_adapter_registry() -> ProviderAdapterRegistry:
    """Return the built-in OpenAI provider-request registry."""
    registry = ProviderAdapterRegistry()
    registry.register(OpenAIChatCompletionsV1Adapter())
    registry.register(OpenAIResponsesV1Adapter())
    return registry


def _parse_pi_body(body: bytes, timeout: Timeout) -> PiMessageV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi request body is unsupported") from None
    if not isinstance(parsed, PiMessageV1):
        raise AdmissionShapeError("Pi request body is unsupported")
    if canonical_json_bytes(parsed) != body:
        raise AdmissionShapeError("Pi request body is not canonical JSON")
    return parsed


def _parse_pi_tool_result(body: bytes, timeout: Timeout) -> PiToolResultV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_TOOL_RESULT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi tool-result body is unsupported") from None
    if not isinstance(parsed, PiToolResultV1):
        raise AdmissionShapeError("Pi tool-result body is unsupported")
    return parsed


def _tool_result_attested_candidate(
    result: PiToolResultV1,
) -> CanonicalMessageV1:
    if any(block.type == "image" for block in result.content):
        raise AdmissionShapeError("Pi tool-result images are unsupported")
    text = "\n".join(
        block.text for block in result.content if isinstance(block, PiTextContentV1)
    )
    return CanonicalMessageV1(
        role=CanonicalRole.TOOL,
        content=text or "(no tool output)",
        tool_call_id=_provider_tool_call_id(result.tool_call_id),
    )


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


def _responses_text(value: ScalarString | tuple[_ResponsesInputText, ...]) -> str:
    if isinstance(value, str):
        return value
    if not value:
        raise ProviderShapeError("provider message content cannot be empty")
    return "\n".join(block.text for block in value)


def _responses_tool_result(
    item: _ResponsesFunctionCallOutput,
) -> CanonicalMessageV1:
    return CanonicalMessageV1(
        role=CanonicalRole.TOOL,
        content=_responses_text(item.output),
        tool_call_id=_provider_tool_call_id(item.call_id),
    )


def _provider_tool_call_id(value: str) -> str:
    return value.split("|", 1)[0]


def _load_json(body: bytes, error_type: type[ValueError], timeout: Timeout) -> object:
    try:
        JsonDocument.parse(body, timeout=timeout)
    except (BodyFormatError, GateInputError):
        raise error_type("request body is not canonical JSON") from None
    try:
        text = body.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise error_type("request body is not canonical JSON") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON object key")
        output[key] = value
    return output


def _provider_message_to_canonical(item: _ProviderMessage) -> CanonicalMessageV1:
    if isinstance(item.content, tuple):
        if len(item.content) != 1:
            raise ProviderShapeError("multipart text requires exactly one block")
        content = item.content[0].text
    else:
        content = item.content
    return CanonicalMessageV1(
        role=CanonicalRole(item.role),
        content=content,
        name=item.name,
        tool_call_id=(
            _provider_tool_call_id(item.tool_call_id)
            if item.tool_call_id is not None
            else None
        ),
        tool_calls=tuple(
            CanonicalFunctionCallV1(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in item.tool_calls
        ),
    )


_PI_ADAPTER = TypeAdapter(PiMessageV1)
_PI_TOOL_RESULT_ADAPTER = TypeAdapter(PiToolResultV1)
_PROVIDER_ADAPTER = TypeAdapter(_ProviderRequest)
_RESPONSES_PROVIDER_ADAPTER = TypeAdapter(_ResponsesRequest)


__all__ = [
    "AdmissionMutationError",
    "AdmissionShapeError",
    "AttestedCandidate",
    "HarnessAdapter",
    "HarnessAdapterRegistry",
    "OpenAIChatCompletionsV1Adapter",
    "OpenAIResponsesV1Adapter",
    "PiMessageV1",
    "PiImageContentV1",
    "PiTextContentV1",
    "PiToolResultV1",
    "PiToolResultV1Adapter",
    "PiMessageV1Adapter",
    "PreparedHarnessRequest",
    "ProviderAdapterRegistry",
    "ProviderRequestAdapter",
    "ProviderShapeError",
    "create_pi_adapter_registry",
    "create_provider_adapter_registry",
]
