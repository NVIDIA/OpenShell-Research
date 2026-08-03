"""Immutable request and mutation models shared by Egress Gate components."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BeforeValidator, Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_HEADER_MUTATION_DATA_BYTES,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
)
from egress_gate.string_validators import ScalarString, validate_scalar_string


def _require_tuple(value: object, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return value


def _validate_non_empty_header_name(value: object) -> str:
    name = validate_scalar_string(value)
    if not name:
        raise ValueError("header name must not be empty")
    return name


HeaderName = Annotated[str, BeforeValidator(_validate_non_empty_header_name)]
HeaderValue = ScalarString


class Process(StrictDomainModel):
    """The originating workload process and its executable ancestry."""

    binary: ScalarString
    pid: int = Field(ge=0, le=2**32 - 1)
    ancestors: tuple[ScalarString, ...] = ()

    @field_validator("ancestors", mode="before")
    @classmethod
    def _ancestors_are_a_tuple(cls, value: object) -> object:
        return _require_tuple(value, "ancestors")


class RequestContext(StrictDomainModel):
    """Sandbox and request identity supplied by OpenShell."""

    request_id: ScalarString
    sandbox_id: ScalarString
    originating_process: Process | None = None


class HttpTarget(StrictDomainModel):
    """The bounded destination and request target visible before credentials."""

    scheme: ScalarString
    host: ScalarString
    port: int = Field(ge=0, le=2**32 - 1)
    method: ScalarString
    path: ScalarString
    query: ScalarString


class HttpHeader(StrictDomainModel):
    """One ordered, repeated request-header field."""

    name: HeaderName
    value: HeaderValue


class HttpRequest(StrictDomainModel):
    """The immutable OpenShell HTTP request exposed to a gate."""

    context: RequestContext
    target: HttpTarget
    headers: tuple[HttpHeader, ...]
    body: bytes = Field(repr=False)

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_are_a_tuple(cls, value: object) -> object:
        return _require_tuple(value, "headers")

    @field_validator("headers")
    @classmethod
    def _headers_are_bounded(
        cls, value: tuple[HttpHeader, ...]
    ) -> tuple[HttpHeader, ...]:
        if len(value) > MAX_PROTO_HEADERS:
            raise ValueError("request has too many headers")
        encoded_size = sum(
            len(header.name.encode("utf-8")) + len(header.value.encode("utf-8"))
            for header in value
        )
        if encoded_size > MAX_PROTO_HEADERS_BYTES:
            raise ValueError("request headers exceed the size limit")
        return value

    @field_validator("body")
    @classmethod
    def _body_is_bounded(cls, value: bytes) -> bytes:
        if len(value) > MAX_BODY_BYTES:
            raise ValueError("request body exceeds the size limit")
        return value


class ExistingHeaderAction(StrEnum):
    """How a header write handles existing case-insensitive fields."""

    APPEND = "append"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class WriteHeaderMutation(StrictDomainModel):
    """One ordered write operation proposed by a gate."""

    operation: Literal["write"] = "write"
    name: HeaderName
    value: HeaderValue
    on_existing: ExistingHeaderAction


class RemoveHeaderMutation(StrictDomainModel):
    """One ordered removal operation proposed by a gate."""

    operation: Literal["remove"] = "remove"
    name: HeaderName


HeaderMutation: TypeAlias = Annotated[
    WriteHeaderMutation | RemoveHeaderMutation,
    Field(discriminator="operation"),
]


class RequestPatch(StrictDomainModel):
    """Validated body and header mutations proposed by one gate."""

    replacement_body: bytes | None = Field(default=None, repr=False)
    header_mutations: tuple[HeaderMutation, ...] = ()

    @field_validator("header_mutations", mode="before")
    @classmethod
    def _mutations_are_a_tuple(cls, value: object) -> object:
        return _require_tuple(value, "header_mutations")

    @field_validator("replacement_body")
    @classmethod
    def _replacement_body_is_bounded(cls, value: bytes | None) -> bytes | None:
        if value is not None and len(value) > MAX_BODY_BYTES:
            raise ValueError("replacement body exceeds the size limit")
        return value

    @model_validator(mode="after")
    def _mutations_are_bounded(self) -> RequestPatch:
        if len(self.header_mutations) > MAX_HEADER_MUTATIONS:
            raise ValueError("request patch has too many header mutations")
        data_size = sum(
            len(mutation.name.encode("utf-8"))
            + (
                len(mutation.value.encode("utf-8"))
                if isinstance(mutation, WriteHeaderMutation)
                else 0
            )
            for mutation in self.header_mutations
        )
        if data_size > MAX_HEADER_MUTATION_DATA_BYTES:
            raise ValueError("request patch header data exceeds the size limit")
        return self

    @property
    def is_empty(self) -> bool:
        """Whether this patch proposes no mutation."""
        return self.replacement_body is None and not self.header_mutations


__all__ = [
    "ExistingHeaderAction",
    "HeaderMutation",
    "HeaderName",
    "HeaderValue",
    "HttpHeader",
    "HttpRequest",
    "HttpTarget",
    "Process",
    "RemoveHeaderMutation",
    "RequestContext",
    "RequestPatch",
    "WriteHeaderMutation",
]
