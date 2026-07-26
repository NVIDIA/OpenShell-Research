"""Native Python logging configuration for Privacy Guard."""

from __future__ import annotations

import logging
from typing import TextIO


def get_logger(name: str) -> logging.Logger:
    """Return a logger governed by Privacy Guard's shared configuration."""
    return logging.getLogger(name)


def configure_logging(
    level: int | str = logging.INFO,
    *,
    stream: TextIO | None = None,
) -> None:
    """Configure consistent console logging for the Privacy Guard package.

    Repeated calls replace the handler installed by this function. Handlers
    installed by the containing application are left unchanged.
    """
    package_logger = get_logger("privacy_guard")
    package_logger.setLevel(level)

    for handler in package_logger.handlers[:]:
        if isinstance(handler, _PrivacyGuardStreamHandler):
            package_logger.removeHandler(handler)
            handler.close()

    handler = _PrivacyGuardStreamHandler(stream)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    package_logger.addHandler(handler)
    package_logger.propagate = False


def reset_logging() -> None:
    """Remove logging configuration installed by :func:`configure_logging`."""
    package_logger = get_logger("privacy_guard")
    managed_handlers = [
        handler
        for handler in package_logger.handlers
        if isinstance(handler, _PrivacyGuardStreamHandler)
    ]
    if not managed_handlers:
        return

    for handler in managed_handlers:
        package_logger.removeHandler(handler)
        handler.close()
    package_logger.setLevel(logging.NOTSET)
    package_logger.propagate = True


class _PrivacyGuardStreamHandler(logging.StreamHandler[TextIO]):
    """Stream handler owned by Privacy Guard's logging configuration."""


__all__ = ["configure_logging", "get_logger", "reset_logging"]
