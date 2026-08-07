"""A shared monotonic timeout for one request-pipeline run."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Self

from pydantic import Field

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_TIMEOUT_MIDDLEWARE_PROCESSING
from egress_gate.errors import TimeoutExpiredError


def validate_timeout_middleware_processing(seconds: object) -> float:
    """Return a supported internal processing timeout, in seconds."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int | float)
        or not math.isfinite(seconds)
    ):
        raise ValueError(
            "timeout_middleware_processing must be between 10ms and "
            f"{MAX_TIMEOUT_MIDDLEWARE_PROCESSING:g}s, using whole milliseconds"
        )
    validated_seconds = float(seconds)
    milliseconds = validated_seconds * 1000
    rounded_milliseconds = round(milliseconds)
    if (
        rounded_milliseconds < 10
        or validated_seconds > MAX_TIMEOUT_MIDDLEWARE_PROCESSING
        or not math.isclose(milliseconds, rounded_milliseconds)
    ):
        raise ValueError(
            "timeout_middleware_processing must be between 10ms and "
            f"{MAX_TIMEOUT_MIDDLEWARE_PROCESSING:g}s, using whole milliseconds"
        )
    return validated_seconds


def format_timeout_middleware_processing(seconds: object) -> str:
    """Format a processing timeout for the OpenShell duration contract."""
    validated_seconds = validate_timeout_middleware_processing(seconds)
    rounded_milliseconds = round(validated_seconds * 1000)
    if rounded_milliseconds % 1000 == 0:
        return f"{rounded_milliseconds // 1000}s"
    return f"{rounded_milliseconds}ms"


def parse_timeout_duration(duration: object) -> float:
    """Parse an integer OpenShell-style duration such as ``10s`` or ``500ms``."""
    if not isinstance(duration, str):
        raise ValueError("timeout must be an integer duration such as 10s or 500ms")
    match = _TIMEOUT_DURATION_PATTERN.fullmatch(duration)
    if match is None:
        raise ValueError("timeout must be an integer duration such as 10s or 500ms")
    magnitude = int(match.group("magnitude"))
    seconds = magnitude / 1000 if match.group("unit") == "ms" else magnitude
    try:
        format_timeout_middleware_processing(seconds)
    except ValueError:
        raise ValueError(
            "timeout must be between 10ms and "
            f"{MAX_TIMEOUT_MIDDLEWARE_PROCESSING:g}s, using whole milliseconds"
        ) from None
    return float(seconds)


class Timeout(StrictDomainModel):
    """A monotonic deadline shared across gate preparation and execution."""

    deadline: float = Field(allow_inf_nan=False)

    @classmethod
    def from_seconds(cls, seconds: float) -> Self:
        """Create a timeout from a finite, positive bounded duration."""
        return cls(
            deadline=monotonic() + validate_timeout_middleware_processing(seconds)
        )

    def remaining_seconds(self) -> float:
        """Return the positive duration remaining or raise ``TimeoutExpiredError``."""
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutExpiredError
        return remaining

    def raise_if_expired(self) -> None:
        """Raise ``TimeoutExpiredError`` when no time remains."""
        self.remaining_seconds()

    @contextmanager
    def enforce(self) -> Iterator[None]:
        """Enforce this deadline around one delegated operation."""
        self.raise_if_expired()
        try:
            yield
        except TimeoutError:
            raise TimeoutExpiredError from None
        self.raise_if_expired()


_TIMEOUT_DURATION_PATTERN = re.compile(r"(?P<magnitude>[0-9]{1,9})(?P<unit>ms|s)")


__all__ = [
    "Timeout",
    "format_timeout_middleware_processing",
    "parse_timeout_duration",
    "validate_timeout_middleware_processing",
]
