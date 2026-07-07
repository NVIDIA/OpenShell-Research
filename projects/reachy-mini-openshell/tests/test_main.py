import sys
from typing import Any

import pytest

import reachy_mini_conversation_app.main as main_mod
from reachy_mini_conversation_app.utils import parse_args
from reachy_mini_conversation_app.tool_transport import LocalToolTransport, RoutedToolTransport
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple[Any, ...]]] = []
        self.debugs: list[tuple[str, tuple[Any, ...]]] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append((message, args))

    def debug(self, message: str, *args: Any) -> None:
        self.debugs.append((message, args))


def test_shutdown_step_suppresses_keyboard_interrupt() -> None:
    """Cleanup should not print a traceback if Ctrl-C lands during a shutdown step."""
    logger = _FakeLogger()

    def stop() -> None:
        raise KeyboardInterrupt

    main_mod._shutdown_step(logger, "movement manager", stop)

    assert logger.warnings == [("Shutdown interrupted while stopping %s; continuing cleanup.", ("movement manager",))]


def test_shutdown_step_suppresses_regular_cleanup_errors() -> None:
    """Cleanup errors should be logged and allow later shutdown steps to run."""
    logger = _FakeLogger()

    def stop() -> None:
        raise RuntimeError("already stopped")

    main_mod._shutdown_step(logger, "media", stop)

    assert len(logger.debugs) == 1
    message, args = logger.debugs[0]
    assert message == "Error while stopping %s: %s"
    assert args[0] == "media"
    assert isinstance(args[1], RuntimeError)
    assert str(args[1]) == "already stopped"


def test_parse_args_accepts_mcp_tool_transport(monkeypatch: Any) -> None:
    """The standalone app should expose MCP mode as a CLI choice."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-conversation-app", "--tool-transport", "mcp"])

    args, unknown = parse_args()

    assert args.tool_transport == "mcp"
    assert unknown == []


def test_mcp_transport_factory_requires_endpoint_and_token() -> None:
    """MCP mode should fail before robot or model startup when its connection config is absent."""
    dependencies = ToolDependencies()

    with pytest.raises(ValueError, match="REACHY_MCP_URL"):
        main_mod._build_tool_transport_factory("mcp", dependencies, mcp_url=None, mcp_token="token")
    with pytest.raises(ValueError, match="REACHY_MCP_TOKEN"):
        main_mod._build_tool_transport_factory(
            "mcp",
            dependencies,
            mcp_url="http://127.0.0.1:8766/mcp",
            mcp_token=None,
        )


def test_mcp_transport_factory_builds_routed_transport() -> None:
    """Each conversation connection should receive its own MCP/local routing stack."""
    dependencies = ToolDependencies()
    factory = main_mod._build_tool_transport_factory(
        "mcp",
        dependencies,
        mcp_url="http://127.0.0.1:8766/mcp",
        mcp_token="token",
    )

    first = factory()
    second = factory()

    assert isinstance(first, RoutedToolTransport)
    assert isinstance(first._local, LocalToolTransport)
    assert first is not second
