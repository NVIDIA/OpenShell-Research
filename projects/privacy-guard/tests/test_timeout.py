"""Tests for the shared entity-processing timeout."""

from time import monotonic

import pytest

from privacy_guard.errors import TimeoutExpiredError
from privacy_guard.timeout import Timeout


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), 31])
def test_timeout_duration_is_strict_positive_and_bounded(
    seconds: bool | int | float,
) -> None:
    with pytest.raises(ValueError):
        Timeout.from_seconds(seconds)


def test_expired_timeout_raises_typed_signal() -> None:
    timeout = Timeout(deadline=monotonic() - 1)

    with pytest.raises(TimeoutExpiredError):
        timeout.raise_if_expired()


def test_timeout_context_translates_delegated_timeout_error() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(TimeoutExpiredError), timeout.enforce():
        raise TimeoutError


def test_timeout_context_preserves_unrelated_errors() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(RuntimeError), timeout.enforce():
        raise RuntimeError
