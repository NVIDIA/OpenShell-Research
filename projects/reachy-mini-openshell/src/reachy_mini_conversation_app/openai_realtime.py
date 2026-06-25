import json
import uuid
import base64
import random
import asyncio
import logging
from typing import Any, Final, Tuple, Literal, Optional, cast
from datetime import datetime

import cv2
import numpy as np
import gradio as gr
from openai import AsyncOpenAI
from fastrtc import AdditionalOutputs, AsyncStreamHandler, wait_for_item, audio_to_int16
from numpy.typing import NDArray
from scipy.signal import resample
from websockets.exceptions import ConnectionClosedError

from reachy_mini_conversation_app.config import LOCKED_PROFILE, config
from reachy_mini_conversation_app.prompts import get_session_voice, get_session_instructions
from reachy_mini_conversation_app.riva_asr import RivaAsrConfig, RivaStreamingTranscriber
from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    get_tool_specs,
    dispatch_tool_call_with_manager,
)
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


logger = logging.getLogger(__name__)

OPEN_AI_INPUT_SAMPLE_RATE: Final[Literal[24000]] = 24000
OPEN_AI_OUTPUT_SAMPLE_RATE: Final[Literal[24000]] = 24000
AUDIO_MODE_OPENAI_REALTIME: Final = "openai_realtime"
AUDIO_MODE_RIVA_STT: Final = "riva_stt"
AUDIO_MODE_TEXT: Final = "text"
AUDIO_INPUT_MODES: Final = {AUDIO_MODE_OPENAI_REALTIME, AUDIO_MODE_RIVA_STT, AUDIO_MODE_TEXT}

# Cost tracking from usage data (pricing as of Feb 2026 https://openai.com/api/pricing/)
AUDIO_INPUT_COST_PER_1M = 32.0
AUDIO_OUTPUT_COST_PER_1M = 64.0
TEXT_INPUT_COST_PER_1M = 4.0
TEXT_OUTPUT_COST_PER_1M = 16.0
IMAGE_INPUT_COST_PER_1M = 5.0

_RESPONSE_DONE_TIMEOUT: Final[float] = 30.0


def _normalize_audio_input_mode(audio_input_mode: str) -> str:
    """Normalize configured audio input mode."""
    normalized = audio_input_mode.strip().lower().replace("-", "_")
    if normalized in {"openai", "realtime", "microphone"}:
        return AUDIO_MODE_OPENAI_REALTIME
    if normalized in {"riva", "riva_asr", "riva_stt"}:
        return AUDIO_MODE_RIVA_STT
    if normalized in {"chat", "chat_completions", "text"}:
        return AUDIO_MODE_TEXT
    if normalized not in AUDIO_INPUT_MODES:
        logger.warning("Unknown AUDIO_INPUT_MODE=%r; using %s", audio_input_mode, AUDIO_MODE_OPENAI_REALTIME)
        return AUDIO_MODE_OPENAI_REALTIME
    return normalized


def _compute_response_cost(usage: Any) -> float:
    """Compute dollar cost from a response usage object."""
    inp = getattr(usage, "input_token_details", None)
    out = getattr(usage, "output_token_details", None)
    cost = 0.0
    if inp:
        cost += (getattr(inp, "audio_tokens", 0) or 0) * AUDIO_INPUT_COST_PER_1M / 1e6
        cost += (getattr(inp, "text_tokens", 0) or 0) * TEXT_INPUT_COST_PER_1M / 1e6
        cost += (getattr(inp, "image_tokens", 0) or 0) * IMAGE_INPUT_COST_PER_1M / 1e6
    if out:
        cost += (getattr(out, "audio_tokens", 0) or 0) * AUDIO_OUTPUT_COST_PER_1M / 1e6
        cost += (getattr(out, "text_tokens", 0) or 0) * TEXT_OUTPUT_COST_PER_1M / 1e6
    return cost


class OpenaiRealtimeHandler(AsyncStreamHandler):
    """An OpenAI realtime handler for fastrtc Stream."""

    def __init__(
        self,
        deps: ToolDependencies,
        gradio_mode: bool = False,
        instance_path: Optional[str] = None,
        audio_input_mode: str | None = None,
        audio_input_mode_state: dict[str, str] | None = None,
    ):
        """Initialize the handler."""
        super().__init__(
            expected_layout="mono",
            output_sample_rate=OPEN_AI_OUTPUT_SAMPLE_RATE,
            input_sample_rate=OPEN_AI_INPUT_SAMPLE_RATE,
        )

        # Override typing of the sample rates to match OpenAI's requirements
        self.output_sample_rate: Literal[24000] = self.output_sample_rate
        self.input_sample_rate: Literal[24000] = self.input_sample_rate

        self.deps = deps
        self._audio_input_mode_state = audio_input_mode_state or {
            "mode": _normalize_audio_input_mode(audio_input_mode or config.AUDIO_INPUT_MODE)
        }

        # Override type annotations for OpenAI strict typing (only for values used in API)
        self.output_sample_rate = OPEN_AI_OUTPUT_SAMPLE_RATE
        self.input_sample_rate = OPEN_AI_INPUT_SAMPLE_RATE

        self.connection: Any = None
        self.output_queue: "asyncio.Queue[Tuple[int, NDArray[np.int16]] | AdditionalOutputs]" = asyncio.Queue()

        self.last_activity_time = asyncio.get_event_loop().time()
        self.start_time = asyncio.get_event_loop().time()
        self.is_idle_tool_call = False
        self.gradio_mode = gradio_mode
        self.instance_path = instance_path
        self._riva_transcriber: RivaStreamingTranscriber | None = None

        # Debouncing for partial transcripts
        self.partial_transcript_task: asyncio.Task[None] | None = None
        self.partial_transcript_sequence: int = 0  # sequence counter to prevent stale emissions
        self.partial_debounce_delay = 0.5  # seconds

        # Internal lifecycle flags
        self._shutdown_requested: bool = False
        self._connected_event: asyncio.Event = asyncio.Event()
        self._realtime_startup_task: asyncio.Task[None] | None = None
        self._startup_error: str | None = None
        self._microphone_error_reported: bool = False

        # Background tool manager
        self.tool_manager = BackgroundToolManager()

        # Cost tracking
        self.cumulative_cost: float = 0.0

        # Response-in-progress guard: the Realtime API only allows one active
        # response per conversation at a time.  A dedicated worker task
        # (_response_sender_loop) dequeues and sends one request at a time
        self._pending_responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._response_done_event: asyncio.Event = asyncio.Event()
        self._response_done_event.set()
        self._last_response_rejected: bool = False

    def copy(self) -> "OpenaiRealtimeHandler":
        """Create a copy of the handler."""
        return OpenaiRealtimeHandler(
            self.deps,
            self.gradio_mode,
            self.instance_path,
            audio_input_mode_state=self._audio_input_mode_state,
        )

    @property
    def audio_input_mode(self) -> str:
        """Return the current shared microphone audio mode."""
        return self._audio_input_mode_state["mode"]

    def set_audio_input_mode(self, audio_input_mode: str) -> None:
        """Select how microphone audio should be processed."""
        self._audio_input_mode_state["mode"] = _normalize_audio_input_mode(audio_input_mode)

    def _record_startup_error(self, message: str) -> None:
        """Store a startup failure so the UI can show a useful error."""
        self._startup_error = message
        try:
            self._connected_event.set()
        except Exception:
            pass

    def _realtime_context(self) -> str:
        """Return non-secret Realtime configuration for diagnostics."""
        return f"model={config.MODEL_NAME!r}, base_url={config.OPENAI_BASE_URL!r}"

    def _provider_realtime_hint(self) -> str:
        """Return a provider-specific hint for common compatibility failures."""
        base_url = str(config.OPENAI_BASE_URL)
        if "integrate.api.nvidia.com" in base_url or "inference-api.nvidia.com" in base_url:
            return (
                " NVIDIA OpenAI-compatible chat endpoints use Chat Completions; "
                "microphone mode requires an OpenAI-compatible Realtime API endpoint."
            )
        return ""

    def _not_connected_message(self) -> str:
        """Return the user-facing reason the Realtime session is unavailable."""
        if self._startup_error:
            return f"[error] {self._startup_error}"
        return (
            "[error] Realtime session is not connected. Check OPENAI_API_KEY, "
            "OPENAI_BASE_URL, and MODEL_NAME in .env."
        )

    @staticmethod
    def _text_model_uses_realtime() -> bool:
        """Return whether typed messages should use the Realtime transport."""
        return "realtime" in str(config.MODEL_NAME).lower()

    @staticmethod
    def _chat_completion_tool_specs() -> list[dict[str, Any]]:
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

    @staticmethod
    def _tool_call_value(tool_call: Any, name: str) -> Any:
        """Read a value from either an SDK model object or a dict."""
        if isinstance(tool_call, dict):
            return tool_call.get(name)
        return getattr(tool_call, name, None)

    @classmethod
    def _tool_call_function_value(cls, tool_call: Any, name: str) -> Any:
        """Read a function value from either an SDK tool-call object or a dict."""
        function = cls._tool_call_value(tool_call, "function")
        if isinstance(function, dict):
            return function.get(name)
        return getattr(function, name, None)

    @staticmethod
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

    def _openai_api_key_or_error(self) -> str | None:
        """Return the configured API key or record a useful startup error."""
        openai_api_key = (config.OPENAI_API_KEY or "").strip()
        if openai_api_key:
            return openai_api_key

        message = (
            "OPENAI_API_KEY is missing or empty after reading .env. Add it to .env and restart the "
            "conversation app. If .env uses OPENAI_API_KEY=${NVIDIA_API_KEY}, make sure "
            "NVIDIA_API_KEY is exported in the shell that starts the app."
        )
        self._record_startup_error(message)
        logger.error(message)
        return None

    async def _ensure_realtime_session(self, task_name: str) -> bool:
        """Start a Realtime session for Gradio text or microphone input if needed."""
        if self.connection is not None:
            return True

        if self._realtime_startup_task is None or self._realtime_startup_task.done():
            self._startup_error = None
            try:
                self._connected_event.clear()
            except Exception:
                pass
            self._realtime_startup_task = asyncio.create_task(self.start_up(), name=task_name)

        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for Realtime session startup")

        return self.connection is not None

    async def _ensure_text_session(self) -> bool:
        """Start a Realtime session for text-only Gradio input if needed."""
        return await self._ensure_realtime_session("openai-realtime-text")

    async def _report_microphone_error_once(self, message: str) -> None:
        """Show one visible microphone error instead of silently dropping every frame."""
        if self._microphone_error_reported:
            return
        self._microphone_error_reported = True
        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": message}))

    @staticmethod
    def _chat_message_from_output(output: AdditionalOutputs) -> dict[str, Any] | None:
        """Convert a FastRTC AdditionalOutputs payload into a chatbot message."""
        if not output.args:
            return None
        candidate = output.args[0]
        if not isinstance(candidate, dict):
            return None
        role = candidate.get("role")
        content = candidate.get("content")
        if role == "user_partial" or not isinstance(role, str) or content is None:
            return None
        return candidate

    async def send_text_message(self, message: str, timeout: float = 30.0) -> list[dict[str, Any]]:
        """Send a typed user message and collect chat updates."""
        text = message.strip()
        if not text:
            return []

        if not self._text_model_uses_realtime():
            return await self._send_chat_completion_text_message(text)

        if not await self._ensure_text_session():
            return [
                {
                    "role": "assistant",
                    "content": self._not_connected_message(),
                },
            ]

        await self.connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        )
        await self._safe_response_create()

        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        saw_assistant_message = False
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            try:
                output = await asyncio.wait_for(self.output_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if saw_assistant_message and self._response_done_event.is_set():
                    break
                continue

            if not isinstance(output, AdditionalOutputs):
                continue

            chat_message = self._chat_message_from_output(output)
            if chat_message is None:
                continue

            messages.append(chat_message)
            if chat_message.get("role") == "assistant":
                saw_assistant_message = True

            if saw_assistant_message and self._response_done_event.is_set():
                break

        if not saw_assistant_message:
            messages.append({"role": "assistant", "content": "[error] Timed out waiting for a response."})

        return messages

    async def _send_chat_completion_text_message(self, text: str) -> list[dict[str, Any]]:
        """Send a typed message through Chat Completions for non-Realtime models."""
        openai_api_key = self._openai_api_key_or_error()
        if openai_api_key is None:
            return [{"role": "assistant", "content": self._not_connected_message()}]

        if getattr(self, "client", None) is None:
            self.client = AsyncOpenAI(
                api_key=openai_api_key,
                base_url=config.OPENAI_BASE_URL,
            )

        chatbot_messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        chat_messages: list[dict[str, Any]] = [
            {"role": "system", "content": get_session_instructions()},
            {"role": "user", "content": text},
        ]
        chat_tools = self._chat_completion_tool_specs()

        try:
            completion = await self.client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=cast(Any, chat_messages),
                tools=cast(Any, chat_tools),
                tool_choice="auto",
            )
        except Exception as e:
            logger.exception("Chat Completions request failed")
            return [
                *chatbot_messages,
                {
                    "role": "assistant",
                    "content": (
                        f"[error] Chat Completions request failed "
                        f"(model={config.MODEL_NAME!r}, base_url={config.OPENAI_BASE_URL!r}): "
                        f"{type(e).__name__}: {e}"
                    ),
                },
            ]

        choice = completion.choices[0] if completion.choices else None
        assistant_message = getattr(choice, "message", None) if choice else None
        assistant_content = getattr(assistant_message, "content", None) or ""
        tool_calls = getattr(assistant_message, "tool_calls", None) or []

        if not tool_calls:
            chatbot_messages.append({"role": "assistant", "content": assistant_content or "[no response]"})
            return chatbot_messages

        chat_messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [self._serialize_tool_call(tool_call) for tool_call in tool_calls],
            }
        )

        for tool_call in tool_calls:
            tool_call_id = self._tool_call_value(tool_call, "id") or str(uuid.uuid4())
            tool_name = self._tool_call_function_value(tool_call, "name")
            args_json = self._tool_call_function_value(tool_call, "arguments") or "{}"
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

        try:
            follow_up = await self.client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=cast(Any, chat_messages),
                tools=cast(Any, chat_tools),
            )
        except Exception as e:
            logger.exception("Chat Completions tool follow-up failed")
            chatbot_messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"[error] Chat Completions tool follow-up failed "
                        f"(model={config.MODEL_NAME!r}, base_url={config.OPENAI_BASE_URL!r}): "
                        f"{type(e).__name__}: {e}"
                    ),
                }
            )
            return chatbot_messages

        follow_up_choice = follow_up.choices[0] if follow_up.choices else None
        follow_up_message = getattr(follow_up_choice, "message", None) if follow_up_choice else None
        follow_up_content = getattr(follow_up_message, "content", None) or "Done."
        chatbot_messages.append({"role": "assistant", "content": follow_up_content})
        return chatbot_messages

    async def _emit_debounced_partial(self, transcript: str, sequence: int) -> None:
        """Emit partial transcript after debounce delay."""
        try:
            await asyncio.sleep(self.partial_debounce_delay)
            # Only emit if this is still the latest partial (by sequence number)
            if self.partial_transcript_sequence == sequence:
                await self.output_queue.put(AdditionalOutputs({"role": "user_partial", "content": transcript}))
                logger.debug(f"Debounced partial emitted: {transcript}")
        except asyncio.CancelledError:
            logger.debug("Debounced partial cancelled")
            raise

    async def start_up(self) -> None:
        """Start the handler with minimal retries on unexpected websocket closure."""
        openai_api_key = self._openai_api_key_or_error()
        if openai_api_key is None:
            return

        self.client = AsyncOpenAI(
            api_key=openai_api_key,
            base_url=config.OPENAI_BASE_URL,
        )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self._run_realtime_session()
                # Normal exit from the session, stop retrying
                return
            except ConnectionClosedError as e:
                # Abrupt close (e.g., "no close frame received or sent") → retry
                logger.warning("Realtime websocket closed unexpectedly (attempt %d/%d): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    # exponential backoff with jitter
                    base_delay = 2 ** (attempt - 1)  # 1s, 2s, 4s, 8s, etc.
                    jitter = random.uniform(0, 0.5)
                    delay = base_delay + jitter
                    logger.info("Retrying in %.1f seconds...", delay)
                    await asyncio.sleep(delay)
                    continue
                message = (
                    f"Realtime websocket closed before startup completed ({self._realtime_context()}): "
                    f"{type(e).__name__}: {e}.{self._provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.error(message)
                return
            except Exception as e:
                message = (
                    f"Realtime startup failed ({self._realtime_context()}): "
                    f"{type(e).__name__}: {e}.{self._provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.exception(message)
                return
            finally:
                # never keep a stale reference
                self.connection = None
                try:
                    if self._startup_error is None:
                        self._connected_event.clear()
                    else:
                        self._connected_event.set()
                except Exception:
                    pass

    async def _restart_session(self) -> None:
        """Force-close the current session and start a fresh one in background.

        Does not block the caller while the new session is establishing.
        """
        try:
            if self.connection is not None:
                try:
                    await self.connection.close()
                except Exception:
                    pass
                finally:
                    self.connection = None

            # Ensure we have a client (start_up must have run once)
            if getattr(self, "client", None) is None:
                logger.warning("Cannot restart: OpenAI client not initialized yet.")
                return

            # Fire-and-forget new session and wait briefly for connection
            try:
                self._connected_event.clear()
            except Exception:
                pass
            asyncio.create_task(self._run_realtime_session(), name="openai-realtime-restart")
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
                logger.info("Realtime session restarted and connected.")
            except asyncio.TimeoutError:
                logger.warning("Realtime session restart timed out; continuing in background.")
        except Exception as e:
            logger.warning("_restart_session failed: %s", e)

    async def _safe_response_create(self, **kwargs: Any) -> None:
        """Enqueue a response.create() kwargs for the sender worker _response_sender_loop().

        This method never blocks the caller.
        """
        await self._pending_responses.put(kwargs)

    async def _response_sender_loop(self) -> None:
        """Dedicated worker that sends ``response.create()`` calls serially.

        This logic was designed to comply with the response.create() docstring specification for event ordering:
        https://github.com/openai/openai-python/blob/3e0c05b84a2056870abf3bd6a5e7849020209cc3/src/openai/resources/realtime/realtime.py#L649C1-L651C30

        For each queued request the worker:
        1. Waits until no response is active (_response_done_event).
        2. Sends response.create().
        3. Waits for the response cycle to complete (response.done).
        4. If the server rejected with active_response, retries from step 1.
        """
        while self.connection:
            try:
                kwargs = await self._pending_responses.get()
            except asyncio.CancelledError:
                return

            sent = False
            max_retries = 5
            attempts = 0
            while not sent and self.connection and attempts < max_retries:
                try:
                    await asyncio.wait_for(self._response_done_event.wait(), timeout=_RESPONSE_DONE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for previous response to finish; forcing ahead")
                    self._response_done_event.set()

                if not self.connection:
                    break

                self._last_response_rejected = False
                try:
                    # Mark the just-requested response as active immediately.
                    # The server's response.created event can arrive after
                    # response.create() returns, so waiting on the old set
                    # event here would let the next queued response race ahead.
                    self._response_done_event.clear()
                    await self.connection.response.create(**kwargs)
                except Exception as e:
                    logger.debug("_response_sender_loop: send failed: %s", e)
                    self._response_done_event.set()
                    break

                try:
                    await asyncio.wait_for(self._response_done_event.wait(), timeout=_RESPONSE_DONE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for response.done; assuming response completed")
                    self._response_done_event.set()
                    break

                # Check if we were rejected
                if self._last_response_rejected:
                    attempts += 1
                    if attempts >= max_retries:
                        logger.debug("response.create rejected %d times; giving up", attempts)
                        break
                    logger.debug("response.create was rejected; retrying (%d/%d)", attempts, max_retries)
                    continue

                sent = True

    async def _handle_tool_result(self, bg_tool: ToolNotification) -> None:
        """Process the result of a tool call."""
        if bg_tool.error is not None:
            logger.error("Tool '%s' (id=%s) failed with error: %s", bg_tool.tool_name, bg_tool.id, bg_tool.error)
            tool_result = {"error": bg_tool.error}
        elif bg_tool.result is not None:
            tool_result = bg_tool.result
            logger.info(
                "Tool '%s' (id=%s) executed successfully.",
                bg_tool.tool_name, bg_tool.id,
            )
            logger.debug("Tool '%s' full result: %s", bg_tool.tool_name, tool_result)
        else:
            logger.warning("Tool '%s' (id=%s) returned no result and no error", bg_tool.tool_name, bg_tool.id)
            tool_result = {"error": "No result returned from tool execution"}

        # Connection may have closed while tool was running
        if not self.connection:
            logger.warning("Connection closed during tool '%s' (id=%s) execution; cannot send result back", bg_tool.tool_name, bg_tool.id)
            return

        try:
            # Send the tool result back
            if isinstance(bg_tool.id, str):
                await self.connection.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": bg_tool.id,
                        "output": json.dumps(tool_result),
                    },
                )

            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "assistant",
                        "content": json.dumps(tool_result),
                        # Gradio UI metadata.status accept only "pending" and "done". Do not accept bg.tool.status values.
                        "metadata": {
                            "title": f"🛠️ Used tool {bg_tool.tool_name}",
                            "status": "done",
                        },
                    },
                ),
            )

            if bg_tool.tool_name == "camera" and "b64_im" in tool_result:
                # use raw base64, don't json.dumps (which adds quotes)
                b64_im = tool_result["b64_im"]
                if not isinstance(b64_im, str):
                    logger.warning("Unexpected type for b64_im: %s", type(b64_im))
                    b64_im = str(b64_im)
                await self.connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{b64_im}",
                            },
                        ],
                    },
                )
                logger.info("Added camera image to conversation")

                if self.deps.camera_worker is not None:
                    np_img = self.deps.camera_worker.get_latest_frame()
                    if np_img is not None:
                        # Camera frames are BGR from OpenCV; convert so Gradio displays correct colors.
                        rgb_frame = cv2.cvtColor(np_img, cv2.COLOR_BGR2RGB)
                    else:
                        rgb_frame = None
                    img = gr.Image(value=rgb_frame)

                    await self.output_queue.put(
                        AdditionalOutputs(
                            {
                                "role": "assistant",
                                "content": img,
                            },
                        ),
                    )

            # If this tool call was triggered by an idle signal, don't make the robot speak.
            # For other tool calls, let the robot reply out loud.
            if not bg_tool.is_idle_tool_call:
                await self._safe_response_create(
                    response={
                        "instructions": "Use the tool result just returned and answer concisely in speech.",
                    },
                )

            # Re-synchronize the head wobble after a tool call that may have taken some time
            if self.deps.head_wobbler is not None:
                self.deps.head_wobbler.reset()

        except ConnectionClosedError:
            logger.warning("Connection closed while sending tool result")
            self.connection = None
            self._response_done_event.set()

    async def _run_realtime_session(self) -> None:
        """Establish and manage a single realtime session."""
        async with self.client.realtime.connect(model=config.MODEL_NAME) as conn:
            try:
                await conn.session.update(
                    session={
                        "type": "realtime",
                        "instructions": get_session_instructions(),
                        "audio": {
                            "input": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": self.input_sample_rate,
                                },
                                "transcription": {"model": "gpt-4o-transcribe", "language": "en"},
                                "turn_detection": {
                                    "type": "server_vad",
                                    "interrupt_response": True,
                                },
                            },
                            "output": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": self.output_sample_rate,
                                },
                                "voice": get_session_voice(),
                            },
                        },
                        "tools": get_tool_specs(),  # type: ignore[typeddict-item]
                        "tool_choice": "auto",
                    },
                )
                logger.info(
                    "Realtime session initialized with locked_profile=%r voice=%r",
                    LOCKED_PROFILE,
                    get_session_voice(),
                )
            except Exception as e:
                message = (
                    f"Realtime session.update failed ({self._realtime_context()}): "
                    f"{type(e).__name__}: {e}.{self._provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.exception("Realtime session.update failed; aborting startup")
                return

            logger.info("Realtime session updated successfully")

            # Manage event received from the openai server
            self.connection = conn
            try:
                self._connected_event.set()
            except Exception:
                pass


            response_sender_task: asyncio.Task[None] | None = None
            try:
                # Start the background tool manager
                self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

                # Start the response sender worker
                response_sender_task = asyncio.create_task(
                    self._response_sender_loop(), name="response-sender"
                )

                async for event in self.connection:
                    logger.debug(f"OpenAI event: {event.type}")
                    if event.type == "input_audio_buffer.speech_started":
                        if hasattr(self, "_clear_queue") and callable(self._clear_queue):
                            self._clear_queue()
                        if self.deps.head_wobbler is not None:
                            self.deps.head_wobbler.reset()
                        self.deps.movement_manager.set_listening(True)
                        logger.debug("User speech started")

                    if event.type == "input_audio_buffer.speech_stopped":
                        self.deps.movement_manager.set_listening(False)
                        logger.debug("User speech stopped - server will auto-commit with VAD")

                    if event.type in (
                        "response.audio.done",
                        "response.output_audio.done",
                        "response.audio.completed",
                        "response.completed",
                    ):
                        logger.debug("response completed")

                    if event.type == "response.created":
                        self._response_done_event.clear()
                        logger.debug("Response created (active)")

                    if event.type == "response.done":
                        # Doesn't mean the audio is done playing
                        self._response_done_event.set()
                        logger.debug("Response done")

                        response = getattr(event, "response", None)
                        usage = getattr(response, "usage", None) if response else None
                        if usage:
                            cost = _compute_response_cost(usage)
                            self.cumulative_cost += cost
                            logger.debug("Cost: $%.4f | Cumulative: $%.4f", cost, self.cumulative_cost)
                        else:
                            logger.warning("No usage data available for cost tracking")

                    # Handle partial transcription (user speaking in real-time)
                    if event.type == "conversation.item.input_audio_transcription.partial":
                        logger.debug(f"User partial transcript: {event.transcript}")

                        # Increment sequence
                        self.partial_transcript_sequence += 1
                        current_sequence = self.partial_transcript_sequence

                        # Cancel previous debounce task if it exists
                        if self.partial_transcript_task and not self.partial_transcript_task.done():
                            self.partial_transcript_task.cancel()
                            try:
                                await self.partial_transcript_task
                            except asyncio.CancelledError:
                                pass

                        # Start new debounce timer with sequence number
                        self.partial_transcript_task = asyncio.create_task(
                            self._emit_debounced_partial(event.transcript, current_sequence)
                        )

                    # Handle completed transcription (user finished speaking)
                    if event.type == "conversation.item.input_audio_transcription.completed":
                        logger.debug(f"User transcript: {event.transcript}")

                        # Cancel any pending partial emission
                        if self.partial_transcript_task and not self.partial_transcript_task.done():
                            self.partial_transcript_task.cancel()
                            try:
                                await self.partial_transcript_task
                            except asyncio.CancelledError:
                                pass

                        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": event.transcript}))

                    # Handle assistant transcription
                    if event.type in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
                        logger.debug(f"Assistant transcript: {event.transcript}")
                        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": event.transcript}))

                    # Handle audio delta
                    if event.type in ("response.audio.delta", "response.output_audio.delta"):
                        if self.deps.head_wobbler is not None:
                            self.deps.head_wobbler.feed(event.delta)
                        self.last_activity_time = asyncio.get_event_loop().time()
                        logger.debug("last activity time updated to %s", self.last_activity_time)
                        await self.output_queue.put(
                            (
                                self.output_sample_rate,
                                np.frombuffer(base64.b64decode(event.delta), dtype=np.int16).reshape(1, -1),
                            ),
                        )

                    # ---- tool-calling plumbing ----
                    if event.type == "response.function_call_arguments.done":
                        tool_name = getattr(event, "name", None)
                        args_json_str = getattr(event, "arguments", None)
                        call_id: str = str(getattr(event, "call_id", uuid.uuid4()))

                        logger.info(
                            "Tool call received — tool_name=%r, call_id=%s, is_idle=%s, args=%s",
                            tool_name, call_id, self.is_idle_tool_call, args_json_str,
                        )

                        if not isinstance(tool_name, str) or not isinstance(args_json_str, str):
                            logger.error(
                                "Invalid tool call: tool_name=%s (type=%s), args=%s (type=%s), call_id=%s",
                                tool_name, type(tool_name).__name__,
                                args_json_str, type(args_json_str).__name__,
                                call_id,
                            )
                            continue

                        bg_tool = await self.tool_manager.start_tool(
                            call_id=call_id,
                            tool_call_routine=ToolCallRoutine(
                                tool_name=tool_name,
                                args_json_str=args_json_str,
                                deps=self.deps,
                            ),
                            is_idle_tool_call=self.is_idle_tool_call,
                        )

                        await self.output_queue.put(
                            AdditionalOutputs(
                                {
                                    "role": "assistant",
                                    "content": f"🛠️ Used tool {tool_name} with args {args_json_str}. The tool is now running. Tool ID: {bg_tool.tool_id}",
                                },
                            ),
                        )

                        if self.is_idle_tool_call:
                            self.is_idle_tool_call = False
                        else:
                            await self._safe_response_create(
                                response={
                                    "instructions": "Notify what the tool has been running giving meaningful information about the task",
                                },
                            )

                        logger.info("Started background tool: %s (id=%s, call_id=%s)", tool_name, bg_tool.tool_id, call_id)

                    # server error
                    if event.type == "error":
                        err = getattr(event, "error", None)
                        msg = getattr(err, "message", str(err) if err else "unknown error")
                        code = getattr(err, "code", "")

                        if code == "conversation_already_has_active_response":
                            # response.create was rejected.  The sender worker
                            # is waiting on _response_done_event; when the active
                            # response finishes it will wake up and see this flag.
                            self._last_response_rejected = True
                            logger.debug("response.create rejected; worker will retry after active response finishes")
                        else:
                            logger.error("Realtime error [%s]: %s (raw=%s)", code, msg, err)

                        # Only show user-facing errors, not internal state errors
                        if code not in ("input_audio_buffer_commit_empty",):
                            await self.output_queue.put(
                                AdditionalOutputs({"role": "assistant", "content": f"[error] {msg}"})
                            )
            finally:
                # Stop the response sender worker.
                if response_sender_task is not None:
                    response_sender_task.cancel()
                    try:
                        await response_sender_task
                    except asyncio.CancelledError:
                        pass

                # Stop background tool manager tasks (listener + cleanup) in all paths.
                await self.tool_manager.shutdown()

    # Microphone receive
    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive audio frame from the microphone and route it through the selected audio mode."""
        if self.audio_input_mode == AUDIO_MODE_RIVA_STT:
            await self._receive_riva_stt(frame)
            return
        if self.audio_input_mode == AUDIO_MODE_TEXT:
            return

        await self._receive_openai_realtime_audio(frame)

    async def _receive_openai_realtime_audio(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Send a microphone frame to the OpenAI Realtime audio session."""
        if not self.connection:
            if not self._text_model_uses_realtime():
                self._record_startup_error(
                    "OpenAI Realtime microphone mode requires an OpenAI-compatible Realtime model. "
                    f"Current MODEL_NAME={config.MODEL_NAME!r} uses the text path only; "
                    "switch Input to Riva STT/Text or configure a Realtime model for OpenAI Realtime mode."
                )
                await self._report_microphone_error_once(self._not_connected_message())
                return

            if not await self._ensure_realtime_session("openai-realtime-microphone"):
                await self._report_microphone_error_once(self._not_connected_message())
                return

        self._microphone_error_reported = False

        input_sample_rate, audio_frame = frame
        audio_frame = self._prepare_audio_frame(input_sample_rate, audio_frame, target_sample_rate=self.input_sample_rate)

        # Send to OpenAI (guard against races during reconnect)
        try:
            audio_message = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return

    async def _receive_riva_stt(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Send a microphone frame to Riva ASR, then route transcripts into the text LLM path."""
        input_sample_rate, audio_frame = frame
        audio_frame = self._prepare_audio_frame(input_sample_rate, audio_frame)

        if self._riva_transcriber is None:
            self._riva_transcriber = RivaStreamingTranscriber(
                config=RivaAsrConfig.from_env(),
                on_final_transcript=self._handle_riva_final_transcript,
                on_partial_transcript=self._handle_riva_partial_transcript,
            )
            try:
                await self._riva_transcriber.start(input_sample_rate)
            except RuntimeError as exc:
                self._riva_transcriber = None
                await self._report_microphone_error_once(f"[error] Riva STT stream is not available: {exc}")
                return

        try:
            if await self._riva_transcriber.send_audio(audio_frame.tobytes()) is False:
                self._riva_transcriber = None
                await self._report_microphone_error_once("[error] Riva STT stream is not running.")
        except RuntimeError as exc:
            logger.warning("Riva STT stream stopped: %s", exc)
            self._riva_transcriber = None
            await self._report_microphone_error_once(f"[error] Riva STT stream is not available: {exc}")

    def _prepare_audio_frame(
        self,
        input_sample_rate: int,
        audio_frame: NDArray[np.int16],
        *,
        target_sample_rate: int | None = None,
    ) -> NDArray[np.int16]:
        """Convert incoming audio to mono PCM16, optionally resampling first."""
        if audio_frame.ndim == 2:
            # Scipy channels last convention
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            # Multiple channels -> Mono channel
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        if target_sample_rate is not None and target_sample_rate != input_sample_rate:
            audio_frame = resample(audio_frame, int(len(audio_frame) * target_sample_rate / input_sample_rate))

        return audio_to_int16(audio_frame)

    async def _handle_riva_partial_transcript(self, transcript: str) -> None:
        await self.output_queue.put(AdditionalOutputs({"role": "user_partial", "content": transcript}))

    async def _handle_riva_final_transcript(self, transcript: str) -> None:
        if transcript.startswith("[error]"):
            await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": transcript}))
            return

        for message in await self.send_text_message(transcript):
            await self.output_queue.put(AdditionalOutputs(message))

    async def emit(self) -> Tuple[int, NDArray[np.int16]] | AdditionalOutputs | None:
        """Emit audio frame to be played by the speaker."""
        # sends to the stream the stuff put in the output queue by the openai event handler
        # This is called periodically by the fastrtc Stream

        # Handle idle
        idle_duration = asyncio.get_event_loop().time() - self.last_activity_time
        if idle_duration > 15.0 and self.deps.movement_manager.is_idle():
            try:
                await self.send_idle_signal(idle_duration)
            except Exception as e:
                logger.warning("Idle signal skipped (connection closed?): %s", e)
                return None

            self.last_activity_time = asyncio.get_event_loop().time()  # avoid repeated resets

        return await wait_for_item(self.output_queue)  # type: ignore[no-any-return]

    async def shutdown(self) -> None:
        """Shutdown the handler."""
        self._shutdown_requested = True

        # Unblock the response sender worker so it can exit
        self._response_done_event.set()

        # Stop background tool manager tasks (listener + cleanup)
        await self.tool_manager.shutdown()

        if self._riva_transcriber is not None:
            await self._riva_transcriber.stop()
            self._riva_transcriber = None

        # Cancel any pending debounce task
        if self.partial_transcript_task and not self.partial_transcript_task.done():
            self.partial_transcript_task.cancel()
            try:
                await self.partial_transcript_task
            except asyncio.CancelledError:
                pass

        if self.connection:
            try:
                await self.connection.close()
            except ConnectionClosedError as e:
                logger.debug(f"Connection already closed during shutdown: {e}")
            except Exception as e:
                logger.debug(f"connection.close() ignored: {e}")
            finally:
                self.connection = None

        # Clear any remaining items in the output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def format_timestamp(self) -> str:
        """Format current timestamp with date, time, and elapsed seconds."""
        loop_time = asyncio.get_event_loop().time()  # monotonic
        elapsed_seconds = loop_time - self.start_time
        dt = datetime.now()  # wall-clock
        return f"[{dt.strftime('%Y-%m-%d %H:%M:%S')} | +{elapsed_seconds:.1f}s]"

    async def send_idle_signal(self, idle_duration: float) -> None:
        """Send an idle signal to the openai server."""
        logger.debug("Sending idle signal")
        self.is_idle_tool_call = True
        timestamp_msg = f"[Idle time update: {self.format_timestamp()} - No activity for {idle_duration:.1f}s] You've been idle for a while. Feel free to get creative - dance, show an emotion, look around, do nothing, or just be yourself!"
        if not self.connection:
            logger.debug("No connection, cannot send idle signal")
            return
        await self.connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": timestamp_msg}],
            },
        )
        await self._safe_response_create(
            response={
                "instructions": "You MUST respond with function calls only - no speech or text. Choose appropriate actions for idle behavior.",
                "tool_choice": "required",
            },
        )
