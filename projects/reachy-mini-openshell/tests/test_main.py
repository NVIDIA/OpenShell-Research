import sys
from typing import Any

import pytest

import reachy_mini_conversation_app.main as main_mod
from reachy_mini_conversation_app.utils import parse_args
from reachy_mini_conversation_app.tool_transport import RoutedToolTransport, ConversationUtilityTransport
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.rest_tool_transport import RestToolTransport


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


def test_parse_args_accepts_rest_tool_transport(monkeypatch: Any) -> None:
    """The standalone app should expose REST mode as a CLI choice."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-conversation-app", "--tool-transport", "rest"])

    args, unknown = parse_args()

    assert args.tool_transport == "rest"
    assert unknown == []


def test_rest_transport_factory_requires_endpoint() -> None:
    """REST mode should fail before robot or model startup when its endpoint is absent."""
    dependencies = ToolDependencies()

    with pytest.raises(ValueError, match="REACHY_REST_BASE_URL"):
        main_mod._build_tool_transport_factory("rest", dependencies, rest_base_url=None)


def test_rest_transport_factory_builds_routed_transport() -> None:
    """Each conversation connection should receive its own REST/local routing stack."""
    dependencies = ToolDependencies()
    factory = main_mod._build_tool_transport_factory(
        "rest",
        dependencies,
        rest_base_url="http://127.0.0.1:8000",
    )

    first = factory()
    second = factory()

    assert isinstance(first, RoutedToolTransport)
    assert isinstance(first._remote, RestToolTransport)
    assert isinstance(first._local, ConversationUtilityTransport)
    assert first is not second


@pytest.mark.asyncio
async def test_rest_transport_factory_exposes_only_v1_tools() -> None:
    """REST mode should hide every legacy camera, dance, emotion, and tracking tool."""
    factory = main_mod._build_tool_transport_factory(
        "rest",
        ToolDependencies(),
        rest_base_url="http://127.0.0.1:8000",
    )
    transport = factory()

    tools = await transport.list_tools()
    await transport.close()

    assert {tool["name"] for tool in tools} == {
        "move_head",
        "stop_motion",
        "do_nothing",
        "task_status",
        "task_cancel",
    }


@pytest.mark.asyncio
async def test_rest_transport_factory_adds_camera_only_with_adapter_endpoint() -> None:
    """REST mode should advertise camera only when the trusted native adapter is configured."""
    factory = main_mod._build_tool_transport_factory(
        "rest",
        ToolDependencies(),
        rest_base_url="http://127.0.0.1:8000",
        camera_base_url="http://host.openshell.internal:8042",
    )
    transport = factory()

    tools = await transport.list_tools()
    await transport.close()

    assert {tool["name"] for tool in tools} == {
        "move_head",
        "stop_motion",
        "camera",
        "do_nothing",
        "task_status",
        "task_cancel",
    }
