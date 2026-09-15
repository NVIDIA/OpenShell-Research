# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A shared monotonic timeout for one request-pipeline run."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Literal, Self

from pydantic import Field, ValidationError

from egress_gate.base import StrictDomainModel
from egress_gate.errors import TimeoutExpiredError

TIMEOUT_DURATION_PATTERN = r"^(?P<magnitude>[1-9][0-9]{0,8})(?P<unit>ms|s)$"
_TIMEOUT_DURATION_PATTERN = re.compile(TIMEOUT_DURATION_PATTERN)


class _DurationValue(StrictDomainModel):
    """A duration string validated with the shared OpenShell-style pattern."""

    value: str = Field(pattern=TIMEOUT_DURATION_PATTERN)


def validate_timeout_middleware_processing(seconds: float) -> float:
    """Return a supported internal processing timeout, in seconds."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int | float)
        or not math.isfinite(seconds)
    ):
        raise ValueError(
            "timeout_middleware_processing must be at least 10ms, using whole "
            "milliseconds"
        )
    validated_seconds = float(seconds)
    milliseconds = validated_seconds * 1000
    if not math.isfinite(milliseconds):
        raise ValueError(
            "timeout_middleware_processing must be at least 10ms, using whole "
            "milliseconds"
        )
    rounded_milliseconds = round(milliseconds)
    if rounded_milliseconds < 10 or not math.isclose(
        milliseconds, rounded_milliseconds
    ):
        raise ValueError(
            "timeout_middleware_processing must be at least 10ms, using whole "
            "milliseconds"
        )
    return validated_seconds


def format_timeout_middleware_processing(seconds: float) -> str:
    """Format a processing timeout for the OpenShell duration contract."""
    validated_seconds = validate_timeout_middleware_processing(seconds)
    rounded_milliseconds = round(validated_seconds * 1000)
    if rounded_milliseconds % 1000 == 0:
        return f"{rounded_milliseconds // 1000}s"
    return f"{rounded_milliseconds}ms"


def parse_duration(
    duration: str,
    unit: Literal["s", "ms"] = "s",
) -> float:
    """Parse a validated OpenShell-style duration into the requested unit."""
    try:
        validated_duration = _DurationValue(value=duration).value
    except ValidationError:
        raise ValueError("timeout must be an integer duration such as 10s or 500ms")
    match = _TIMEOUT_DURATION_PATTERN.fullmatch(validated_duration)
    if match is None:
        raise AssertionError("Pydantic accepted an unmatched timeout duration")
    magnitude = int(match.group("magnitude"))
    milliseconds = magnitude if match.group("unit") == "ms" else magnitude * 1000
    return milliseconds / 1000 if unit == "s" else float(milliseconds)


def parse_timeout_duration(duration: str) -> float:
    """Parse and validate an internal middleware processing duration."""
    seconds = parse_duration(duration)
    try:
        format_timeout_middleware_processing(seconds)
    except ValueError:
        raise ValueError(
            "timeout must be at least 10ms, using whole milliseconds"
        ) from None
    return float(seconds)


class Timeout(StrictDomainModel):
    """A monotonic deadline shared across gate preparation and execution."""

    deadline: float = Field(allow_inf_nan=False)

    @classmethod
    def from_seconds(cls, seconds: float) -> Self:
        """Create a timeout from a finite positive duration."""
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


__all__ = [
    "TIMEOUT_DURATION_PATTERN",
    "Timeout",
    "format_timeout_middleware_processing",
    "parse_duration",
    "parse_timeout_duration",
    "validate_timeout_middleware_processing",
]
