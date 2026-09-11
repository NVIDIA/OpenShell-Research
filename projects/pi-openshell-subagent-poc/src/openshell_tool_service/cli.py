"""Command-line entry point for the OpenShell Tool Service."""

from __future__ import annotations

import logging

import uvicorn

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if settings.log_level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        timeout_graceful_shutdown=settings.graceful_shutdown_seconds,
    )


if __name__ == "__main__":
    main()
