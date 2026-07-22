"""Format-handler registry and format-agnostic request-body records."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_body.base import (
    FormatHandler,
    FormatHandlerContractError,
    RequestBody,
    TextBlock,
    parse_normalized_body,
)
from privacy_guard.request_body.json import JsonHandler

DEFAULT_FORMAT_HANDLERS: Mapping[str, FormatHandler] = MappingProxyType(
    {handler.format_name: handler for handler in (JsonHandler(),)}
)
"""Built-in handlers keyed by name; processors may receive a custom mapping."""


def select_format_handler(body_format: str) -> FormatHandler:
    """Return the registered FormatHandler for ``body_format`` (e.g. "json").

    ``body_format`` comes from ``PolicyConfig.body_format``. Raise for an
    unregistered format rather than silently falling back.
    """
    try:
        return DEFAULT_FORMAT_HANDLERS[body_format]
    except KeyError:
        raise PrivacyGuardError(ErrorCode.BODY_FORMAT_UNSUPPORTED) from None


__all__ = [
    "DEFAULT_FORMAT_HANDLERS",
    "FormatHandler",
    "FormatHandlerContractError",
    "JsonHandler",
    "RequestBody",
    "TextBlock",
    "parse_normalized_body",
    "select_format_handler",
]
