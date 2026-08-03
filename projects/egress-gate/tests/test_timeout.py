"""Tests for the shared gate-pipeline timeout."""

from time import monotonic

import pytest

from egress_gate.errors import TimeoutExpiredError
from egress_gate.timeout import Timeout


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), 31])
def test_timeout_duration_is_strict_positive_and_bounded(
    seconds: bool | int | float,
) -> None:
    with pytest.raises(
        ValueError,
        match="finite number greater than 0 and at most 30",
    ):
        Timeout.from_seconds(seconds)


def test_expired_timeout_raises_typed_signal() -> None:
    timeout = Timeout(deadline=monotonic() - 1)

    with pytest.raises(TimeoutExpiredError) as captured:
        timeout.raise_if_expired()

    assert str(captured.value) == (
        "Egress Gate processing timed out. Reduce the request size or simplify "
        "the configured gates and rules, or increase the processing timeout "
        "to at most 30 seconds, then retry."
    )


def test_timeout_context_translates_delegated_timeout_error() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(TimeoutExpiredError), timeout.enforce():
        raise TimeoutError


def test_timeout_context_preserves_unrelated_errors() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(RuntimeError), timeout.enforce():
        raise RuntimeError
