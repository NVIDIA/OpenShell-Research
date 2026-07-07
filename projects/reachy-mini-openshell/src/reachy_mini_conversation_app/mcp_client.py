"""Authenticated Streamable HTTP transport for Reachy MCP tools."""

from __future__ import annotations
import json
import asyncio
import logging
from copy import deepcopy
from typing import Any, Protocol, cast
from datetime import timedelta
from contextlib import AbstractAsyncContextManager, suppress, asynccontextmanager
from dataclasses import dataclass
from collections.abc import Callable, AsyncIterator

import httpx
from mcp import ClientSession, types
from mcp.shared.exceptions import McpError
from mcp.client.streamable_http import streamable_http_client


logger = logging.getLogger(__name__)

POLICY_DENIED_ERROR = "Blocked by OpenShell policy"
MCP_UNAVAILABLE_ERROR = "Reachy MCP server is unavailable"
_POLICY_DENIAL_MARKERS = ("policy_denied", "policy denied", "blocked by", "denied by", "not allowed")


class McpTransportError(RuntimeError):
    """Base error for MCP transport setup and discovery failures."""


class McpTransportUnavailable(McpTransportError):
    """Raised when MCP discovery cannot reach or initialize the server."""


class _McpSession(Protocol):
    """Subset of ClientSession used by the transport and its tests."""

    async def initialize(self) -> types.InitializeResult:
        """Initialize the MCP session."""
        ...

    async def list_tools(self, *, cursor: str | None = None) -> types.ListToolsResult:
        """List one page of MCP tools."""
        ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> types.CallToolResult:
        """Call an MCP tool."""
        ...


SessionConnector = Callable[[str, str, float], AbstractAsyncContextManager[_McpSession]]


@dataclass
class _OwnerRequest:
    """One operation executed by the task that owns the MCP context managers."""

    operation: str
    future: asyncio.Future[Any]
    name: str | None = None
    arguments: dict[str, Any] | None = None


@asynccontextmanager
async def _streamable_http_session(
    url: str,
    token: str,
    request_timeout_seconds: float,
) -> AsyncIterator[ClientSession]:
    """Open one initialized-capable MCP session with bearer authentication."""
    timeout = httpx.Timeout(request_timeout_seconds, read=max(300.0, request_timeout_seconds))
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=request_timeout_seconds),
            ) as session:
                yield session


class McpToolTransport:
    """Discover and invoke Reachy tools through an authenticated MCP server."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        request_timeout_seconds: float = 30.0,
        session_connector: SessionConnector | None = None,
    ) -> None:
        """Configure the remote endpoint without connecting until first use."""
        if not url.strip():
            raise ValueError("REACHY_MCP_URL must be set")
        if not token.strip():
            raise ValueError("REACHY_MCP_TOKEN must be set")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")

        self._url = url
        self._token = token
        self._request_timeout_seconds = request_timeout_seconds
        self._session_connector = session_connector or cast(SessionConnector, _streamable_http_session)
        self._owner_queue: asyncio.Queue[_OwnerRequest] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._tool_cache: list[dict[str, Any]] | None = None
        self._operation_lock = asyncio.Lock()
        self._closed = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover MCP tools once and cache their OpenAI-compatible schemas."""
        async with self._operation_lock:
            if self._tool_cache is not None:
                return deepcopy(self._tool_cache)

            try:
                tools = await self._request("list_tools")
            except Exception as exc:
                if isinstance(exc, McpTransportError):
                    raise
                raise McpTransportUnavailable(MCP_UNAVAILABLE_ERROR) from exc

            if not isinstance(tools, list):
                raise McpTransportError("MCP tools/list returned an invalid result")
            self._tool_cache = [self._to_openai_tool(tool) for tool in tools]
            return deepcopy(self._tool_cache)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one remote tool without retrying a command whose outcome is uncertain."""
        async with self._operation_lock:
            try:
                result = await self._request("call_tool", name=name, arguments=arguments)
            except Exception as exc:
                if _is_policy_denial(exc):
                    return _policy_denied_result(name)
                logger.warning("Reachy MCP call failed for %s: %s", name, type(exc).__name__)
                return {"status": "mcp_unavailable", "error": MCP_UNAVAILABLE_ERROR}

            if not isinstance(result, types.CallToolResult):
                return {"status": "tool_error", "tool": name, "error": "MCP returned an invalid tool result"}
            return _tool_result(name, result)

    async def close(self) -> None:
        """Close the live MCP session and prevent later reconnection."""
        async with self._operation_lock:
            self._closed = True
            self._tool_cache = None
            await self._stop_owner()

    async def _ensure_owner(self) -> None:
        if self._closed:
            raise McpTransportUnavailable("MCP tool transport is closed")
        if self._owner_task is not None and not self._owner_task.done():
            return
        if self._owner_task is not None:
            await self._reset_owner()

        queue: asyncio.Queue[_OwnerRequest] = asyncio.Queue()
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        owner_task = asyncio.create_task(self._run_session_owner(queue, ready), name="reachy-mcp-session")
        self._owner_queue = queue
        self._owner_task = owner_task
        try:
            await ready
        except Exception as exc:
            await self._reset_owner()
            if _is_policy_denial(exc):
                raise McpTransportError(POLICY_DENIED_ERROR) from exc
            raise McpTransportUnavailable(MCP_UNAVAILABLE_ERROR) from exc

    async def _request(
        self,
        operation: str,
        *,
        name: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_owner()
        queue = self._owner_queue
        owner_task = self._owner_task
        if queue is None or owner_task is None:
            raise McpTransportUnavailable(MCP_UNAVAILABLE_ERROR)

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await queue.put(_OwnerRequest(operation, future, name, arguments))
        if owner_task.done() and not future.done():
            future.set_exception(McpTransportUnavailable(MCP_UNAVAILABLE_ERROR))
        try:
            return await future
        except asyncio.CancelledError:
            await self._cancel_owner()
            raise
        except Exception:
            await self._reset_owner()
            raise

    async def _run_session_owner(
        self,
        queue: asyncio.Queue[_OwnerRequest],
        ready: asyncio.Future[None],
    ) -> None:
        """Own the MCP context so setup, calls, and cleanup happen in one task."""
        terminal_error: BaseException | None = None
        try:
            async with self._session_connector(
                self._url,
                self._token,
                self._request_timeout_seconds,
            ) as session:
                await session.initialize()
                if not ready.done():
                    ready.set_result(None)

                while True:
                    request = await queue.get()
                    if request.operation == "close":
                        if not request.future.done():
                            request.future.set_result(None)
                        return
                    try:
                        if request.operation == "list_tools":
                            result: Any = await self._list_all_tools(session)
                        elif request.operation == "call_tool" and request.name is not None:
                            result = await session.call_tool(request.name, request.arguments)
                        else:
                            raise McpTransportError(f"Unknown MCP transport operation: {request.operation}")
                    except BaseException as exc:
                        terminal_error = exc
                        if not request.future.done():
                            request.future.set_exception(exc)
                        return
                    if not request.future.done():
                        request.future.set_result(result)
        except BaseException as exc:
            terminal_error = exc
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if not ready.done():
                ready.set_exception(terminal_error or McpTransportUnavailable(MCP_UNAVAILABLE_ERROR))
            pending_error = terminal_error or McpTransportUnavailable(MCP_UNAVAILABLE_ERROR)
            while not queue.empty():
                pending = queue.get_nowait()
                if not pending.future.done():
                    pending.future.set_exception(pending_error)

    async def _stop_owner(self) -> None:
        owner_task = self._owner_task
        queue = self._owner_queue
        if owner_task is not None and not owner_task.done() and queue is not None:
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            await queue.put(_OwnerRequest("close", future))
            if owner_task.done() and not future.done():
                future.set_result(None)
            with suppress(BaseException):
                await future
        await self._reset_owner()

    async def _cancel_owner(self) -> None:
        owner_task = self._owner_task
        self._owner_task = None
        self._owner_queue = None
        if owner_task is not None and not owner_task.done():
            owner_task.cancel()
        if owner_task is not None:
            with suppress(BaseException):
                await owner_task

    async def _reset_owner(self) -> None:
        owner_task = self._owner_task
        self._owner_task = None
        self._owner_queue = None
        if owner_task is not None:
            with suppress(BaseException):
                await owner_task

    @staticmethod
    async def _list_all_tools(session: _McpSession) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                return tools

    @staticmethod
    def _to_openai_tool(tool: types.Tool) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description or "",
            "parameters": deepcopy(tool.inputSchema),
        }


def _tool_result(name: str, result: types.CallToolResult) -> dict[str, Any]:
    text = _result_text(result)
    if result.isError:
        if _is_policy_denial(text):
            return _policy_denied_result(name)
        return {
            "status": "tool_error",
            "tool": name,
            "error": text or f"MCP tool {name!r} failed",
        }

    if result.structuredContent is not None:
        return deepcopy(result.structuredContent)

    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"result": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _result_text(result: types.CallToolResult) -> str:
    texts = [block.text for block in result.content if isinstance(block, types.TextContent)]
    return "\n".join(texts).strip()


def _is_policy_denial(error: object) -> bool:
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) == 403:
        return True

    parts = [str(error)]
    if isinstance(error, McpError):
        parts.extend((str(error.error.message), str(error.error.data or "")))
    message = " ".join(parts).lower()
    return any(marker in message for marker in _POLICY_DENIAL_MARKERS)


def _policy_denied_result(name: str) -> dict[str, Any]:
    return {
        "status": "policy_denied",
        "tool": name,
        "error": POLICY_DENIED_ERROR,
    }
