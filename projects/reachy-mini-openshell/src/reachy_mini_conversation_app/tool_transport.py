"""Tool transport abstraction and direct in-process implementation."""

from __future__ import annotations
import json
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable

from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    dispatch_tool_call,
    get_tool_specs_for_dependencies,
)
from reachy_mini_conversation_app.tools.tool_constants import SystemTool


CONVERSATION_LOCAL_TOOL_NAMES = frozenset(
    {
        "do_nothing",
        *(tool.value for tool in SystemTool),
    }
)


@runtime_checkable
class ToolTransport(Protocol):
    """Common interface for discovering and invoking conversation tools."""

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the tools available through this transport."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool and return its model-visible result."""
        ...

    async def close(self) -> None:
        """Release resources owned by the transport."""
        ...


class LocalToolTransport:
    """Invoke the existing Python tool registry in the conversation process."""

    def __init__(self, dependencies: ToolDependencies) -> None:
        """Bind the registry to the application's local hardware dependencies."""
        self._dependencies = dependencies

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return dependency-compatible local tool schemas."""
        return deepcopy(get_tool_specs_for_dependencies(self._dependencies))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool through the existing local Python implementation."""
        try:
            arguments_json = json.dumps(arguments)
        except (TypeError, ValueError) as exc:
            return {"error": f"Tool arguments are not JSON serializable: {exc}"}
        return await dispatch_tool_call(name, arguments_json, self._dependencies)

    async def close(self) -> None:
        """Close the transport; local dispatch owns no transport resources."""


class RoutedToolTransport:
    """Route an explicit local allowlist locally and every other tool remotely."""

    def __init__(
        self,
        *,
        remote: ToolTransport,
        local: ToolTransport,
        local_tool_names: frozenset[str] = CONVERSATION_LOCAL_TOOL_NAMES,
    ) -> None:
        """Configure the remote transport and local-only tool names."""
        self._remote = remote
        self._local = local
        self._local_tool_names = local_tool_names

    async def list_tools(self) -> list[dict[str, Any]]:
        """Merge remote schemas with only the explicitly permitted local schemas."""
        remote_tools = await self._remote.list_tools()
        local_tools = await self._local.list_tools()
        routed_remote_tools = [tool for tool in remote_tools if tool.get("name") not in self._local_tool_names]
        routed_local_tools = [tool for tool in local_tools if tool.get("name") in self._local_tool_names]
        return [*routed_remote_tools, *routed_local_tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke local-only tools locally and route all other names through MCP."""
        transport = self._local if name in self._local_tool_names else self._remote
        return await transport.call_tool(name, arguments)

    async def close(self) -> None:
        """Close both underlying transports even if one close operation fails."""
        try:
            await self._remote.close()
        finally:
            await self._local.close()
