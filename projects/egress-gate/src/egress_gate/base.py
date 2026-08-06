"""Package-wide base class for validated, immutable domain objects."""

from pydantic import BaseModel, ConfigDict


class StrictDomainModel(BaseModel):
    """Base for immutable domain values parsed without implicit coercion."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        validate_default=True,
    )


__all__ = ["StrictDomainModel"]
