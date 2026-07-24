"""Reusable string validators and the field types built from them."""

from typing import Annotated

from pydantic import BeforeValidator

from privacy_guard.constants import MAX_DIAGNOSTIC_TEXT_BYTES


def validate_scalar_string(value: object) -> str:
    """Validate and return a string containing only Unicode scalar values."""
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("string must contain valid Unicode scalar values")
    return value


def validate_bounded_metadata_string(value: object) -> str:
    """Validate and return a non-empty, size-bounded metadata string."""
    validated = validate_scalar_string(value)
    if not validated:
        raise ValueError("string must not be empty")
    if (
        len(validated) > MAX_DIAGNOSTIC_TEXT_BYTES
        or len(validated.encode("utf-8")) > MAX_DIAGNOSTIC_TEXT_BYTES
    ):
        raise ValueError("metadata exceeds the UTF-8 byte limit")
    return validated


ScalarString = Annotated[str, BeforeValidator(validate_scalar_string)]
BoundedMetadataString = Annotated[
    str,
    BeforeValidator(validate_bounded_metadata_string),
]


__all__ = [
    "BoundedMetadataString",
    "ScalarString",
    "validate_bounded_metadata_string",
    "validate_scalar_string",
]
