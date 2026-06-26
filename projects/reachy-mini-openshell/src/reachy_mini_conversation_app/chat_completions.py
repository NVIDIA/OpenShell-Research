"""Chat Completions text/tool loop for the local-STT backend."""

import re
import json
import uuid
import asyncio
import logging
from typing import Any, Final, cast

from reachy_mini_conversation_app.prompts import get_session_instructions
from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    get_tool_specs,
    dispatch_tool_call_with_manager,
)
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)

_RATE_LIMIT_ATTEMPTS: Final[int] = 3
_RATE_LIMIT_DEFAULT_DELAY: Final[float] = 5.0
_RATE_LIMIT_MAX_DELAY: Final[float] = 30.0
_TOOL_ROUND_LIMIT: Final[int] = 5
_WAIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:please\s+)?wait\s+(\d+(?:\.\d+)?)\s+seconds?",
    re.IGNORECASE,
)


def chat_completion_tool_specs() -> list[dict[str, Any]]:
    """Convert Realtime-style tool specs to Chat Completions tool specs."""
    chat_tools: list[dict[str, Any]] = []
    for tool in get_tool_specs():
        if tool.get("type") != "function":
            continue
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
        )
    return chat_tools


def _tool_call_value(tool_call: Any, name: str) -> Any:
    """Read a value from either an SDK model object or a dict."""
    if isinstance(tool_call, dict):
        return tool_call.get(name)
    return getattr(tool_call, name, None)


def _message_value(message: Any, name: str) -> Any:
    """Read a chat message value from either an SDK model object or a dict."""
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _choice_message(choice: Any) -> Any:
    """Read a Chat Completions choice message from either an object or a dict."""
    if isinstance(choice, dict):
        return choice.get("message")
    return getattr(choice, "message", None)


def _tool_call_function_value(tool_call: Any, name: str) -> Any:
    """Read a function value from either an SDK tool-call object or a dict."""
    function = _tool_call_value(tool_call, "function")
    if isinstance(function, dict):
        return function.get(name)
    return getattr(function, name, None)


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    """Convert a tool call into the dict shape Chat Completions expects."""
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump()
    if isinstance(tool_call, dict):
        return tool_call
    return {
        "id": getattr(tool_call, "id", None),
        "type": getattr(tool_call, "type", "function"),
        "function": {
            "name": getattr(getattr(tool_call, "function", None), "name", None),
            "arguments": getattr(getattr(tool_call, "function", None), "arguments", "{}"),
        },
    }


def _rate_limit_delay(exc: Exception) -> float | None:
    """Return a retry delay when a Chat Completions error is a provider rate limit."""
    status_code = getattr(exc, "status_code", None)
    message = str(exc)
    is_rate_limit = (
        status_code == 429
        or "ratelimit" in type(exc).__name__.lower()
        or "rate limit" in message.lower()
        or "throttling" in message.lower()
    )
    if not is_rate_limit:
        return None

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _RATE_LIMIT_MAX_DELAY)
            except (TypeError, ValueError):
                pass

    match = _WAIT_RE.search(message)
    if match:
        try:
            return min(float(match.group(1)), _RATE_LIMIT_MAX_DELAY)
        except (TypeError, ValueError):
            pass

    return _RATE_LIMIT_DEFAULT_DELAY


class ChatCompletionRunner:
    """Run a Chat Completions turn, including Reachy tool calls."""

    def __init__(
        self,
        *,
        client: Any,
        deps: ToolDependencies,
        tool_manager: BackgroundToolManager,
        model_name: str,
        base_url: str | None,
    ) -> None:
        """Initialize the runner."""
        self.client = client
        self.deps = deps
        self.tool_manager = tool_manager
        self.model_name = model_name
        self.base_url = base_url

    async def create_with_retries(self, operation: str, **kwargs: Any) -> Any:
        """Create a Chat Completions response, retrying short provider rate limits."""
        for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                delay = _rate_limit_delay(e)
                if delay is None or attempt == _RATE_LIMIT_ATTEMPTS:
                    raise

                logger.warning(
                    "%s rate-limited by provider; retrying in %.1fs (%d/%d)",
                    operation,
                    delay,
                    attempt,
                    _RATE_LIMIT_ATTEMPTS,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("unreachable Chat Completions retry state")

    async def send_text_message(self, text: str) -> list[dict[str, Any]]:
        """Send a user text turn through Chat Completions and Reachy tools."""
        chatbot_messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        chat_messages: list[dict[str, Any]] = [
            {"role": "system", "content": get_session_instructions()},
            {"role": "user", "content": text},
        ]
        chat_tools = chat_completion_tool_specs()

        operation = "Chat Completions request"
        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": cast(Any, chat_messages),
            "tools": cast(Any, chat_tools),
            "tool_choice": "auto",
        }

        for tool_round in range(_TOOL_ROUND_LIMIT + 1):
            try:
                completion = await self.create_with_retries(operation, **request_kwargs)
            except Exception as e:
                logger.exception("%s failed", operation)
                chatbot_messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"[error] {operation} failed "
                            f"(model={self.model_name!r}, base_url={self.base_url!r}): "
                            f"{type(e).__name__}: {e}"
                        ),
                    }
                )
                return chatbot_messages

            choice = completion.choices[0] if completion.choices else None
            assistant_message = _choice_message(choice) if choice else None
            assistant_content = _message_value(assistant_message, "content") or ""
            tool_calls = _message_value(assistant_message, "tool_calls") or []

            if not tool_calls:
                chatbot_messages.append({"role": "assistant", "content": assistant_content or "[no response]"})
                return chatbot_messages

            if tool_round >= _TOOL_ROUND_LIMIT:
                chatbot_messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"[error] Chat Completions exceeded the configured tool round limit ({_TOOL_ROUND_LIMIT})."
                        ),
                    }
                )
                return chatbot_messages

            chat_messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": [_serialize_tool_call(tool_call) for tool_call in tool_calls],
                }
            )

            for tool_call in tool_calls:
                tool_call_id = _tool_call_value(tool_call, "id") or str(uuid.uuid4())
                tool_name = _tool_call_function_value(tool_call, "name")
                args_json = _tool_call_function_value(tool_call, "arguments") or "{}"
                if not isinstance(tool_name, str):
                    tool_result = {"error": "tool call did not include a valid function name"}
                else:
                    tool_result = await dispatch_tool_call_with_manager(
                        tool_name,
                        args_json,
                        self.deps,
                        self.tool_manager,
                    )

                tool_result_json = json.dumps(tool_result)
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_result_json,
                    }
                )
                chatbot_messages.append(
                    {
                        "role": "assistant",
                        "content": tool_result_json,
                        "metadata": {
                            "title": f"Used tool {tool_name or 'unknown'}",
                            "status": "done",
                        },
                    }
                )

            operation = "Chat Completions tool follow-up"
            request_kwargs = {
                "model": self.model_name,
                "messages": cast(Any, chat_messages),
                "tools": cast(Any, chat_tools),
            }

        raise RuntimeError("unreachable Chat Completions tool round state")
