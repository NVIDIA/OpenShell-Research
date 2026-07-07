"""Tests for local and MCP tool transports."""

from __future__ import annotations
import asyncio
from typing import Any, cast
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import pytest
from mcp import types
from mcp.shared.exceptions import McpError

from reachy_mini_conversation_app.mcp_client import (
    McpToolTransport,
    McpTransportUnavailable,
)
from reachy_mini_conversation_app.tool_transport import (
    ToolTransport,
    LocalToolTransport,
    RoutedToolTransport,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.background_tool_manager import ToolCallRoutine, BackgroundToolManager


class _FakeSession:
    def __init__(
        self,
        *,
        pages: dict[str | None, types.ListToolsResult] | None = None,
        results: list[types.CallToolResult | Exception] | None = None,
    ) -> None:
        self.pages = pages or {None: types.ListToolsResult(tools=[])}
        self.results = list(results or [])
        self.initialize_calls = 0
        self.list_calls: list[str | None] = []
        self.tool_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def initialize(self) -> Any:
        self.initialize_calls += 1
        return object()

    async def list_tools(self, *, cursor: str | None = None) -> types.ListToolsResult:
        self.list_calls.append(cursor)
        return self.pages[cursor]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> types.CallToolResult:
        self.tool_calls.append((name, arguments))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeConnector:
    def __init__(self, sessions: list[_FakeSession | Exception]) -> None:
        self.sessions = list(sessions)
        self.connect_calls: list[tuple[str, str, float]] = []
        self.close_calls = 0
        self.enter_tasks: list[asyncio.Task[Any] | None] = []
        self.exit_tasks: list[asyncio.Task[Any] | None] = []

    @asynccontextmanager
    async def __call__(self, url: str, token: str, timeout: float) -> AsyncIterator[_FakeSession]:
        self.connect_calls.append((url, token, timeout))
        self.enter_tasks.append(asyncio.current_task())
        session = self.sessions.pop(0)
        if isinstance(session, Exception):
            raise session
        try:
            yield session
        finally:
            self.close_calls += 1
            self.exit_tasks.append(asyncio.current_task())


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
async def test_routed_transport_keeps_only_conversation_system_tools_local() -> None:
    """MCP mode should route hardware remotely while retaining narrow local utilities."""
    remote = _RecordingTransport(
        [
            {"type": "function", "name": "move_head"},
            {"type": "function", "name": "dance"},
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
        "dance",
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


@pytest.mark.asyncio
async def test_mcp_transport_initializes_paginates_converts_and_caches_tools() -> None:
    """Discovery should initialize once, include every page, and return safe copies."""
    move_schema = {"type": "object", "properties": {"directions": {"type": "array"}}}
    session = _FakeSession(
        pages={
            None: types.ListToolsResult(
                tools=[types.Tool(name="move_head", description="Move Reachy's head", inputSchema=move_schema)],
                nextCursor="page-2",
            ),
            "page-2": types.ListToolsResult(
                tools=[types.Tool(name="camera", description=None, inputSchema={"type": "object"})]
            ),
        }
    )
    connector = _FakeConnector([session])
    transport = McpToolTransport("http://127.0.0.1:8766/mcp", "secret", session_connector=connector)

    first = await transport.list_tools()
    first[0]["parameters"]["mutated"] = True
    second = await transport.list_tools()

    assert second == [
        {
            "type": "function",
            "name": "move_head",
            "description": "Move Reachy's head",
            "parameters": move_schema,
        },
        {
            "type": "function",
            "name": "camera",
            "description": "",
            "parameters": {"type": "object"},
        },
    ]
    assert session.initialize_calls == 1
    assert session.list_calls == [None, "page-2"]
    assert connector.connect_calls == [("http://127.0.0.1:8766/mcp", "secret", 30.0)]

    await transport.close()
    assert connector.close_calls == 1
    assert connector.enter_tasks == connector.exit_tasks


@pytest.mark.asyncio
async def test_mcp_transport_returns_structured_tool_results() -> None:
    """Structured MCP content should pass through as the model-visible result."""
    session = _FakeSession(
        results=[
            types.CallToolResult(
                content=[types.TextContent(type="text", text='{"status":"queued"}')],
                structuredContent={"status": "queued", "directions": ["left"]},
            )
        ]
    )
    transport = McpToolTransport(
        "http://127.0.0.1:8766/mcp",
        "secret",
        session_connector=_FakeConnector([session]),
    )

    result = await transport.call_tool("move_head", {"directions": ["left"]})

    assert result == {"status": "queued", "directions": ["left"]}
    assert session.tool_calls == [("move_head", {"directions": ["left"]})]
    await transport.close()


@pytest.mark.asyncio
async def test_mcp_transport_maps_proxy_denials() -> None:
    """An OpenShell MCP denial should become an actionable model tool result."""
    denial = McpError(types.ErrorData(code=-32000, message="Blocked by OpenShell policy: dance"))
    session = _FakeSession(results=[denial])
    transport = McpToolTransport(
        "http://127.0.0.1:8766/mcp",
        "secret",
        session_connector=_FakeConnector([session]),
    )

    result = await transport.call_tool("dance", {"move": "groovy_sway_and_roll", "repeat": 2})

    assert result == {
        "status": "policy_denied",
        "tool": "dance",
        "error": "Blocked by OpenShell policy",
    }


@pytest.mark.asyncio
async def test_mcp_transport_reconnects_next_call_without_retrying_failed_command() -> None:
    """An uncertain command should fail once; only a later request may reconnect."""
    disconnected = _FakeSession(results=[ConnectionError("connection lost")])
    reconnected = _FakeSession(
        results=[
            types.CallToolResult(
                content=[types.TextContent(type="text", text='{"status":"queued"}')],
                structuredContent={"status": "queued"},
            )
        ]
    )
    connector = _FakeConnector([disconnected, reconnected])
    transport = McpToolTransport(
        "http://127.0.0.1:8766/mcp",
        "secret",
        session_connector=connector,
    )

    failed = await transport.call_tool("move_head", {"directions": ["left"]})
    succeeded = await transport.call_tool("move_head", {"directions": ["front"]})

    assert failed == {
        "status": "mcp_unavailable",
        "error": "Reachy MCP server is unavailable",
    }
    assert succeeded == {"status": "queued"}
    assert disconnected.tool_calls == [("move_head", {"directions": ["left"]})]
    assert reconnected.tool_calls == [("move_head", {"directions": ["front"]})]
    assert len(connector.connect_calls) == 2
    await transport.close()


@pytest.mark.asyncio
async def test_mcp_discovery_reports_unavailable_server() -> None:
    """Startup discovery should fail clearly when the MCP server cannot be reached."""
    transport = McpToolTransport(
        "http://127.0.0.1:8766/mcp",
        "secret",
        session_connector=_FakeConnector([ConnectionError("refused")]),
    )

    with pytest.raises(McpTransportUnavailable, match="Reachy MCP server is unavailable"):
        await transport.list_tools()
