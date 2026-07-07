import json
import uuid
import base64
import random
import asyncio
import logging
from typing import Any, Final, Tuple, Callable, Optional, cast
from pathlib import Path
from datetime import datetime
from collections import deque

import numpy as np
import gradio as gr
from openai import AsyncOpenAI
from fastrtc import AdditionalOutputs, AsyncStreamHandler, wait_for_item
from numpy.typing import NDArray
from websockets.exceptions import ConnectionClosedError

from reachy_mini_conversation_app.config import (
    LOCKED_PROFILE,
    BACKEND_HF_REALTIME,
    BACKEND_OPENAI_REALTIME,
    config,
    openai_realtime_api_key,
)
from reachy_mini_conversation_app.prompts import get_session_voice, get_session_instructions
from reachy_mini_conversation_app.audio.pcm import prepare_mono_int16_audio
from reachy_mini_conversation_app.tool_transport import ToolTransport
from reachy_mini_conversation_app.backend_runtime import (
    selected_backend,
    backend_config_error,
    local_stt_chat_config_error,
)
from reachy_mini_conversation_app.audio.mic_phrase import (
    MIC_TRANSCRIPTION_SAMPLE_RATE,
    MicPhraseBuffer,
    MicPhraseConfig,
)
from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    get_tool_specs_for_dependencies,
)
from reachy_mini_conversation_app.local_stt_backend import LocalSTTBackend
from reachy_mini_conversation_app.realtime_backends import (
    realtime_context,
    build_realtime_client,
    provider_realtime_hint,
    build_realtime_connect_kwargs,
    build_realtime_session_config,
)
from reachy_mini_conversation_app.media_result_processor import (
    MediaSecurityError,
    ProcessedToolResult,
    MediaResultProcessor,
    contains_raw_media,
    assert_no_raw_media,
)
from reachy_mini_conversation_app.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


logger = logging.getLogger(__name__)

# Cost tracking from usage data (pricing as of Feb 2026 https://openai.com/api/pricing/)
AUDIO_INPUT_COST_PER_1M = 32.0
AUDIO_OUTPUT_COST_PER_1M = 64.0
TEXT_INPUT_COST_PER_1M = 4.0
TEXT_OUTPUT_COST_PER_1M = 16.0
IMAGE_INPUT_COST_PER_1M = 5.0

_RESPONSE_DONE_TIMEOUT: Final[float] = 30.0
_TYPED_TOOL_TIMEOUT: Final[float] = 180.0
_MODEL_IO_MAX_STRING: Final[int] = 8_000
_MAX_TOOL_IMAGES: Final[int] = 12
_MODEL_IO_SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "authorization",
        "hf_token",
        "openai_api_key",
        "openai_realtime_api_key",
        "token",
    }
)


def _encoded_payload_summary(value: str, media_type: str) -> str:
    """Describe a Base64 payload without writing the payload to logs."""
    encoded = value.split(",", 1)[1] if "," in value else value
    padding = len(encoded) - len(encoded.rstrip("="))
    estimated_bytes = max(0, (len(encoded) * 3 // 4) - padding)
    return f"<{media_type}: base64_chars={len(encoded)}, estimated_bytes={estimated_bytes}>"


def _sanitize_model_io(value: Any, field_name: str = "") -> Any:
    """Convert model I/O to JSON-safe values while removing secrets and binary blobs."""
    normalized_field = field_name.lower()
    if normalized_field in _MODEL_IO_SECRET_FIELDS:
        return "<redacted>"

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            value = model_dump(mode="json")
        except TypeError:
            value = model_dump()

    if isinstance(value, dict):
        return {str(key): _sanitize_model_io(item, str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_model_io(item, field_name) for item in value]
    if isinstance(value, str):
        if normalized_field == "image_url" and value.startswith("data:image/"):
            media_type = value.split(";", 1)[0].removeprefix("data:")
            return _encoded_payload_summary(value, media_type)
        if normalized_field in {"audio", "b64_im", "b64_images", "delta"} and len(value) > 128:
            return _encoded_payload_summary(value, normalized_field)
        if len(value) > _MODEL_IO_MAX_STRING:
            omitted = len(value) - _MODEL_IO_MAX_STRING
            return f"{value[:_MODEL_IO_MAX_STRING]}... <{omitted} characters omitted>"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value

    return repr(value)


def _model_io_json(value: Any) -> str:
    """Return a compact, redacted JSON representation for debug logs."""
    return json.dumps(_sanitize_model_io(value), ensure_ascii=False, separators=(",", ":"), default=repr)


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


class ConversationStreamHandler(AsyncStreamHandler):
    """Conversation audio/text stream handler for the selected backend."""

    def __init__(
        self,
        deps: ToolDependencies,
        gradio_mode: bool = False,
        instance_path: Optional[str] = None,
        model_logs: bool = False,
        tool_transport: ToolTransport | None = None,
        tool_transport_factory: Callable[[], ToolTransport] | None = None,
        media_result_processor: MediaResultProcessor | None = None,
    ):
        """Initialize the handler."""
        backend = selected_backend()
        stream_sample_rate = backend.stream_sample_rate
        super().__init__(
            expected_layout="mono",
            output_sample_rate=stream_sample_rate,
            input_sample_rate=stream_sample_rate,
        )

        self.deps = deps
        if tool_transport is not None and tool_transport_factory is not None:
            raise ValueError("Provide tool_transport or tool_transport_factory, not both")
        self._tool_transport_factory = tool_transport_factory
        self.tool_transport = tool_transport or (tool_transport_factory() if tool_transport_factory else None)
        self._tool_specs_cache: list[dict[str, Any]] | None = None
        self._tool_transport_closed = False
        self.media_result_processor = media_result_processor

        self.output_sample_rate = stream_sample_rate
        self.input_sample_rate = stream_sample_rate

        self.connection: Any = None
        self.local_stt_backend: LocalSTTBackend | None = None
        self._realtime_connect_query: dict[str, str] = {}
        self.output_queue: "asyncio.Queue[Tuple[int, NDArray[np.int16]] | AdditionalOutputs]" = asyncio.Queue()
        self._typed_output_queue: asyncio.Queue[AdditionalOutputs] | None = None
        self._typed_request_lock: asyncio.Lock = asyncio.Lock()
        self._typed_tool_calls_awaiting_followup: set[str] = set()
        self._typed_followup_call_order: deque[str] = deque()
        self._tool_call_response_ids: set[str] = set()
        self._chat_response_ids: set[str] = set()

        self.last_activity_time = asyncio.get_event_loop().time()
        self.start_time = asyncio.get_event_loop().time()
        self.is_idle_tool_call = False
        self.gradio_mode = gradio_mode
        self.instance_path = instance_path
        self.model_logs = model_logs

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

        self.mic_phrase_buffer = MicPhraseBuffer(
            MicPhraseConfig(
                sample_rate=MIC_TRANSCRIPTION_SAMPLE_RATE,
                rms_threshold=config.MIC_TRANSCRIPTION_RMS_THRESHOLD,
                min_audio_ms=config.MIC_TRANSCRIPTION_MIN_AUDIO_MS,
                silence_ms=config.MIC_TRANSCRIPTION_SILENCE_MS,
                max_audio_ms=config.MIC_TRANSCRIPTION_MAX_AUDIO_MS,
            )
        )
        self._mic_transcription_lock: asyncio.Lock = asyncio.Lock()
        self._mic_transcription_tasks: set[asyncio.Task[None]] = set()

    def copy(self) -> "ConversationStreamHandler":
        """Create a copy of the handler."""
        return ConversationStreamHandler(
            self.deps,
            self.gradio_mode,
            self.instance_path,
            self.model_logs,
            tool_transport=self.tool_transport if self._tool_transport_factory is None else None,
            tool_transport_factory=self._tool_transport_factory,
            media_result_processor=self.media_result_processor,
        )

    async def _available_tool_specs(self) -> list[dict[str, Any]]:
        """Return and cache the schemas advertised by the selected transport."""
        if self._tool_specs_cache is None:
            if self.tool_transport is None:
                self._tool_specs_cache = get_tool_specs_for_dependencies(self.deps)
            else:
                self._tool_specs_cache = await self.tool_transport.list_tools()
        return self._tool_specs_cache

    def _record_startup_error(self, message: str) -> None:
        """Store a startup failure so the UI can show a useful error."""
        self._startup_error = message
        try:
            self._connected_event.set()
        except Exception:
            pass

    def _not_connected_message(self) -> str:
        """Return the user-facing reason the Realtime session is unavailable."""
        if self._startup_error:
            return f"[error] {self._startup_error}"
        return (
            "[error] Realtime session is not connected. Check BACKEND_PROVIDER and the matching API key, "
            "OPENAI_REALTIME_* values, or HF_REALTIME_* values."
        )

    def _record_backend_config_error(self, error: str) -> None:
        """Store a selected-backend config error for user-facing output."""
        if selected_backend().provider == BACKEND_OPENAI_REALTIME and "OPENAI_API_KEY" in error:
            message = (
                f"{error} Export OPENAI_API_KEY in the shell that starts the app, or set "
                "OPENAI_REALTIME_API_KEY in .env only if this app needs a different OpenAI key. "
                "Then restart the conversation app."
            )
        else:
            message = f"{error} Add the missing value to .env and restart the conversation app."
        self._record_startup_error(message)
        logger.error(message)

    @staticmethod
    def _text_model_uses_realtime() -> bool:
        """Return whether typed messages should use the Realtime transport."""
        return selected_backend().uses_realtime

    def _get_local_stt_backend(self) -> LocalSTTBackend:
        """Return the local-STT backend adapter."""
        if self.local_stt_backend is None:
            self.local_stt_backend = LocalSTTBackend(
                deps=self.deps,
                tool_manager=self.tool_manager,
                client_factory=AsyncOpenAI,
                tool_transport=self.tool_transport,
                media_result_processor=self.media_result_processor,
            )
        return self.local_stt_backend

    def _schedule_mic_transcription(self, audio_frame: NDArray[np.int16]) -> None:
        """Start a background transcription task for a captured mic phrase."""
        if audio_frame.size == 0:
            return

        task = asyncio.create_task(
            self._transcribe_and_send_mic_audio(audio_frame),
            name="mic-transcribe-and-send",
        )
        self._mic_transcription_tasks.add(task)

        def discard_task(done_task: asyncio.Task[None]) -> None:
            self._mic_transcription_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Mic transcription task failed")

        task.add_done_callback(discard_task)

    async def _receive_transcribed_text_frame(self, frame: Tuple[int, NDArray[Any]]) -> None:
        """Buffer microphone audio and transcribe completed phrases for chat models."""
        self._microphone_error_reported = False
        phrase_result = self.mic_phrase_buffer.push_frame(frame)
        if phrase_result.saw_speech:
            self.last_activity_time = asyncio.get_event_loop().time()

        if phrase_result.phrase_audio is None:
            return

        self._schedule_mic_transcription(phrase_result.phrase_audio)

    async def _transcribe_and_send_mic_audio(self, audio_frame: NDArray[np.int16]) -> None:
        """Transcribe microphone audio and send the transcript through text chat."""
        async with self._mic_transcription_lock:
            local_backend = self._get_local_stt_backend()
            try:
                transcript = await local_backend.transcribe_audio(
                    audio_frame,
                    MIC_TRANSCRIPTION_SAMPLE_RATE,
                    filename="microphone.wav",
                )
            except Exception as e:
                logger.exception("Speech transcription failed")
                await self.output_queue.put(
                    AdditionalOutputs(
                        {
                            "role": "assistant",
                            "content": (
                                f"[error] Speech transcription failed "
                                f"({local_backend.stt_context}): "
                                f"{type(e).__name__}: {e}"
                            ),
                        },
                    ),
                )
                return

            if not transcript:
                logger.debug("Speech transcription returned no text")
                return

            for message in await self.send_text_message(transcript):
                await self.output_queue.put(AdditionalOutputs(message))
                if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                    if message.get("metadata"):
                        continue
                    await self._synthesize_assistant_speech(message["content"])

    async def _synthesize_assistant_speech(self, text: str) -> None:
        """Synthesize assistant text and enqueue audio for playback."""
        spoken_text = text.strip()
        if not spoken_text or spoken_text.startswith("[error]"):
            return

        local_backend = self._get_local_stt_backend()
        try:
            sample_rate, audio_frame = await local_backend.synthesize_speech(spoken_text)
        except Exception as e:
            logger.exception("Text-to-speech synthesis failed")
            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "assistant",
                        "content": (
                            f"[error] Text-to-speech synthesis failed "
                            f"({local_backend.tts_context}): "
                            f"{type(e).__name__}: {e}"
                        ),
                    },
                )
            )
            return

        if self.deps.head_wobbler is not None:
            self.deps.head_wobbler.feed(base64.b64encode(audio_frame.tobytes()).decode("utf-8"))
        await self.output_queue.put((sample_rate, audio_frame.reshape(1, -1)))

    def _chat_api_key_or_error(self) -> str | None:
        """Return the configured chat API key or record a useful startup error."""
        chat_api_key = (config.CHAT_API_KEY or "").strip()
        if chat_api_key:
            return chat_api_key

        message = (
            "CHAT_API_KEY is missing or empty after reading .env. Add it to .env and restart the "
            "conversation app. If .env uses CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}, make sure "
            "NVIDIA_INFERENCE_API_KEY is exported in the shell that starts the app."
        )
        self._record_startup_error(message)
        logger.error(message)
        return None

    def _realtime_api_key_or_error(self) -> str | None:
        """Return the configured realtime API key or record a useful startup error."""
        if selected_backend().provider == BACKEND_HF_REALTIME:
            return (config.HF_TOKEN or "").strip() or "DUMMY"

        realtime_api_key = openai_realtime_api_key()
        if realtime_api_key:
            return realtime_api_key

        message = (
            "OPENAI_API_KEY is not exported in the shell that starts the conversation app, and no "
            "OPENAI_REALTIME_API_KEY override was found in .env. Export OPENAI_API_KEY, then restart "
            "the app. Set OPENAI_REALTIME_API_KEY in .env only if this app should use a different "
            "OpenAI key from the global one."
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
        return await self._ensure_realtime_session("conversation-realtime-text")

    async def _report_microphone_error_once(self, message: str) -> None:
        """Show one visible microphone error instead of silently dropping every frame."""
        if self._microphone_error_reported:
            return
        self._microphone_error_reported = True
        await self._publish_chat_output({"role": "assistant", "content": message})

    async def _publish_chat_output(self, message: dict[str, Any]) -> None:
        """Publish a chat update to the live stream and any active typed request.

        FastRTC continuously drains ``output_queue``. Typed Gradio callbacks
        therefore need their own copy so the stream cannot consume the model's
        final transcript before ``send_text_message`` sees it.
        """
        output = AdditionalOutputs(message)
        await self.output_queue.put(output)
        typed_output_queue = self._typed_output_queue
        if typed_output_queue is not None:
            await typed_output_queue.put(output)

    async def _create_conversation_item(self, item: dict[str, Any]) -> None:
        """Log and send a Realtime conversation.item.create request."""
        self._log_model_request("conversation.item.create", item)
        await self.connection.conversation.item.create(item=item)

    def _log_model_request(self, request_type: str, payload: Any) -> None:
        """Log a sanitized model request at the selected detail level."""
        log = logger.info if self.model_logs else logger.debug
        log("MODEL request type=%s payload=%s", request_type, _model_io_json(payload))

    @staticmethod
    def _response_message_text(response: Any) -> str:
        """Extract final assistant text or audio transcript from response.done."""
        output_items = getattr(response, "output", None)
        if not isinstance(output_items, list):
            return ""

        text_parts: list[str] = []
        for item in output_items:
            if getattr(item, "type", None) != "message" or getattr(item, "role", None) != "assistant":
                continue
            content_parts = getattr(item, "content", None)
            if not isinstance(content_parts, list):
                continue
            for content_part in content_parts:
                text = getattr(content_part, "transcript", None) or getattr(content_part, "text", None)
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())

        return "\n".join(text_parts)

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

    @staticmethod
    def _is_final_assistant_message(message: dict[str, Any]) -> bool:
        """Return whether a chat update is a user-facing assistant answer.

        Tool lifecycle cards and camera previews also use the assistant role in
        Gradio, but they are intermediate updates. Treating either as the final
        answer makes typed requests return before the post-tool model response.
        """
        if message.get("role") != "assistant" or message.get("metadata"):
            return False

        content = message.get("content")
        if not isinstance(content, str):
            return False

        return not content.startswith("🛠️ Used tool")

    def _typed_turn_is_complete(self, saw_assistant_message: bool) -> bool:
        """Return whether typed chat has a final answer and no pending tool follow-up."""
        return (
            saw_assistant_message
            and self._response_done_event.is_set()
            and not self._typed_tool_calls_awaiting_followup
        )

    def _mark_typed_followup_response(self, response_id: Any) -> None:
        """Match a non-tool response to the next tool awaiting a spoken follow-up."""
        if not self._typed_tool_calls_awaiting_followup:
            return
        if isinstance(response_id, str) and response_id in self._tool_call_response_ids:
            return
        if not self._typed_followup_call_order:
            return

        call_id = self._typed_followup_call_order.popleft()
        self._typed_tool_calls_awaiting_followup.discard(call_id)
        logger.debug("Typed tool follow-up completed for call_id=%s response_id=%s", call_id, response_id)

    async def send_text_message(
        self,
        message: str,
        timeout: float = 30.0,
        tool_timeout: float = _TYPED_TOOL_TIMEOUT,
    ) -> list[dict[str, Any]]:
        """Send a typed user message and collect chat updates."""
        text = message.strip()
        if not text:
            return []

        config_error = backend_config_error()
        if config_error:
            self._record_backend_config_error(config_error)
            return [
                {
                    "role": "assistant",
                    "content": self._not_connected_message(),
                },
            ]

        if not self._text_model_uses_realtime():
            return await self._send_chat_completion_text_message(text)

        if not await self._ensure_text_session():
            return [
                {
                    "role": "assistant",
                    "content": self._not_connected_message(),
                },
            ]

        async with self._typed_request_lock:
            typed_output_queue: asyncio.Queue[AdditionalOutputs] = asyncio.Queue()
            self._typed_output_queue = typed_output_queue
            self._typed_tool_calls_awaiting_followup.clear()
            self._typed_followup_call_order.clear()
            self._tool_call_response_ids.clear()
            try:
                return await self._send_realtime_text_message(text, timeout, tool_timeout, typed_output_queue)
            finally:
                self._typed_output_queue = None
                self._typed_tool_calls_awaiting_followup.clear()
                self._typed_followup_call_order.clear()
                self._tool_call_response_ids.clear()

    async def _send_realtime_text_message(
        self,
        text: str,
        timeout: float,
        tool_timeout: float,
        typed_output_queue: asyncio.Queue[AdditionalOutputs],
    ) -> list[dict[str, Any]]:
        """Send one typed Realtime turn and collect its dedicated chat updates."""
        await self._create_conversation_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        await self._safe_response_create()

        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        saw_assistant_message = False
        turn_completed = False
        loop = asyncio.get_event_loop()
        response_deadline = loop.time() + timeout
        active_tool_deadline: float | None = None

        while True:
            now = loop.time()
            has_pending_tool = bool(self._typed_tool_calls_awaiting_followup)
            if has_pending_tool and active_tool_deadline is None:
                active_tool_deadline = now + tool_timeout
                logger.debug("Extended typed request timeout for active tool by %.1f seconds", tool_timeout)
            elif not has_pending_tool and active_tool_deadline is not None:
                active_tool_deadline = None
                response_deadline = now + timeout

            if has_pending_tool:
                assert active_tool_deadline is not None
                deadline = active_tool_deadline
            else:
                deadline = response_deadline
            if now >= deadline:
                break

            try:
                output = await asyncio.wait_for(typed_output_queue.get(), timeout=min(0.5, deadline - now))
            except asyncio.TimeoutError:
                if self._typed_turn_is_complete(saw_assistant_message):
                    turn_completed = True
                    break
                continue

            response_deadline = loop.time() + timeout
            chat_message = self._chat_message_from_output(output)
            if chat_message is None:
                continue

            messages.append(chat_message)
            if self._is_final_assistant_message(chat_message):
                saw_assistant_message = True

            if self._typed_turn_is_complete(saw_assistant_message):
                turn_completed = True
                break

        if not turn_completed:
            logger.warning(
                "Typed Realtime request timed out (pending_tools=%s, saw_assistant=%s)",
                sorted(self._typed_tool_calls_awaiting_followup),
                saw_assistant_message,
            )
            messages.append({"role": "assistant", "content": "[error] Timed out waiting for a response."})

        return messages

    async def _send_chat_completion_text_message(self, text: str) -> list[dict[str, Any]]:
        """Send a typed message through Chat Completions for non-Realtime models."""
        chat_api_key = self._chat_api_key_or_error()
        if chat_api_key is None:
            return [{"role": "assistant", "content": self._not_connected_message()}]

        config_error = local_stt_chat_config_error()
        if config_error:
            self._record_backend_config_error(config_error)
            return [{"role": "assistant", "content": self._not_connected_message()}]

        return await self._get_local_stt_backend().send_text_message(text)

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
        try:
            await self._available_tool_specs()
        except Exception as e:
            message = f"Tool discovery failed: {type(e).__name__}: {e}"
            self._record_startup_error(message)
            logger.exception(message)
            return

        if not self._text_model_uses_realtime():
            logger.info(
                "Skipping Realtime startup because BACKEND_PROVIDER=%r uses local STT/chat", config.BACKEND_PROVIDER
            )
            self._connected_event.set()
            return

        realtime_api_key = self._realtime_api_key_or_error()
        if realtime_api_key is None:
            return

        config_error = backend_config_error()
        if config_error:
            self._record_backend_config_error(config_error)
            return

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                if getattr(self, "client", None) is None or (
                    attempt > 1 and selected_backend().refresh_realtime_client_on_retry
                ):
                    self.client = await self._build_realtime_client(realtime_api_key)
                await self._run_realtime_session()
                # Normal exit from the session, stop retrying
                return
            except ConnectionClosedError as e:
                # Abrupt close (e.g., "no close frame received or sent") triggers a retry.
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
                    f"Realtime websocket closed before startup completed ({realtime_context()}): "
                    f"{type(e).__name__}: {e}.{provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.error(message)
                return
            except Exception as e:
                message = (
                    f"Realtime startup failed ({realtime_context()}): "
                    f"{type(e).__name__}: {e}.{provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.exception(message)
                return

    async def _build_realtime_client(self, realtime_api_key: str) -> Any:
        """Build the selected OpenAI-compatible realtime client."""
        bundle = await build_realtime_client(
            realtime_api_key,
            client_factory=AsyncOpenAI,
        )
        self._realtime_connect_query = bundle.connect_query
        return bundle.client

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

            realtime_api_key = self._realtime_api_key_or_error()
            if realtime_api_key is None:
                return

            config_error = backend_config_error()
            if config_error:
                self._record_backend_config_error(config_error)
                return

            if getattr(self, "client", None) is None or selected_backend().refresh_realtime_client_on_retry:
                self.client = await self._build_realtime_client(realtime_api_key)

            # Fire-and-forget new session and wait briefly for connection
            try:
                self._connected_event.clear()
            except Exception:
                pass
            asyncio.create_task(self._run_realtime_session(), name="conversation-realtime-restart")
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
                    logger.warning("Timed out waiting for previous response to finish; forcing ahead")
                    self._response_done_event.set()

                if not self.connection:
                    break

                self._last_response_rejected = False
                try:
                    self._response_done_event.clear()
                    logger.info("Sending queued Realtime response")
                    self._log_model_request("response.create", kwargs)
                    await self.connection.response.create(**kwargs)
                    logger.info("Realtime response request sent; waiting for model output")
                except Exception as e:
                    logger.exception("Failed to send queued Realtime response: %s", e)
                    self._response_done_event.set()
                    break

                try:
                    await asyncio.wait_for(self._response_done_event.wait(), timeout=_RESPONSE_DONE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("Timed out waiting for response.done; assuming response completed")
                    self._response_done_event.set()
                    break

                # Check if we were rejected
                if self._last_response_rejected:
                    attempts += 1
                    if attempts >= max_retries:
                        logger.warning("response.create rejected %d times; giving up", attempts)
                        break
                    logger.debug("response.create was rejected; retrying (%d/%d)", attempts, max_retries)
                    continue

                sent = True

    async def _handle_tool_result(self, bg_tool: ToolNotification) -> None:
        """Process the result of a tool call."""
        if bg_tool.result is not None:
            tool_result = bg_tool.result
            if bg_tool.error is not None:
                logger.error("Tool '%s' (id=%s) failed with error: %s", bg_tool.tool_name, bg_tool.id, bg_tool.error)
            else:
                logger.info(
                    "Tool '%s' (id=%s) executed successfully.",
                    bg_tool.tool_name,
                    bg_tool.id,
                )
            logger.debug("TOOL response name=%s result=%s", bg_tool.tool_name, _model_io_json(tool_result))
        elif bg_tool.error is not None:
            logger.error("Tool '%s' (id=%s) failed with error: %s", bg_tool.tool_name, bg_tool.id, bg_tool.error)
            tool_result = {"error": bg_tool.error}
        else:
            logger.warning("Tool '%s' (id=%s) returned no result and no error", bg_tool.tool_name, bg_tool.id)
            tool_result = {"error": "No result returned from tool execution"}

        # Connection may have closed while tool was running
        if not self.connection:
            logger.warning(
                "Connection closed during tool '%s' (id=%s) execution; cannot send result back",
                bg_tool.tool_name,
                bg_tool.id,
            )
            return

        try:
            vision_images: list[str] = []
            processed_media: ProcessedToolResult | None = None
            if self.media_result_processor is not None:
                try:
                    processed_media = await self.media_result_processor.process(bg_tool.tool_name, tool_result)
                    model_tool_result = processed_media.model_payload
                except MediaSecurityError:
                    logger.error("Raw media was rejected before Realtime serialization")
                    model_tool_result = {
                        "status": "media_security_error",
                        "tool": bg_tool.tool_name,
                        "error": "Raw media was rejected before reaching the conversation model",
                    }
            elif config.REQUIRE_ROUTED_VISION and contains_raw_media(tool_result):
                logger.error("Raw media reached a strict Realtime handler without a media processor")
                model_tool_result = {
                    "status": "media_security_error",
                    "tool": bg_tool.tool_name,
                    "error": "Routed vision is required; raw media was discarded",
                }
            else:
                model_tool_result = dict(tool_result)
                if bg_tool.tool_name == "camera" and "b64_im" in model_tool_result:
                    raw_camera_image = model_tool_result.pop("b64_im")
                    if isinstance(raw_camera_image, str) and raw_camera_image:
                        vision_images = [raw_camera_image]
                        model_tool_result["status"] = "image_captured"
                    else:
                        logger.warning("Unexpected camera image type: %s", type(raw_camera_image))
                        model_tool_result = {"error": "Camera returned an invalid image"}
                elif bg_tool.tool_name == "scan_scene" and "b64_images" in model_tool_result:
                    raw_scan_images = model_tool_result.pop("b64_images")
                    if (
                        isinstance(raw_scan_images, list)
                        and 0 < len(raw_scan_images) <= _MAX_TOOL_IMAGES
                        and all(isinstance(image, str) and image for image in raw_scan_images)
                    ):
                        vision_images = raw_scan_images
                    else:
                        logger.warning("Unexpected scene-scan image payload")
                        model_tool_result = {"error": "Scene scan returned invalid analysis images"}

            assert_no_raw_media(model_tool_result)
            serialized_tool_result = json.dumps(model_tool_result)

            # Send the tool result back
            if isinstance(bg_tool.id, str):
                await self._create_conversation_item(
                    {
                        "type": "function_call_output",
                        "call_id": bg_tool.id,
                        "output": serialized_tool_result,
                    }
                )

            await self._publish_chat_output(
                {
                    "role": "assistant",
                    "content": serialized_tool_result,
                    # Gradio UI metadata.status accept only "pending" and "done". Do not accept bg.tool.status values.
                    "metadata": {
                        "title": f"🛠️ Used tool {bg_tool.tool_name}",
                        "status": "done",
                    },
                },
            )

            if vision_images:
                image_content: list[dict[str, Any]] = []
                if bg_tool.tool_name == "scan_scene":
                    question = model_tool_result.get("question", "Describe everything visible during the scan.")
                    timestamps = model_tool_result.get("frame_timestamps_seconds", [])
                    image_content.append(
                        {
                            "type": "input_text",
                            "text": (
                                "These are chronological frames sampled across one Reachy scene sweep. "
                                f"Frame timestamps in seconds: {timestamps}. "
                                "Combine evidence across all frames, deduplicate repeated people and objects, "
                                "and do not claim details that are not visibly supported. "
                                f"User question: {question}"
                            ),
                        }
                    )
                image_content.extend(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{image}",
                    }
                    for image in vision_images
                )
                await self._create_conversation_item(
                    {
                        "type": "message",
                        "role": "user",
                        "content": image_content,
                    }
                )
                logger.info("Added %d image(s) from tool '%s' to conversation", len(vision_images), bg_tool.tool_name)

            # Show the local camera preview even when a dedicated vision model
            # consumed the raw image and only returned a text description.
            preview_image = processed_media.preview_image if processed_media is not None else None
            if preview_image is None and (
                bg_tool.tool_name == "camera"
                and self.deps.camera_worker is not None
                and (vision_images or model_tool_result.get("status") == "image_analyzed")
            ):
                np_img = self.deps.camera_worker.get_latest_frame()
                if np_img is not None:
                    import cv2

                    # Camera frames are BGR from OpenCV; convert so Gradio displays correct colors.
                    preview_image = cv2.cvtColor(np_img, cv2.COLOR_BGR2RGB)

            if preview_image is not None:
                img = gr.Image(value=preview_image)

                await self._publish_chat_output(
                    {
                        "role": "assistant",
                        "content": img,
                    },
                )

            if bg_tool.tool_name == "scan_scene":
                video_path: str | Path | None = (
                    processed_media.video_path if processed_media is not None else model_tool_result.get("video_path")
                )
                if isinstance(video_path, (str, Path)) and Path(video_path).is_file():
                    await self._publish_chat_output(
                        {
                            "role": "assistant",
                            "content": gr.Video(value=str(video_path)),
                        },
                    )

            # If this tool call was triggered by an idle signal, don't make the robot speak.
            # For other tool calls, let the robot reply out loud.
            if not bg_tool.is_idle_tool_call:
                follow_up_instructions = "Use the tool result just returned and answer concisely in speech."
                if bg_tool.tool_name == "camera" and vision_images:
                    follow_up_instructions = (
                        "Answer the user's camera question using the input image just added. "
                        "Describe only what is visibly supported by the image, and answer concisely in speech."
                    )
                elif bg_tool.tool_name == "camera" and model_tool_result.get("status") == "image_analyzed":
                    follow_up_instructions = (
                        "Relay the image_description returned by the approved vision model. "
                        "Answer the user's camera question concisely in speech and do not claim that you "
                        "personally received the raw image."
                    )
                elif bg_tool.tool_name == "scan_scene" and vision_images:
                    follow_up_instructions = (
                        "Answer the user's scene-scan question using every chronological image just added. "
                        "Give one concise combined account, deduplicate things visible in multiple frames, and "
                        "describe only visibly supported details. Mention that the recording was saved, but do "
                        "not read the full local filesystem path aloud."
                    )
                elif bg_tool.tool_name == "scan_scene" and model_tool_result.get("status") == "scene_analyzed":
                    if model_tool_result.get("recording_status") == "preview_unavailable":
                        follow_up_instructions = (
                            "Relay the image_description returned by the approved vision model as one concise "
                            "combined account. Briefly mention that the recording preview is unavailable, without "
                            "exposing internal paths."
                        )
                    else:
                        follow_up_instructions = (
                            "Relay the image_description returned by the approved vision model as one concise "
                            "combined account. Mention that the recording was saved, but do not expose internal paths."
                        )
                await self._safe_response_create(
                    response={
                        "instructions": follow_up_instructions,
                        "tool_choice": "none",
                    },
                )
                if isinstance(bg_tool.id, str) and bg_tool.id in self._typed_tool_calls_awaiting_followup:
                    self._typed_followup_call_order.append(bg_tool.id)
                logger.info("Queued spoken follow-up for tool '%s'", bg_tool.tool_name)

            # Re-synchronize the head wobble after a tool call that may have taken some time
            if self.deps.head_wobbler is not None:
                self.deps.head_wobbler.reset()

        except ConnectionClosedError:
            logger.warning("Connection closed while sending tool result")
            self.connection = None
            self._response_done_event.set()

    async def _run_realtime_session(self) -> None:
        """Establish and manage a single realtime session."""
        connect_kwargs = build_realtime_connect_kwargs(self._realtime_connect_query)

        async with self.client.realtime.connect(**connect_kwargs) as conn:
            try:
                backend = selected_backend()
                session_voice = get_session_voice(backend.realtime_voice)
                session_config = build_realtime_session_config(
                    backend_provider=backend.provider,
                    input_sample_rate=self.input_sample_rate,
                    output_sample_rate=self.output_sample_rate,
                    instructions=get_session_instructions(),
                    voice=session_voice,
                    tools=await self._available_tool_specs(),
                    transcription_language=config.REALTIME_TRANSCRIPTION_LANGUAGE,
                )
                logger.info("Realtime model connection: %s", realtime_context())
                logger.debug("MODEL request session.update=%s", _model_io_json({"session": session_config}))
                await conn.session.update(session=cast(Any, session_config))
                logger.info(
                    "Realtime session initialized with backend=%r model=%r locked_profile=%r voice=%r tools=%s",
                    backend.provider,
                    backend.realtime_model,
                    LOCKED_PROFILE,
                    session_voice,
                    [tool.get("name", "<unnamed>") for tool in session_config["tools"]],
                )
                if self.model_logs:
                    logger.info(
                        "MODEL selected provider=%s model=%s voice=%s",
                        backend.provider,
                        backend.realtime_model,
                        session_voice,
                    )
            except Exception as e:
                message = (
                    f"Realtime session.update failed ({realtime_context()}): "
                    f"{type(e).__name__}: {e}.{provider_realtime_hint()}"
                )
                self._record_startup_error(message)
                logger.exception("Realtime session.update failed; aborting startup")
                return

            logger.info("Realtime session updated successfully")

            # Manage events received from the selected Realtime backend.
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
                response_sender_task = asyncio.create_task(self._response_sender_loop(), name="response-sender")

                async for event in self.connection:
                    logger.debug(f"OpenAI event: {event.type}")
                    if event.type == "input_audio_buffer.speech_started":
                        if hasattr(self, "_clear_queue") and callable(self._clear_queue):
                            self._clear_queue()
                        if self.deps.head_wobbler is not None:
                            self.deps.head_wobbler.reset()
                        if self.deps.movement_manager is not None:
                            self.deps.movement_manager.set_listening(True)
                        logger.debug("User speech started")

                    if event.type == "input_audio_buffer.speech_stopped":
                        if self.deps.movement_manager is not None:
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
                        response = getattr(event, "response", None)
                        response_status = getattr(response, "status", "unknown") if response else "unknown"
                        logger.info("Realtime response completed (status=%s)", response_status)
                        logger.debug("MODEL response response.done=%s", _model_io_json(response))

                        # The normal transcript/text events are preferred, but
                        # response.done also contains the complete assistant
                        # message. Use it as a fallback for compatible backends
                        # that omit the dedicated done event.
                        response_id = getattr(response, "id", None) if response else None
                        if isinstance(response_id, str) and response_id not in self._chat_response_ids:
                            response_text = self._response_message_text(response)
                            if response_text:
                                self._mark_typed_followup_response(response_id)
                                logger.info(
                                    "Recovered assistant text from response.done (%d characters)", len(response_text)
                                )
                                await self._publish_chat_output({"role": "assistant", "content": response_text})
                                self._chat_response_ids.add(response_id)

                        usage = getattr(response, "usage", None) if response else None
                        if usage:
                            cost = _compute_response_cost(usage)
                            self.cumulative_cost += cost
                            if self.model_logs:
                                logger.info(
                                    "MODEL usage model=%s response_id=%s tokens=%s cost_usd=%.6f cumulative_cost_usd=%.6f",
                                    selected_backend().realtime_model,
                                    response_id,
                                    _model_io_json(usage),
                                    cost,
                                    self.cumulative_cost,
                                )
                            else:
                                logger.debug("Cost: $%.4f | Cumulative: $%.4f", cost, self.cumulative_cost)
                        else:
                            logger.warning("No usage data available for cost tracking")

                    # Handle partial transcription (user speaking in real-time)
                    if event.type == "conversation.item.input_audio_transcription.partial":
                        transcript = getattr(event, "transcript", "")
                        if not isinstance(transcript, str):
                            transcript = ""
                        logger.debug(f"User partial transcript: {transcript}")

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
                            self._emit_debounced_partial(transcript, current_sequence)
                        )

                    # Handle completed transcription (user finished speaking)
                    if event.type == "conversation.item.input_audio_transcription.completed":
                        transcript = getattr(event, "transcript", "")
                        if not isinstance(transcript, str):
                            transcript = ""
                        logger.debug(f"User transcript: {transcript}")

                        # Cancel any pending partial emission
                        if self.partial_transcript_task and not self.partial_transcript_task.done():
                            self.partial_transcript_task.cancel()
                            try:
                                await self.partial_transcript_task
                            except asyncio.CancelledError:
                                pass

                        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))

                    # Handle assistant transcription
                    if event.type in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
                        transcript = getattr(event, "transcript", "")
                        if not isinstance(transcript, str):
                            transcript = ""
                        logger.debug(f"Assistant transcript: {transcript}")
                        logger.info("Received assistant transcript (%d characters)", len(transcript))
                        logger.debug("MODEL response assistant.transcript=%s", _model_io_json(transcript))
                        await self._publish_chat_output({"role": "assistant", "content": transcript})
                        response_id = getattr(event, "response_id", None)
                        self._mark_typed_followup_response(response_id)
                        if isinstance(response_id, str):
                            self._chat_response_ids.add(response_id)

                    # Some Realtime responses use text content instead of an
                    # audio transcript. Surface those in the same chat path.
                    if event.type in ("response.text.done", "response.output_text.done"):
                        text = getattr(event, "text", "")
                        if not isinstance(text, str):
                            text = ""
                        logger.info("Received assistant text (%d characters)", len(text))
                        logger.debug("MODEL response assistant.text=%s", _model_io_json(text))
                        await self._publish_chat_output({"role": "assistant", "content": text})
                        response_id = getattr(event, "response_id", None)
                        self._mark_typed_followup_response(response_id)
                        if isinstance(response_id, str):
                            self._chat_response_ids.add(response_id)

                    # Handle audio delta
                    if event.type in ("response.audio.delta", "response.output_audio.delta"):
                        delta = getattr(event, "delta", None)
                        if not isinstance(delta, str):
                            logger.warning("Skipping audio delta event without string delta")
                            continue
                        if self.deps.head_wobbler is not None:
                            self.deps.head_wobbler.feed(delta)
                        self.last_activity_time = asyncio.get_event_loop().time()
                        logger.debug("last activity time updated to %s", self.last_activity_time)
                        await self.output_queue.put(
                            (
                                self.output_sample_rate,
                                np.frombuffer(base64.b64decode(delta), dtype=np.int16).reshape(1, -1),
                            ),
                        )

                    # ---- tool-calling plumbing ----
                    if event.type == "response.function_call_arguments.done":
                        tool_name = getattr(event, "name", None)
                        args_json_str = getattr(event, "arguments", None)
                        call_id: str = str(getattr(event, "call_id", uuid.uuid4()))

                        logger.info(
                            "Tool call received — tool_name=%r, call_id=%s, is_idle=%s, args=%s",
                            tool_name,
                            call_id,
                            self.is_idle_tool_call,
                            args_json_str,
                        )
                        logger.debug(
                            "MODEL response function_call=%s",
                            _model_io_json(
                                {
                                    "name": tool_name,
                                    "call_id": call_id,
                                    "arguments": args_json_str,
                                    "is_idle": self.is_idle_tool_call,
                                }
                            ),
                        )

                        if not isinstance(tool_name, str) or not isinstance(args_json_str, str):
                            logger.error(
                                "Invalid tool call: tool_name=%s (type=%s), args=%s (type=%s), call_id=%s",
                                tool_name,
                                type(tool_name).__name__,
                                args_json_str,
                                type(args_json_str).__name__,
                                call_id,
                            )
                            continue

                        response_id = getattr(event, "response_id", None)
                        if self._typed_output_queue is not None and not self.is_idle_tool_call:
                            self._typed_tool_calls_awaiting_followup.add(call_id)
                            if isinstance(response_id, str):
                                self._tool_call_response_ids.add(response_id)

                        bg_tool = await self.tool_manager.start_tool(
                            call_id=call_id,
                            tool_call_routine=ToolCallRoutine(
                                tool_name=tool_name,
                                args_json_str=args_json_str,
                                deps=self.deps,
                                transport=self.tool_transport,
                            ),
                            is_idle_tool_call=self.is_idle_tool_call,
                        )

                        await self._publish_chat_output(
                            {
                                "role": "assistant",
                                "content": f"🛠️ Used tool {tool_name} with args {args_json_str}. The tool is now running. Tool ID: {bg_tool.tool_id}",
                            },
                        )

                        if self.is_idle_tool_call:
                            self.is_idle_tool_call = False

                        logger.info(
                            "Started background tool: %s (id=%s, call_id=%s)", tool_name, bg_tool.tool_id, call_id
                        )

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
                            await self._publish_chat_output({"role": "assistant", "content": f"[error] {msg}"})
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
                self.connection = None
                try:
                    if self._startup_error is None:
                        self._connected_event.clear()
                    else:
                        self._connected_event.set()
                except Exception:
                    pass

    # Microphone receive
    async def receive(self, frame: Tuple[int, NDArray[Any]]) -> None:
        """Receive an audio frame from the microphone and send it through the selected backend.

        Handles both mono and stereo audio formats, converting to the expected
        mono format. Realtime models stream audio to the Realtime API; chat models
        transcribe completed phrases and pass the transcript through text chat.

        Args:
            frame: A tuple containing (sample_rate, audio_data).

        """
        if not self._text_model_uses_realtime():
            config_error = backend_config_error()
            if config_error:
                await self._report_microphone_error_once(
                    f"[error] {config_error} Add the missing value to .env and restart the conversation app."
                )
                return
            await self._receive_transcribed_text_frame(frame)
            return

        if not self.connection:
            if not await self._ensure_realtime_session("conversation-realtime-microphone"):
                await self._report_microphone_error_once(self._not_connected_message())
                return

        self._microphone_error_reported = False

        audio_frame = prepare_mono_int16_audio(frame, self.input_sample_rate)

        # Send to OpenAI (guard against races during reconnect)
        try:
            audio_message = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return

    async def emit(self) -> Tuple[int, NDArray[np.int16]] | AdditionalOutputs | None:
        """Emit audio frame to be played by the speaker."""
        # This is called periodically by the FastRTC stream to drain handler outputs.

        # Handle idle
        idle_duration = asyncio.get_event_loop().time() - self.last_activity_time
        if (
            self._text_model_uses_realtime()
            and idle_duration > 15.0
            and self.deps.movement_manager is not None
            and self.deps.movement_manager.is_idle()
        ):
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

        # Cancel any pending debounce task
        if self.partial_transcript_task and not self.partial_transcript_task.done():
            self.partial_transcript_task.cancel()
            try:
                await self.partial_transcript_task
            except asyncio.CancelledError:
                pass

        for task in list(self._mic_transcription_tasks):
            if not task.done():
                task.cancel()
        for task in list(self._mic_transcription_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._mic_transcription_tasks.clear()

        if self.connection:
            try:
                await self.connection.close()
            except ConnectionClosedError as e:
                logger.debug(f"Connection already closed during shutdown: {e}")
            except Exception as e:
                logger.debug(f"connection.close() ignored: {e}")
            finally:
                self.connection = None

        if self.tool_transport is not None and not self._tool_transport_closed:
            self._tool_transport_closed = True
            await self.tool_transport.close()

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
        """Send an idle signal to the selected Realtime backend."""
        logger.debug("Sending idle signal")
        self.is_idle_tool_call = True
        timestamp_msg = f"[Idle time update: {self.format_timestamp()} - No activity for {idle_duration:.1f}s] You've been idle for a while. Feel free to get creative - dance, show an emotion, look around, do nothing, or just be yourself!"
        if not self.connection:
            logger.debug("No connection, cannot send idle signal")
            return
        await self._create_conversation_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": timestamp_msg}],
            }
        )
        await self._safe_response_create(
            response={
                "instructions": "You MUST respond with function calls only - no speech or text. Choose appropriate actions for idle behavior.",
                "tool_choice": "required",
            },
        )
