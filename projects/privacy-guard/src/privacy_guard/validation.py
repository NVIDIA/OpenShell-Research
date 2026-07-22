"""Shared strict, content-safe validation primitives."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict

from privacy_guard.constants import MAX_SCANNER_METADATA_BYTES


def parse_scalar_string(value: object) -> str:
    """Return a string containing only valid Unicode scalar values."""
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("string must contain valid Unicode scalar values")
    return value


def parse_non_empty_scalar_string(value: object) -> str:
    """Return a non-empty Unicode scalar string."""
    parsed = parse_scalar_string(value)
    if not parsed:
        raise ValueError("string must not be empty")
    return parsed


def parse_bounded_metadata_string(value: object) -> str:
    """Return metadata whose UTF-8 representation is within the package limit."""
    parsed = parse_non_empty_scalar_string(value)
    if (
        len(parsed) > MAX_SCANNER_METADATA_BYTES
        or len(parsed.encode("utf-8")) > MAX_SCANNER_METADATA_BYTES
    ):
        raise ValueError("metadata exceeds the UTF-8 byte limit")
    return parsed


ScalarString = Annotated[str, BeforeValidator(parse_scalar_string)]
NonEmptyScalarString = Annotated[str, BeforeValidator(parse_non_empty_scalar_string)]
BoundedMetadataString = Annotated[str, BeforeValidator(parse_bounded_metadata_string)]


class StrictDomainModel(BaseModel):
    """Base for immutable domain values parsed without implicit coercion."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        validate_default=True,
    )


class StrictSensitiveModel(StrictDomainModel):
    """Semantic base for strict models containing repr-hidden sensitive fields."""
