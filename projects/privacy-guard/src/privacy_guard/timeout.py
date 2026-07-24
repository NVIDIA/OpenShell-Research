"""A shared monotonic timeout for one entity-processing run."""

from __future__ import annotations

import math
from time import monotonic
from typing import Self

from pydantic import Field

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import MAX_TIMEOUT_SECONDS


class TimeoutExpired(Exception):
    """Signal that the shared entity-processing timeout has expired."""


class Timeout(StrictDomainModel):
    """An immutable monotonic deadline shared across processing stages."""

    deadline: float = Field(allow_inf_nan=False)

    @classmethod
    def from_seconds(cls, seconds: float) -> Self:
        """Create a timeout from a finite, positive bounded duration."""
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not math.isfinite(seconds)
            or seconds <= 0
            or seconds > MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout must be finite, positive, and within the limit")
        return cls(deadline=monotonic() + seconds)

    def remaining_seconds(self) -> float:
        """Return the positive duration remaining or raise ``TimeoutExpired``."""
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutExpired
        return remaining

    def raise_if_expired(self) -> None:
        """Raise ``TimeoutExpired`` when no time remains."""
        self.remaining_seconds()


__all__ = ["Timeout", "TimeoutExpired"]
