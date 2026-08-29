"""Command-line entry point for the OpenShell Tool Service."""

from __future__ import annotations

import logging

import uvicorn

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
