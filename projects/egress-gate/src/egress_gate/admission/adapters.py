"""Registered Pi and provider request-shape adapters."""

from __future__ import annotations

import json
from typing import Literal, Protocol

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


class PiInputV1(StrictDomainModel):
    """Rendered text submitted by the pinned Pi extension."""

    schema_version: Literal["openshell.pi-input.v1"]
    text: ScalarString


class PreparedHarnessRequest:
    """Parsed Pi request plus its canonical Gate projection."""

    def __init__(
        self,
        *,
        native: PiInputV1,
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
    ) -> tuple[bytes | None, PiInputV1]: ...


class PiV1Adapter:
    """Strict rendered-prompt adapter."""

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
    ) -> tuple[bytes | None, PiInputV1]:
        updated = _parse_pi_body(projected_body, timeout)
        encoded = canonical_json_bytes(updated)
        replacement = (
            None
            if canonical_json_bytes(updated) == canonical_json_bytes(prepared.native)
            else encoded
        )
        return replacement, updated


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
        return self


class _ProviderFunctionDefinition(StrictDomainModel):
    name: ScalarString
    description: ScalarString
    parameters: dict[str, object]
    strict: bool


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
    max_completion_tokens: int = Field(ge=1)
    stream: Literal[True]
    stream_options: _ProviderStreamOptions
    store: Literal[False]
    prompt_cache_key: ScalarString | None = None
    prompt_cache_retention: Literal["24h"] | None = None
    reasoning_effort: ScalarString | None = None

    @field_validator("messages", "tools", mode="before")
    @classmethod
    def _provider_collections_are_tuples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list | tuple) else value


class ProviderRequestAdapter(Protocol):
    """Validate and project a provider request for rendered-prompt extraction."""

    schema_version: str

    def canonicalize(
        self, request: HttpRequest, timeout: Timeout
    ) -> ModelRequestV1: ...

    def rendered_prompt(self, request: HttpRequest, timeout: Timeout) -> PiInputV1: ...


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
                max_tokens=provider.max_completion_tokens,
            ),
        )

    def rendered_prompt(self, request: HttpRequest, timeout: Timeout) -> PiInputV1:
        """Extract the last user text from the first provider request."""
        canonical = self.canonicalize(request, timeout)
        for message in reversed(canonical.messages):
            if message.role is CanonicalRole.USER and message.content is not None:
                return PiInputV1(
                    schema_version="openshell.pi-input.v1",
                    text=message.content,
                )
        raise ProviderShapeError("provider request has no user prompt")


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


def create_pi_adapter_registry() -> HarnessAdapterRegistry:
    """Return the built-in Pi v1 admission registry."""
    registry = HarnessAdapterRegistry()
    registry.register(
        "pi",
        AdmissionHook.RENDERED_PROMPT,
        "openshell.pi-input.v1",
        PiV1Adapter(),
    )
    return registry


def create_provider_adapter_registry() -> ProviderAdapterRegistry:
    """Return the milestone-one provider registry."""
    registry = ProviderAdapterRegistry()
    registry.register(OpenAIChatCompletionsV1Adapter())
    return registry


def _parse_pi_body(body: bytes, timeout: Timeout) -> PiInputV1:
    value = _load_json(body, AdmissionShapeError, timeout)
    try:
        parsed = _PI_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise AdmissionShapeError("Pi request body is unsupported") from None
    if not isinstance(parsed, PiInputV1):
        raise AdmissionShapeError("Pi request body is unsupported")
    if canonical_json_bytes(parsed) != body:
        raise AdmissionShapeError("Pi request body is not canonical JSON")
    return parsed


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
        tool_call_id=item.tool_call_id,
        tool_calls=tuple(
            CanonicalFunctionCallV1(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in item.tool_calls
        ),
    )


_PI_ADAPTER = TypeAdapter(PiInputV1)
_PROVIDER_ADAPTER = TypeAdapter(_ProviderRequest)


__all__ = [
    "AdmissionMutationError",
    "AdmissionShapeError",
    "HarnessAdapter",
    "HarnessAdapterRegistry",
    "OpenAIChatCompletionsV1Adapter",
    "PiInputV1",
    "PiV1Adapter",
    "PreparedHarnessRequest",
    "ProviderAdapterRegistry",
    "ProviderRequestAdapter",
    "ProviderShapeError",
    "create_pi_adapter_registry",
    "create_provider_adapter_registry",
]
