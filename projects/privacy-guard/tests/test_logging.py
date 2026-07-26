"""Privacy Guard logging configuration tests."""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest

from privacy_guard.logging import (
    DEFAULT_LOGGING_CONFIG,
    ColorMode,
    LoggingConfig,
    configure_logging,
    get_logger,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    reset_logging()
    yield
    reset_logging()


def test_configure_logging_emits_consistent_package_logs() -> None:
    stream = StringIO()
    configure_logging(LoggingConfig(stream=stream))

    logging.getLogger("privacy_guard.service").info("server_started")
    logging.getLogger("privacy_guard.service").debug("hidden_detail")

    output = stream.getvalue()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        r" \| INFO     \| privacy_guard\.service \| server_started\n",
        output,
    )
    assert "hidden_detail" not in output
    assert "\033[" not in output


def test_default_logging_config_uses_info_and_terminal_aware_colors() -> None:
    assert DEFAULT_LOGGING_CONFIG == LoggingConfig(
        level=logging.INFO,
        stream=None,
        color_mode=ColorMode.AUTO,
    )


def test_configure_logging_colors_interactive_output() -> None:
    stream = _TerminalStream()
    configure_logging(LoggingConfig(stream=stream))

    logging.getLogger("privacy_guard.service").warning("resource_pressure")

    output = stream.getvalue()
    assert "\033[33mWARNING \033[0m" in output
    assert "\033[36mprivacy_guard.service\033[0m" in output
    assert output.endswith(" | resource_pressure\n")


def test_configure_logging_can_disable_terminal_colors() -> None:
    stream = _TerminalStream()
    configure_logging(LoggingConfig(stream=stream, color_mode=ColorMode.NEVER))

    logging.getLogger("privacy_guard.service").error("startup_failed")

    assert "\033[" not in stream.getvalue()


def test_configure_logging_can_force_colors_for_redirected_output() -> None:
    stream = StringIO()
    configure_logging(LoggingConfig(stream=stream, color_mode=ColorMode.ALWAYS))

    logging.getLogger("privacy_guard.service").info("server_started")

    assert "\033[32mINFO    \033[0m" in stream.getvalue()


def test_get_logger_returns_named_package_logger() -> None:
    logger = get_logger("privacy_guard.custom_engine")

    assert logger is logging.getLogger("privacy_guard.custom_engine")


def test_configure_logging_accepts_native_log_levels() -> None:
    stream = StringIO()
    configure_logging(LoggingConfig(level=logging.DEBUG, stream=stream))

    logging.getLogger("privacy_guard.request_processor").debug("processing_diagnostic")

    assert (
        "DEBUG    | privacy_guard.request_processor | processing_diagnostic"
        in stream.getvalue()
    )


def test_configure_logging_replaces_its_previous_handler() -> None:
    first_stream = StringIO()
    second_stream = StringIO()
    configure_logging(LoggingConfig(stream=first_stream))
    configure_logging(LoggingConfig(stream=second_stream))

    logging.getLogger("privacy_guard").info("configured_once")

    assert "configured_once" not in first_stream.getvalue()
    assert second_stream.getvalue().count("configured_once") == 1


def test_reset_logging_restores_application_logging() -> None:
    package_logger = logging.getLogger("privacy_guard")
    application_handler = logging.NullHandler()
    package_logger.addHandler(application_handler)
    configure_logging(LoggingConfig(stream=StringIO()))

    reset_logging()

    try:
        assert package_logger.handlers == [application_handler]
        assert package_logger.level == logging.NOTSET
        assert package_logger.propagate is True
    finally:
        package_logger.removeHandler(application_handler)


def test_source_modules_use_the_shared_logging_module() -> None:
    direct_imports: list[str] = []
    for path in _SOURCE_ROOT.rglob("*.py"):
        if path == _LOGGING_MODULE or "bindings" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            (
                isinstance(node, ast.Import)
                and any(imported.name == "logging" for imported in node.names)
            )
            or (isinstance(node, ast.ImportFrom) and node.module == "logging")
            for node in ast.walk(tree)
        ):
            direct_imports.append(str(path.relative_to(_SOURCE_ROOT)))

    assert direct_imports == []


_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "privacy_guard"
_LOGGING_MODULE = _SOURCE_ROOT / "logging.py"


class _TerminalStream(StringIO):
    def isatty(self) -> bool:
        return True
