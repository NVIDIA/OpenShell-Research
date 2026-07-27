"""Private constrained-template validation and bounded rendering."""

from __future__ import annotations

from string import Formatter
from typing import Protocol

from privacy_guard.constants import MAX_BODY_BYTES, MAX_DIAGNOSTIC_TEXT_BYTES
from privacy_guard.errors import EngineLimitExceededError
from privacy_guard.string_validators import validate_scalar_string


class _ReplacementSpan(Protocol):
    @property
    def entity(self) -> str: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


def validate_replacement_template(value: object) -> str:
    """Return a bounded template containing only literals and ``{entity}``."""
    template = validate_scalar_string(value)
    if len(template.encode("utf-8")) > MAX_DIAGNOSTIC_TEXT_BYTES:
        raise ValueError("replacement template exceeds the size limit")
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(template):
            if field_name is not None and field_name != "entity":
                raise ValueError
            if format_spec or conversion is not None:
                raise ValueError
    except ValueError:
        raise ValueError("replacement template syntax is invalid") from None
    return template


def render_bounded_replacement(
    text: str,
    spans: tuple[_ReplacementSpan, ...],
    template: str,
    *,
    limit_message: str,
) -> str:
    """Project UTF-8 size, then render ordered non-overlapping spans."""
    projected_size = 0
    cursor = 0
    for span in spans:
        projected_size += len(text[cursor : span.start].encode("utf-8"))
        projected_size += _rendered_template_size(template, span.entity)
        if projected_size > MAX_BODY_BYTES:
            raise EngineLimitExceededError(limit_message)
        cursor = span.end
    projected_size += len(text[cursor:].encode("utf-8"))
    if projected_size > MAX_BODY_BYTES:
        raise EngineLimitExceededError(limit_message)

    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(template.format(entity=span.entity))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _rendered_template_size(template: str, entity: str) -> int:
    size = 0
    entity_size = len(entity.encode("utf-8"))
    for literal, field_name, _, _ in Formatter().parse(template):
        size += len(literal.encode("utf-8"))
        if field_name is not None:
            size += entity_size
    return size
