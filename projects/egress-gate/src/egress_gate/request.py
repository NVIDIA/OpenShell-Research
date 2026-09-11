# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Immutable request and mutation models shared by Egress Gate components."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_HEADER_MUTATION_DATA_BYTES,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
)
from egress_gate.string_validators import ScalarString

HeaderName = Annotated[ScalarString, Field(min_length=1)]
HeaderValue = ScalarString


class Process(StrictDomainModel):
    """The originating workload process and its executable ancestry."""

    binary: ScalarString
    pid: int = Field(ge=0, le=2**32 - 1)
    ancestors: tuple[ScalarString, ...] = ()


class RequestContext(StrictDomainModel):
    """Sandbox and request identity supplied by OpenShell."""

    request_id: ScalarString
    sandbox_id: ScalarString
    originating_process: Process | None = None

    @model_validator(mode="after")
    def _context_strings_are_bounded(self) -> RequestContext:
        string_bytes = len(self.request_id.encode("utf-8")) + len(
            self.sandbox_id.encode("utf-8")
        )
        if self.originating_process is not None:
            string_bytes += len(self.originating_process.binary.encode("utf-8"))
            string_bytes += sum(
                len(ancestor.encode("utf-8"))
                for ancestor in self.originating_process.ancestors
            )
        if string_bytes > MAX_PROTO_CONTEXT_BYTES:
            raise ValueError("request context strings exceed the size limit")
        return self


class HttpTarget(StrictDomainModel):
    """The bounded destination and request target visible before credentials."""

    scheme: ScalarString
    host: ScalarString
    port: int = Field(ge=0, le=2**32 - 1)
    method: ScalarString
    path: ScalarString
    query: ScalarString

    @model_validator(mode="after")
    def _target_strings_are_bounded(self) -> HttpTarget:
        string_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                self.scheme,
                self.host,
                self.method,
                self.path,
                self.query,
            )
        )
        if string_bytes > MAX_PROTO_TARGET_BYTES:
            raise ValueError("request target strings exceed the size limit")
        return self


class HttpHeader(StrictDomainModel):
    """One ordered, repeated request-header field."""

    name: HeaderName
    value: HeaderValue


class HttpRequest(StrictDomainModel):
    """The immutable OpenShell HTTP request exposed to a gate."""

    context: RequestContext
    target: HttpTarget
    headers: tuple[HttpHeader, ...] = Field(max_length=MAX_PROTO_HEADERS)
    body: bytes = Field(max_length=MAX_BODY_BYTES, repr=False)

    @field_validator("headers")
    @classmethod
    def _headers_are_bounded(
        cls, value: tuple[HttpHeader, ...]
    ) -> tuple[HttpHeader, ...]:
        encoded_size = sum(
            len(header.name.encode("utf-8")) + len(header.value.encode("utf-8"))
            for header in value
        )
        if encoded_size > MAX_PROTO_HEADERS_BYTES:
            raise ValueError("request headers exceed the size limit")
        return value


class ExistingHeaderAction(StrEnum):
    """How a header write handles existing case-insensitive fields."""

    APPEND = "append"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class WriteHeaderMutation(StrictDomainModel):
    """One ordered write operation proposed by a gate."""

    kind: Literal["write"]
    name: HeaderName
    value: HeaderValue
    on_existing: ExistingHeaderAction


class RemoveHeaderMutation(StrictDomainModel):
    """One ordered removal operation proposed by a gate."""

    kind: Literal["remove"]
    name: HeaderName


HeaderMutation: TypeAlias = Annotated[
    WriteHeaderMutation | RemoveHeaderMutation,
    Field(discriminator="kind"),
]


class RequestMutations(StrictDomainModel):
    """Validated body and header mutations proposed by one gate."""

    replacement_body: bytes | None = Field(
        default=None,
        max_length=MAX_BODY_BYTES,
        repr=False,
    )
    header_mutations: tuple[HeaderMutation, ...] = Field(
        default=(),
        max_length=MAX_HEADER_MUTATIONS,
    )

    @model_validator(mode="after")
    def _mutations_are_bounded(self) -> RequestMutations:
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
            raise ValueError("request mutation header data exceeds the size limit")
        return self

    @property
    def is_empty(self) -> bool:
        """Whether this set proposes no request mutation."""
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
    "RequestMutations",
    "WriteHeaderMutation",
]
