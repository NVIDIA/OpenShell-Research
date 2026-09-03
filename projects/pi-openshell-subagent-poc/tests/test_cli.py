from __future__ import annotations

from typing import Any

from openshell_tool_service import cli


def test_cli_bounds_graceful_http_shutdown(monkeypatch) -> None:
    monkeypatch.setenv("OPENSHELL_TOOL_SERVICE_TOKEN", "test-token")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-api-key")
    monkeypatch.setenv("POC_GRACEFUL_SHUTDOWN_SECONDS", "2")
    application = object()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "create_app", lambda _settings: application)

    def run(app, **kwargs) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", run)

    cli.main()

    assert captured["app"] is application
    assert captured["timeout_graceful_shutdown"] == 2
