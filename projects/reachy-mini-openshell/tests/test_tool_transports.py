"""Tests for local and routed tool transports."""

from __future__ import annotations
from typing import Any, cast

import pytest

from reachy_mini_conversation_app.tool_transport import (
    ToolTransport,
    LocalToolTransport,
    RoutedToolTransport,
    ConversationUtilityTransport,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.background_tool_manager import ToolCallRoutine, BackgroundToolManager


class _RecordingTransport:
    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.close_calls = 0

    async def list_tools(self) -> list[dict[str, Any]]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {"transport": "called", "name": name}

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_local_transport_lists_and_dispatches_existing_tools() -> None:
    """Local mode should preserve dependency filtering and Python dispatch."""
    dependencies = ToolDependencies(
        reachy_mini=cast(Any, None),
        movement_manager=object(),
        camera_worker=None,
    )
    transport = LocalToolTransport(dependencies)

    tools = await transport.list_tools()
    result = await transport.call_tool("do_nothing", {"reason": "testing"})

    assert isinstance(transport, ToolTransport)
    assert "camera" not in {tool["name"] for tool in tools}
    assert result == {"status": "doing nothing", "reason": "testing"}
    await transport.close()


@pytest.mark.asyncio
async def test_conversation_utility_transport_does_not_load_robot_registry() -> None:
    """REST utilities should remain usable without the native robot dependency graph."""
    transport = ConversationUtilityTransport()

    tools = await transport.list_tools()
    result = await transport.call_tool("do_nothing", {"reason": "testing"})

    assert {tool["name"] for tool in tools} == {"do_nothing", "task_status", "task_cancel"}
    assert result == {"status": "doing nothing", "reason": "testing"}


@pytest.mark.asyncio
async def test_routed_transport_keeps_only_conversation_system_tools_local() -> None:
    """Remote mode should route hardware remotely while retaining narrow local utilities."""
    remote = _RecordingTransport(
        [
            {"type": "function", "name": "move_head"},
            {"type": "function", "name": "stop_motion"},
            {"type": "function", "name": "do_nothing", "source": "remote"},
        ]
    )
    local = _RecordingTransport(
        [
            {"type": "function", "name": "camera"},
            {"type": "function", "name": "do_nothing", "source": "local"},
            {"type": "function", "name": "task_status"},
            {"type": "function", "name": "task_cancel"},
        ]
    )
    transport = RoutedToolTransport(remote=remote, local=local)

    tools = await transport.list_tools()
    remote_result = await transport.call_tool("move_head", {"directions": ["left"]})
    local_result = await transport.call_tool("do_nothing", {"reason": "testing"})
    await transport.close()

    assert [tool["name"] for tool in tools] == [
        "move_head",
        "stop_motion",
        "do_nothing",
        "task_status",
        "task_cancel",
    ]
    assert tools[2]["source"] == "local"
    assert remote_result["name"] == "move_head"
    assert local_result["name"] == "do_nothing"
    assert remote.calls == [("move_head", {"directions": ["left"]})]
    assert local.calls == [("do_nothing", {"reason": "testing"})]
    assert remote.close_calls == 1
    assert local.close_calls == 1


@pytest.mark.asyncio
async def test_tool_call_routine_uses_transport_but_keeps_manager_tools_local() -> None:
    """Deferred hardware calls use the transport while task controls retain manager access."""
    transport = _RecordingTransport([])
    manager = BackgroundToolManager()
    dependencies = ToolDependencies()

    hardware_result = await ToolCallRoutine(
        tool_name="move_head",
        args_json_str='{"directions":["left"]}',
        deps=dependencies,
        transport=transport,
    )(manager)
    status_result = await ToolCallRoutine(
        tool_name="task_status",
        args_json_str="{}",
        deps=dependencies,
        transport=transport,
    )(manager)

    assert hardware_result == {"transport": "called", "name": "move_head"}
    assert transport.calls == [("move_head", {"directions": ["left"]})]
    assert status_result == {"status": "idle", "message": "No tools running in the background."}
