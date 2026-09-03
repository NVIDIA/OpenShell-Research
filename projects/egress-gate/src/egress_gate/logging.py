# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Native Python logging configuration for Egress Gate."""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TextIO


class ColorMode(StrEnum):
    """When Egress Gate should add ANSI colors to console logs."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True)
class LoggingConfig:
    """Egress Gate console logging settings."""

    level: int | str = logging.INFO
    stream: TextIO | None = None
    color_mode: ColorMode = ColorMode.AUTO


DEFAULT_LOGGING_CONFIG = LoggingConfig()


def get_logger(name: str) -> logging.Logger:
    """Return a logger governed by Egress Gate's shared configuration."""
    return logging.getLogger(name)


def configure_logging(
    config: LoggingConfig = DEFAULT_LOGGING_CONFIG,
) -> None:
    """Configure consistent console logging for the Egress Gate package.

    Repeated calls replace the handler installed by this function. Handlers
    installed by the containing application are left unchanged.
    """
    package_logger = get_logger("egress_gate")
    package_logger.setLevel(config.level)

    for handler in package_logger.handlers[:]:
        if isinstance(handler, _EgressGateStreamHandler):
            package_logger.removeHandler(handler)
            handler.close()

    handler = _EgressGateStreamHandler(config.stream)
    use_colors = config.color_mode is ColorMode.ALWAYS or (
        config.color_mode is ColorMode.AUTO
        and "NO_COLOR" not in os.environ
        and handler.stream.isatty()
    )
    handler.setFormatter(_EgressGateFormatter(use_colors=use_colors))
    package_logger.addHandler(handler)
    package_logger.propagate = False


def configure_json_log(path: Path) -> None:
    """Write content-safe Egress Gate records as newline-delimited JSON."""
    package_logger = get_logger("egress_gate")
    for handler in package_logger.handlers[:]:
        if isinstance(handler, _EgressGateJsonHandler):
            package_logger.removeHandler(handler)
            handler.close()

    handler = _EgressGateJsonHandler(path, encoding="utf-8")
    handler.setFormatter(_EgressGateJsonFormatter())
    package_logger.addHandler(handler)


def reset_logging() -> None:
    """Remove logging configuration installed by :func:`configure_logging`."""
    package_logger = get_logger("egress_gate")
    managed_handlers = [
        handler
        for handler in package_logger.handlers
        if isinstance(handler, _EgressGateStreamHandler | _EgressGateJsonHandler)
    ]
    if not managed_handlers:
        return

    for handler in managed_handlers:
        package_logger.removeHandler(handler)
        handler.close()
    package_logger.setLevel(logging.NOTSET)
    package_logger.propagate = True


class _EgressGateStreamHandler(logging.StreamHandler[TextIO]):
    """Stream handler owned by Egress Gate's logging configuration."""


class _EgressGateJsonHandler(logging.FileHandler):
    """Optional content-safe JSON sink owned by Egress Gate."""


class _EgressGateJsonFormatter(logging.Formatter):
    """Serialize only the bounded evaluation fields used by verification."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "event": getattr(record, "event", record.getMessage()),
                "request_id": getattr(record, "request_id", None),
                "duration_ms": getattr(record, "duration_ms", None),
                "action": getattr(record, "action", None),
                "reason_code": getattr(record, "reason_code", None),
                "finding_count": getattr(record, "finding_count", None),
                "decision_source_kind": getattr(record, "decision_source_kind", None),
                "error_code": getattr(record, "error_code", None),
            },
            separators=(",", ":"),
        )


class _EgressGateFormatter(logging.Formatter):
    """Readable console formatter with optional level-aware color."""

    def __init__(self, *, use_colors: bool) -> None:
        log_format = _PLAIN_LOG_FORMAT
        if use_colors:
            log_format = _COLOR_LOG_FORMAT
        super().__init__(log_format, datefmt="%Y-%m-%d %H:%M:%S")
        self._use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        formatted_record = copy.copy(record)
        level_name = f"{record.levelname:<8}"
        if self._use_colors:
            color = _LEVEL_COLORS.get(record.levelno, _ANSI_BRIGHT_BLACK)
            level_name = f"{color}{level_name}{_ANSI_RESET}"
        formatted_record.levelname = level_name
        return super().format(formatted_record)


_PLAIN_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_BRIGHT_BLACK = "\033[90m"
_ANSI_CYAN = "\033[36m"
_COLOR_LOG_FORMAT = (
    f"{_ANSI_DIM}%(asctime)s{_ANSI_RESET} | "
    f"%(levelname)s | {_ANSI_CYAN}%(name)s{_ANSI_RESET} | %(message)s"
)
_LEVEL_COLORS = {
    logging.DEBUG: _ANSI_BRIGHT_BLACK,
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


__all__ = [
    "ColorMode",
    "DEFAULT_LOGGING_CONFIG",
    "LoggingConfig",
    "configure_json_log",
    "configure_logging",
    "get_logger",
    "reset_logging",
]
