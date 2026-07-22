"""Strict request-body models and the nominal format-handler contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from pydantic import Field

from privacy_guard.config import PolicyConfig
from privacy_guard.validation import (
    ScalarString,
    StrictSensitiveModel,
    parse_non_empty_scalar_string,
)


class TextBlock(StrictSensitiveModel):
    """One scannable text block, addressed structurally within the body.

    ``path`` is an opaque, format-owned address that may contain sensitive
    structural keys. Callers may use it as a replacement key but must not parse
    it. ``replaceable=False`` exposes observable text, such as a JSON object key,
    that may be scanned but must not be rewritten.
    """

    path: ScalarString = Field(repr=False)
    text: ScalarString = Field(repr=False)
    replaceable: bool = True


class RequestBody(StrictSensitiveModel):
    """A normalized body plus handler-owned reconstruction state.

    ``text_blocks`` are the independently scannable values selected by the
    handler. ``parsed_value`` is opaque handler-owned state and reconstruction
    must not mutate it. ``original_bytes`` contains bytes equal to the exact input;
    a handler returns the stored value for a no-op reconstruction. Rewritten bodies
    need only preserve untouched values semantically.
    """

    text_blocks: tuple[TextBlock, ...]
    parsed_value: object = Field(repr=False)
    original_bytes: bytes = Field(repr=False)


class FormatHandlerContractError(Exception):
    """A content-safe format-handler metadata or output contract failure."""


class FormatHandler(ABC):
    """Nominal, concurrency-safe extension point for one request-body format.

    Processor registries reuse handler instances across requests. Implementations
    must therefore retain no request content or mutable per-request state.
    """

    def __init__(self, *, format_name: str) -> None:
        try:
            self.__format_name = parse_non_empty_scalar_string(format_name)
        except ValueError:
            raise FormatHandlerContractError(
                "format-handler metadata is invalid"
            ) from None

    @property
    def format_name(self) -> str:
        """Return the immutable construction-time format identity."""
        return self.__format_name

    def normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
        """Parse request bytes and validate the implementation's body model."""
        result: object = self._normalize(raw_body, policy_config)
        return parse_normalized_body(result)

    def reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        """Rebuild a normalized body and require a bytes result."""
        result: object = self._reconstruct(request_body, replacements_by_path)
        if not isinstance(result, bytes):
            raise FormatHandlerContractError(
                "format-handler output is invalid"
            ) from None
        return result

    @abstractmethod
    def _normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
        """Implement format-specific parsing without retaining request state."""
        raise NotImplementedError

    @abstractmethod
    def _reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        """Implement format-specific reconstruction without mutating the model."""
        raise NotImplementedError


def parse_normalized_body(result: object) -> RequestBody:
    """Require handlers to return the documented body model."""
    if not isinstance(result, RequestBody):
        raise FormatHandlerContractError("format-handler output is invalid") from None
    return result


__all__ = [
    "FormatHandler",
    "FormatHandlerContractError",
    "RequestBody",
    "TextBlock",
    "parse_normalized_body",
]
