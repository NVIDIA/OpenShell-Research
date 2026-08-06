"""A shared monotonic timeout for one request-pipeline run."""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Self

from pydantic import Field

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_TIMEOUT_SECONDS
from egress_gate.errors import TimeoutExpiredError


def validate_timeout_seconds(seconds: object) -> float:
    """Return a finite supported processing timeout in seconds."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int | float)
        or not math.isfinite(seconds)
        or seconds <= 0
        or seconds > MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "timeout seconds must be a finite number greater than 0 and at most "
            f"{MAX_TIMEOUT_SECONDS:g}"
        )
    return float(seconds)


class Timeout(StrictDomainModel):
    """A monotonic deadline shared across gate preparation and execution."""

    deadline: float = Field(allow_inf_nan=False)

    @classmethod
    def from_seconds(cls, seconds: float) -> Self:
        """Create a timeout from a finite, positive bounded duration."""
        return cls(deadline=monotonic() + validate_timeout_seconds(seconds))

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


__all__ = ["Timeout", "validate_timeout_seconds"]
