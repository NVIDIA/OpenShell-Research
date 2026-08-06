"""Tests for the shared gate-pipeline timeout."""

from time import monotonic

import pytest

from egress_gate.errors import TimeoutExpiredError
from egress_gate.timeout import (
    Timeout,
    format_timeout_middleware_processing,
    parse_timeout_duration,
)


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), 31])
def test_timeout_duration_is_strict_positive_and_bounded(
    seconds: bool | int | float,
) -> None:
    with pytest.raises(
        ValueError,
        match="greater than 0 and no more than the 30s timeout_gateway_ceiling",
    ):
        Timeout.from_seconds(seconds)


@pytest.mark.parametrize(
    ("seconds", "duration"),
    [(0.01, "10ms"), (1.0, "1s"), (4.5, "4500ms"), (30.0, "30s")],
)
def test_middleware_timeout_uses_the_contract_duration_format(
    seconds: float,
    duration: str,
) -> None:
    assert format_timeout_middleware_processing(seconds) == duration


@pytest.mark.parametrize("seconds", [0.001, 1.0001])
def test_middleware_timeout_rejects_unsupported_precision(seconds: float) -> None:
    with pytest.raises(ValueError, match="whole milliseconds"):
        format_timeout_middleware_processing(seconds)


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("10ms", 0.01), ("500ms", 0.5), ("1s", 1.0), ("30s", 30.0)],
)
def test_timeout_duration_parser_accepts_concise_cli_values(
    duration: str,
    seconds: float,
) -> None:
    assert parse_timeout_duration(duration) == seconds


@pytest.mark.parametrize("duration", ["", "1", "1.5s", "0s", "31s", "1m"])
def test_timeout_duration_parser_rejects_unsupported_values(duration: str) -> None:
    with pytest.raises(ValueError, match="timeout"):
        parse_timeout_duration(duration)


def test_expired_timeout_raises_typed_signal() -> None:
    timeout = Timeout(deadline=monotonic() - 1)

    with pytest.raises(TimeoutExpiredError) as captured:
        timeout.raise_if_expired()

    assert str(captured.value) == (
        "Egress Gate processing timed out. Reduce the request size or simplify "
        "the configured gates and rules, or increase egress-gate serve --timeout "
        "to at most 30s, then retry."
    )


def test_timeout_context_translates_delegated_timeout_error() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(TimeoutExpiredError), timeout.enforce():
        raise TimeoutError


def test_timeout_context_preserves_unrelated_errors() -> None:
    timeout = Timeout.from_seconds(1)

    with pytest.raises(RuntimeError), timeout.enforce():
        raise RuntimeError
