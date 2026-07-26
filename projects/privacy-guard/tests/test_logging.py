"""Privacy Guard logging configuration tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from io import StringIO

import pytest

from privacy_guard.logging import configure_logging, reset_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    reset_logging()
    yield
    reset_logging()


def test_configure_logging_emits_consistent_package_logs() -> None:
    stream = StringIO()
    configure_logging(stream=stream)

    logging.getLogger("privacy_guard.service").info("server_started")
    logging.getLogger("privacy_guard.service").debug("hidden_detail")

    output = stream.getvalue()
    assert "[INFO] server_started" in output
    assert "hidden_detail" not in output


def test_configure_logging_accepts_native_log_levels() -> None:
    stream = StringIO()
    configure_logging(logging.DEBUG, stream=stream)

    logging.getLogger("privacy_guard.request_processor").debug("processing_diagnostic")

    assert "[DEBUG] processing_diagnostic" in stream.getvalue()


def test_configure_logging_replaces_its_previous_handler() -> None:
    first_stream = StringIO()
    second_stream = StringIO()
    configure_logging(stream=first_stream)
    configure_logging(stream=second_stream)

    logging.getLogger("privacy_guard").info("configured_once")

    assert "configured_once" not in first_stream.getvalue()
    assert second_stream.getvalue().count("configured_once") == 1


def test_reset_logging_restores_application_logging() -> None:
    package_logger = logging.getLogger("privacy_guard")
    application_handler = logging.NullHandler()
    package_logger.addHandler(application_handler)
    configure_logging(stream=StringIO())

    reset_logging()

    try:
        assert package_logger.handlers == [application_handler]
        assert package_logger.level == logging.NOTSET
        assert package_logger.propagate is True
    finally:
        package_logger.removeHandler(application_handler)
