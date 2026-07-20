import json
import base64
import random
import asyncio
import logging
from typing import Any, cast
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.chat_completions as chat_mod
import reachy_mini_conversation_app.realtime_backends as realtime_mod
import reachy_mini_conversation_app.conversation_stream as stream_mod
import reachy_mini_conversation_app.tools.background_tool_manager as btm_mod
from reachy_mini_conversation_app.config import (
    BACKEND_LOCAL_STT,
    BACKEND_HF_REALTIME,
    HF_REALTIME_CONNECTION_LOCAL,
)
from reachy_mini_conversation_app.audio.pcm import wav_bytes, prepare_mono_int16_audio
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.conversation_stream import (
    ConversationStreamHandler,
    _model_io_json,
    _compute_response_cost,
)
from reachy_mini_conversation_app.tools.tool_constants import ToolState
from reachy_mini_conversation_app.media_result_processor import ProcessedToolResult
from reachy_mini_conversation_app.tools.background_tool_manager import ToolCallRoutine, ToolNotification


def _build_handler(loop: asyncio.AbstractEventLoop) -> ConversationStreamHandler:
    asyncio.set_event_loop(loop)
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    return ConversationStreamHandler(deps)


def _set_openai_test_config(monkeypatch: Any) -> None:
    monkeypatch.setattr(stream_mod.config, "BACKEND_PROVIDER", stream_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_MODEL", "test-realtime-model")
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_VOICE", "cedar")


def _set_local_stt_test_config(monkeypatch: Any) -> None:
    monkeypatch.setattr(stream_mod.config, "BACKEND_PROVIDER", BACKEND_LOCAL_STT)
    monkeypatch.setattr(stream_mod.config, "CHAT_API_KEY", "test-key")
    monkeypatch.setattr(stream_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(stream_mod.config, "CHAT_MODEL_NAME", "test-chat-model")
    monkeypatch.setattr(stream_mod.config, "STT_API_KEY", "stt-key")
    monkeypatch.setattr(stream_mod.config, "STT_BASE_URL", "https://stt.test/v1")
    monkeypatch.setattr(stream_mod.config, "STT_MODEL_NAME", "test-stt-model")
    monkeypatch.setattr(stream_mod.config, "TTS_API_KEY", "tts-key")
    monkeypatch.setattr(stream_mod.config, "TTS_BASE_URL", "https://tts.test/v1")
    monkeypatch.setattr(stream_mod.config, "TTS_MODEL_NAME", "test-tts-model")
    monkeypatch.setattr(stream_mod.config, "TTS_VOICE", "cedar")


def _set_hf_realtime_test_config(monkeypatch: Any) -> None:
    monkeypatch.setattr(stream_mod.config, "BACKEND_PROVIDER", BACKEND_HF_REALTIME)
    monkeypatch.setattr(stream_mod.config, "HF_REALTIME_CONNECTION_MODE", HF_REALTIME_CONNECTION_LOCAL)
    monkeypatch.setattr(
        stream_mod.config,
        "HF_REALTIME_WS_URL",
        "ws://localhost:8765/v1/realtime?session_id=session_123&model=ignored",
    )
    monkeypatch.setattr(stream_mod.config, "HF_REALTIME_MODEL", "hf-test-model")
    monkeypatch.setattr(stream_mod.config, "HF_REALTIME_VOICE", "Aiden")
    monkeypatch.setattr(stream_mod.config, "HF_TOKEN", "hf-token")


def test_format_timestamp_uses_wall_clock() -> None:
    """Test that format_timestamp uses wall clock time."""
    loop = asyncio.new_event_loop()
    try:
        print("Testing format_timestamp...")
        handler = _build_handler(loop)
        formatted = handler.format_timestamp()
        print(f"Formatted timestamp: {formatted}")
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    # Extract year from "[YYYY-MM-DD ...]"
    year = int(formatted[1:5])
    assert year == datetime.now(timezone.utc).year


def test_model_io_logging_redacts_secrets_and_binary_payloads() -> None:
    """Detailed model logs keep useful text while omitting credentials and media bytes."""
    raw_base64 = "A" * 1_000

    logged = _model_io_json(
        {
            "api_key": "sk-secret-value",
            "text": "Describe this image in detail.",
            "image_url": f"data:image/jpeg;base64,{raw_base64}",
            "audio": raw_base64,
            "b64_images": [raw_base64, raw_base64],
        }
    )

    assert "sk-secret-value" not in logged
    assert raw_base64 not in logged
    assert "<redacted>" in logged
    assert "image/jpeg" in logged
    assert "estimated_bytes" in logged
    assert "Describe this image in detail." in logged


@pytest.mark.asyncio
async def test_focused_model_request_logging_uses_info(caplog: Any) -> None:
    """Focused model logs should work without enabling global DEBUG output."""
    caplog.set_level(logging.INFO)
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = ConversationStreamHandler(deps, model_logs=True)

    handler._log_model_request(
        "response.create",
        {"response": {"instructions": "Describe the image.", "tool_choice": "none"}},
    )

    assert "MODEL request type=response.create" in caplog.text
    assert "Describe the image." in caplog.text


def test_parse_hf_realtime_url_removes_realtime_path_and_preserves_query() -> None:
    """HF realtime URLs are split into OpenAI-compatible HTTP and websocket bases."""
    parsed = realtime_mod.parse_hf_realtime_url("wss://example.test/v1/realtime?session_id=session_123&model=ignored")

    assert parsed.base_url == "https://example.test/v1"
    assert parsed.websocket_base_url == "wss://example.test/v1"
    assert parsed.connect_query == {"session_id": "session_123"}
    assert parsed.has_realtime_path is True


def test_prepare_mono_int16_audio_accepts_channel_first_float64() -> None:
    """Browser mic frames can arrive as normalized float64 channel-first audio."""
    audio_frame = np.array([[0.0, 0.5, -0.5]], dtype=np.float64)

    prepared = prepare_mono_int16_audio((16000, audio_frame), 16000)

    assert prepared.dtype == np.int16
    assert prepared.ndim == 1
    assert prepared.shape == (3,)
    assert prepared[1] > 10_000
    assert prepared[2] < -10_000


def test_prepare_mono_int16_audio_accepts_stereo_channel_layouts() -> None:
    """Both common stereo layouts should select the first channel predictably."""
    first_channel = np.array([0.0, 0.25, -0.25, 0.5], dtype=np.float32)
    second_channel = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)

    channel_first = np.vstack([first_channel, second_channel])
    channel_last = np.column_stack([first_channel, second_channel])

    prepared_first = prepare_mono_int16_audio(
        (16000, channel_first),
        16000,
    )
    prepared_last = prepare_mono_int16_audio(
        (16000, channel_last),
        16000,
    )

    assert prepared_first.tolist() == prepared_last.tolist()
    assert prepared_first.shape == (4,)
    assert prepared_first[-1] > 10_000


def test_prepare_mono_int16_audio_preserves_resampled_pcm_scale() -> None:
    """Scipy resampling returns float64 PCM-scale values that should not be amplified again."""
    audio_frame = np.full(4800, 2000, dtype=np.int16)

    prepared = prepare_mono_int16_audio((48000, audio_frame), 16000)

    assert prepared.dtype == np.int16
    assert prepared.shape == (1600,)
    assert 1_900 <= int(np.median(prepared)) <= 2_100


@pytest.mark.asyncio
async def test_build_realtime_client_supports_local_hf_endpoint(monkeypatch: Any) -> None:
    """HF realtime local mode uses the configured websocket URL and connect query."""
    _set_hf_realtime_test_config(monkeypatch)
    client_kwargs: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            client_kwargs.update(kwargs)

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    client = await handler._build_realtime_client("hf-token")

    assert isinstance(client, FakeClient)
    assert client_kwargs == {
        "api_key": "hf-token",
        "base_url": "http://localhost:8765/v1",
        "websocket_base_url": "ws://localhost:8765/v1",
    }
    assert handler._realtime_connect_query == {"session_id": "session_123"}


@pytest.mark.asyncio
async def test_hf_realtime_session_uses_configured_model_and_connect_query(monkeypatch: Any) -> None:
    """HF realtime sessions pass the selected model separately from session query params."""
    _set_hf_realtime_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod, "get_session_instructions", lambda: "test instructions")
    monkeypatch.setattr(stream_mod, "get_tool_specs_for_dependencies", lambda _deps: [])

    connect_calls: list[dict[str, Any]] = []
    session_updates: list[dict[str, Any]] = []

    class FakeSession:
        async def update(self, **kwargs: Any) -> None:
            session_updates.append(kwargs)

    class FakeConn:
        session = FakeSession()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> None:
            raise StopAsyncIteration

    class FakeRealtime:
        def connect(self, **kwargs: Any) -> FakeConn:
            connect_calls.append(kwargs)
            return FakeConn()

    class FakeClient:
        realtime = FakeRealtime()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.client = cast(Any, FakeClient())
    handler._realtime_connect_query = {"session_id": "session_123"}

    await handler._run_realtime_session()

    assert connect_calls == [
        {
            "model": "hf-test-model",
            "extra_query": {"session_id": "session_123"},
        }
    ]
    assert session_updates
    session_config = session_updates[0]["session"]
    assert session_config["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": None}
    assert session_config["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": None}
    assert handler.connection is None


@pytest.mark.asyncio
async def test_transport_discovery_precedes_realtime_configuration(monkeypatch: Any) -> None:
    """Remote schemas must be discovered before the model session is configured."""
    _set_openai_test_config(monkeypatch)
    events: list[str] = []
    session_updates: list[dict[str, Any]] = []

    class FakeTransport:
        async def list_tools(self) -> list[dict[str, Any]]:
            events.append("tools/list")
            return [
                {
                    "type": "function",
                    "name": "move_head",
                    "description": "Move Reachy's head",
                    "parameters": {"type": "object"},
                }
            ]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {"name": name, "arguments": arguments}

        async def close(self) -> None:
            events.append("transport.close")

    class FakeSession:
        async def update(self, **kwargs: Any) -> None:
            events.append("session.update")
            session_updates.append(kwargs)

    class FakeConn:
        session = FakeSession()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> None:
            raise StopAsyncIteration

    class FakeRealtime:
        def connect(self, **_kwargs: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        realtime = FakeRealtime()

    handler = ConversationStreamHandler(ToolDependencies(), tool_transport=FakeTransport())
    handler.client = cast(Any, FakeClient())

    await handler._run_realtime_session()
    await handler.shutdown()

    assert events == ["tools/list", "session.update", "transport.close"]
    assert session_updates[0]["session"]["tools"][0]["name"] == "move_head"


@pytest.mark.asyncio
async def test_hf_start_up_refreshes_client_between_realtime_retries(monkeypatch: Any) -> None:
    """HF session retries should allocate a fresh client/connect URL after abrupt closes."""
    _set_hf_realtime_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod, "get_session_instructions", lambda: "test instructions")
    monkeypatch.setattr(stream_mod, "get_tool_specs_for_dependencies", lambda _deps: [])

    fake_closed_error = type("FakeConnectionClosedError", (Exception,), {})
    monkeypatch.setattr(stream_mod, "ConnectionClosedError", fake_closed_error)

    real_sleep = asyncio.sleep

    async def fast_sleep(*_args: Any, **_kwargs: Any) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep, raising=False)

    build_client_ids: list[int] = []
    connect_client_ids: list[int] = []

    class FakeSession:
        async def update(self, **_kwargs: Any) -> None:
            return None

    class FakeConn:
        session = FakeSession()

        def __init__(self, client_id: int) -> None:
            self.client_id = client_id

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> None:
            if self.client_id == 1:
                raise fake_closed_error("stale HF connect URL")
            raise StopAsyncIteration

    class FakeRealtime:
        def __init__(self, client_id: int) -> None:
            self.client_id = client_id

        def connect(self, **_kwargs: Any) -> FakeConn:
            connect_client_ids.append(self.client_id)
            return FakeConn(self.client_id)

    class FakeClient:
        def __init__(self, client_id: int) -> None:
            self.realtime = FakeRealtime(client_id)

    async def fake_build_realtime_client(_api_key: str) -> FakeClient:
        client_id = len(build_client_ids) + 1
        build_client_ids.append(client_id)
        return FakeClient(client_id)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    monkeypatch.setattr(handler, "_build_realtime_client", fake_build_realtime_client)

    await handler.start_up()

    assert build_client_ids == [1, 2]
    assert connect_client_ids == [1, 2]
    assert handler.connection is None


@pytest.mark.asyncio
async def test_hf_restart_session_refreshes_realtime_client(monkeypatch: Any) -> None:
    """HF restarts should refresh the session-backed realtime client before reconnecting."""
    _set_hf_realtime_test_config(monkeypatch)

    built_with_keys: list[str] = []
    run_calls = 0

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class FakeClient:
        pass

    async def fake_build_realtime_client(api_key: str) -> FakeClient:
        built_with_keys.append(api_key)
        return FakeClient()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    previous_client = FakeClient()
    connection = FakeConnection()
    handler.client = cast(Any, previous_client)
    handler.connection = connection
    monkeypatch.setattr(handler, "_build_realtime_client", fake_build_realtime_client)

    async def fake_run_realtime_session() -> None:
        nonlocal run_calls
        run_calls += 1
        handler._connected_event.set()

    monkeypatch.setattr(handler, "_run_realtime_session", fake_run_realtime_session)

    await handler._restart_session()

    assert connection.closed is True
    assert built_with_keys == ["hf-token"]
    assert handler.client is not previous_client
    assert run_calls == 1


@pytest.mark.asyncio
async def test_start_up_retries_on_abrupt_close(monkeypatch: Any, caplog: Any) -> None:
    """First connection dies with ConnectionClosedError during iteration -> retried.

    Second connection iterates cleanly (no events) -> start_up returns without raising.
    Ensures handler clears self.connection at the end.
    """
    caplog.set_level(logging.WARNING)
    _set_openai_test_config(monkeypatch)

    # Use a local Exception as the module's ConnectionClosedError to avoid ws dependency
    FakeCCE = type("FakeCCE", (Exception,), {})
    monkeypatch.setattr(stream_mod, "ConnectionClosedError", FakeCCE)

    # Make asyncio.sleep return immediately (for backoff)
    _real_sleep = asyncio.sleep

    async def _mock_sleep(*_a: Any, **_kw: Any) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _mock_sleep, raising=False)

    attempt_counter = {"n": 0}

    class FakeConn:
        """Minimal realtime connection stub."""

        def __init__(self, mode: str):
            self._mode = mode

            class _Session:
                async def update(self, **_kw: Any) -> None:
                    return None

            self.session = _Session()

            class _InputAudioBuffer:
                async def append(self, **_kw: Any) -> None:
                    return None

            self.input_audio_buffer = _InputAudioBuffer()

            class _Item:
                async def create(self, **_kw: Any) -> None:
                    return None

            class _Conversation:
                item = _Item()

            self.conversation = _Conversation()

            class _Response:
                async def create(self, **_kw: Any) -> None:
                    return None

                async def cancel(self, **_kw: Any) -> None:
                    return None

            self.response = _Response()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def close(self) -> None:
            return None

        # Async iterator protocol
        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> None:
            if self._mode == "raise_on_iter":
                raise FakeCCE("abrupt close (simulated)")
            raise StopAsyncIteration  # clean exit (no events)

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            attempt_counter["n"] += 1
            mode = "raise_on_iter" if attempt_counter["n"] == 1 else "clean"
            return FakeConn(mode)

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    # Patch the OpenAI client used by the handler
    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    # Build handler with minimal deps
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    # Run: should retry once and exit cleanly
    await handler.start_up()

    # Validate: two attempts total (fail -> retry -> succeed), and connection cleared
    assert attempt_counter["n"] == 2
    assert handler.connection is None

    # Optional: confirm we logged the unexpected close once
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "closed unexpectedly" in r.msg]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_start_up_passes_env_base_url_to_openai_client(monkeypatch: Any) -> None:
    """OpenAI Realtime key and base URL come from config and are passed to the SDK."""
    _set_openai_test_config(monkeypatch)
    client_kwargs: dict[str, Any] = {}

    class FakeConn:
        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> None:
            raise StopAsyncIteration

        class _Session:
            async def update(self, **_kw: Any) -> None:
                return None

        session = _Session()

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            client_kwargs.update(kwargs)
            self.realtime = FakeRealtime()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    await handler.start_up()

    assert client_kwargs == {
        "api_key": "test-key",
        "base_url": "https://example.test/v1",
    }


@pytest.mark.asyncio
async def test_send_text_message_creates_input_text_and_collects_response(monkeypatch: Any) -> None:
    """Text mode sends a user input_text item and returns chatbot updates."""
    _set_openai_test_config(monkeypatch)
    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    created_items: list[dict[str, Any]] = []

    class FakeEvent:
        def __init__(self, etype: str, **kwargs: Any) -> None:
            self.type = etype
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FakeSession:
        async def update(self, **_kw: Any) -> None:
            return None

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            created_items.append(kwargs["item"])

    class FakeConversation:
        item = FakeConversationItem()

    class FakeResponse:
        async def create(self, **_kw: Any) -> None:
            await event_queue.put(FakeEvent("response.created"))
            await event_queue.put(
                FakeEvent("response.output_audio_transcript.done", transcript="Hello from text mode.")
            )
            await event_queue.put(FakeEvent("response.done", response=MagicMock(usage=None)))

        async def cancel(self, **_kw: Any) -> None:
            return None

    class FakeInputAudioBuffer:
        async def append(self, **_kw: Any) -> None:
            return None

    class FakeConn:
        session = FakeSession()
        conversation = FakeConversation()
        response = FakeResponse()
        input_audio_buffer = FakeInputAudioBuffer()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> Any:
            event = await event_queue.get()
            if event is None:
                raise StopAsyncIteration
            return event

        async def close(self) -> None:
            await event_queue.put(None)

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Hello typed Reachy")
    await handler.shutdown()
    if handler._realtime_startup_task is not None:
        await asyncio.wait_for(handler._realtime_startup_task, timeout=1)

    assert created_items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello typed Reachy"}],
        }
    ]
    assert messages == [
        {"role": "user", "content": "Hello typed Reachy"},
        {"role": "assistant", "content": "Hello from text mode."},
    ]


@pytest.mark.asyncio
async def test_receive_lazily_starts_realtime_and_appends_microphone_audio(monkeypatch: Any) -> None:
    """Mic frames should start the Realtime session before appending audio."""
    _set_openai_test_config(monkeypatch)
    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    appended_audio: list[str] = []

    class FakeSession:
        async def update(self, **_kw: Any) -> None:
            return None

    class FakeInputAudioBuffer:
        async def append(self, **kwargs: Any) -> None:
            appended_audio.append(kwargs["audio"])

    class FakeConn:
        session = FakeSession()
        input_audio_buffer = FakeInputAudioBuffer()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> Any:
            event = await event_queue.get()
            if event is None:
                raise StopAsyncIteration
            return event

        async def close(self) -> None:
            await event_queue.put(None)

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    audio_frame = np.array([0, 1000, -1000, 0], dtype=np.int16)

    await handler.receive((24000, audio_frame))
    await handler.shutdown()
    if handler._realtime_startup_task is not None:
        await asyncio.wait_for(handler._realtime_startup_task, timeout=1)

    assert appended_audio == [base64.b64encode(audio_frame.tobytes()).decode("utf-8")]


@pytest.mark.asyncio
async def test_realtime_tool_call_waits_for_tool_result_before_response_create(monkeypatch: Any) -> None:
    """Realtime tool calls should not request a follow-up before function_call_output is available."""
    _set_openai_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod, "get_session_instructions", lambda: "test instructions")
    monkeypatch.setattr(stream_mod, "get_tool_specs_for_dependencies", lambda _deps: [])
    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    response_create_calls: list[dict[str, Any]] = []

    class FakeEvent:
        def __init__(self, etype: str, **kwargs: Any) -> None:
            self.type = etype
            for key, value in kwargs.items():
                setattr(self, key, value)

    class FakeSession:
        async def update(self, **_kw: Any) -> None:
            return None

    class FakeConversationItem:
        async def create(self, **_kwargs: Any) -> None:
            return None

    class FakeConversation:
        item = FakeConversationItem()

    class FakeResponse:
        async def create(self, **kwargs: Any) -> None:
            response_create_calls.append(kwargs)

        async def cancel(self, **_kw: Any) -> None:
            return None

    class FakeInputAudioBuffer:
        async def append(self, **_kw: Any) -> None:
            return None

    class FakeConn:
        session = FakeSession()
        conversation = FakeConversation()
        response = FakeResponse()
        input_audio_buffer = FakeInputAudioBuffer()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> Any:
            event = await event_queue.get()
            if event is None:
                raise StopAsyncIteration
            return event

        async def close(self) -> None:
            await event_queue.put(None)

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    async def fake_start_tool(*_args: Any, **_kwargs: Any) -> Any:
        class FakeBackgroundTool:
            tool_id = "sweep_look-call_1"

        return FakeBackgroundTool()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(stream_mod.BackgroundToolManager, "start_tool", fake_start_tool)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    startup_task = asyncio.create_task(handler.start_up())
    assert await handler._ensure_realtime_session("test-realtime-tool-call") is True

    await event_queue.put(
        FakeEvent(
            "response.function_call_arguments.done",
            name="sweep_look",
            arguments="{}",
            call_id="call_1",
        )
    )
    output = await asyncio.wait_for(handler.output_queue.get(), timeout=1)

    await handler.shutdown()
    await event_queue.put(None)
    await asyncio.wait_for(startup_task, timeout=1)

    assert isinstance(output, stream_mod.AdditionalOutputs)
    assert output.args[0]["content"].startswith("🛠️ Used tool sweep_look")
    assert response_create_calls == []


@pytest.mark.asyncio
async def test_camera_result_sends_image_separately_from_function_output() -> None:
    """Camera bytes belong in input_image, not the text function result."""
    item_create_calls: list[dict[str, Any]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_camera",
            tool_name="camera",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={
                "b64_im": "jpeg-base64-data",
                "question": "What is the person doing?",
            },
        )
    )

    function_output = item_create_calls[0]["item"]
    image_message = item_create_calls[1]["item"]
    parsed_output = json.loads(function_output["output"])
    queued_response = handler._pending_responses.get_nowait()

    assert function_output["type"] == "function_call_output"
    assert parsed_output == {
        "question": "What is the person doing?",
        "status": "image_captured",
    }
    assert "jpeg-base64-data" not in function_output["output"]
    assert image_message["content"] == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,jpeg-base64-data",
        }
    ]
    assert queued_response["response"]["tool_choice"] == "none"
    assert "input image" in queued_response["response"]["instructions"]


@pytest.mark.asyncio
async def test_camera_processor_routes_raw_image_before_realtime() -> None:
    """Configured routing should send Realtime only the approved text result."""
    item_create_calls: list[dict[str, Any]] = []
    processor_calls: list[tuple[str, dict[str, Any]]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    class FakeProcessor:
        async def process(self, tool_name: str, result: dict[str, Any]) -> ProcessedToolResult:
            processor_calls.append((tool_name, result))
            return ProcessedToolResult(
                model_payload={
                    "status": "image_analyzed",
                    "question": result["question"],
                    "image_description": "The person is waving.",
                    "selected_model": "approved-vision-model",
                }
            )

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(
        deps,
        media_result_processor=cast(Any, FakeProcessor()),
    )
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_camera_routed_before_realtime",
            tool_name="camera",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={
                "b64_im": "raw-camera-bytes",
                "question": "What am I doing?",
            },
        )
    )

    assert processor_calls == [
        (
            "camera",
            {
                "b64_im": "raw-camera-bytes",
                "question": "What am I doing?",
            },
        )
    ]
    assert len(item_create_calls) == 1
    function_output = item_create_calls[0]["item"]
    assert function_output["type"] == "function_call_output"
    assert "raw-camera-bytes" not in function_output["output"]
    assert json.loads(function_output["output"])["image_description"] == "The person is waving."
    queued_response = handler._pending_responses.get_nowait()
    assert "approved vision model" in queued_response["response"]["instructions"]


@pytest.mark.asyncio
async def test_interrupted_routed_scan_is_explained_as_partial() -> None:
    """The follow-up must not describe interrupted scan frames as a complete room scan."""
    item_create_calls: list[dict[str, Any]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    class FakeProcessor:
        async def process(self, tool_name: str, result: dict[str, Any]) -> ProcessedToolResult:
            assert tool_name == "scan_scene"
            assert result["scan_status"] == "scene_scan_incomplete"
            return ProcessedToolResult(
                model_payload={
                    "status": "scene_analyzed",
                    "scan_status": "scene_scan_incomplete",
                    "scan_warning": "Reachy lost its control connection during the sweep",
                    "returned_to_front": True,
                    "image_description": "The recorded frames show a desk and one chair.",
                    "selected_model": "approved-vision-model",
                }
            )

    handler = stream_mod.ConversationStreamHandler(
        ToolDependencies(),
        media_result_processor=cast(Any, FakeProcessor()),
    )
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_interrupted_scan",
            tool_name="scan_scene",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={
                "status": "scene_scan_incomplete",
                "scan_status": "scene_scan_incomplete",
                "scan_warning": "Reachy lost its control connection during the sweep",
                "returned_to_front": True,
                "question": "What did you see?",
                "frame_timestamps_seconds": [0.5],
                "b64_images": ["raw-frame"],
            },
        )
    )

    function_output = json.loads(item_create_calls[0]["item"]["output"])
    queued_response = handler._pending_responses.get_nowait()
    assert function_output["scan_status"] == "scene_scan_incomplete"
    assert function_output["returned_to_front"] is True
    assert "physical scene sweep was interrupted" in queued_response["response"]["instructions"]
    assert "Do not claim a complete room scan" in queued_response["response"]["instructions"]


@pytest.mark.asyncio
async def test_policy_denial_preserves_structured_transport_result() -> None:
    """Realtime should receive the policy status as well as its human-readable error."""
    item_create_calls: list[dict[str, Any]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    handler = stream_mod.ConversationStreamHandler(ToolDependencies())
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_dance",
            tool_name="dance",
            is_idle_tool_call=False,
            status=ToolState.FAILED,
            result={
                "status": "policy_denied",
                "tool": "dance",
                "error": "Blocked by OpenShell policy",
            },
            error="Blocked by OpenShell policy",
        )
    )

    function_output = item_create_calls[0]["item"]
    assert json.loads(function_output["output"]) == {
        "status": "policy_denied",
        "tool": "dance",
        "error": "Blocked by OpenShell policy",
    }
    tool_card = handler.output_queue.get_nowait().args[0]
    assert tool_card["metadata"]["title"] == "🚫 OpenShell blocked tool dance"
    queued_response = handler._pending_responses.get_nowait()
    assert "blocked by the OpenShell policy" in queued_response["response"]["instructions"]
    assert "Do not claim the robot lacks the physical capability" in queued_response["response"]["instructions"]


@pytest.mark.asyncio
async def test_routed_camera_result_returns_description_without_realtime_image() -> None:
    """A dedicated vision route should keep raw image bytes out of the Realtime session."""
    item_create_calls: list[dict[str, Any]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_camera_routed",
            tool_name="camera",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={
                "status": "image_analyzed",
                "image_description": "The person is waving.",
                "selected_model": "gpt-5.4-mini",
                "response_id": "resp_vision",
            },
        )
    )

    assert len(item_create_calls) == 1
    function_output = item_create_calls[0]["item"]
    parsed_output = json.loads(function_output["output"])
    queued_response = handler._pending_responses.get_nowait()

    assert function_output["type"] == "function_call_output"
    assert parsed_output["image_description"] == "The person is waving."
    assert parsed_output["selected_model"] == "gpt-5.4-mini"
    assert queued_response["response"]["tool_choice"] == "none"
    assert "approved vision model" in queued_response["response"]["instructions"]


@pytest.mark.asyncio
async def test_scene_scan_result_sends_chronological_images_and_video_preview(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Scene-scan media stays out of tool JSON and reaches the vision model as images."""
    item_create_calls: list[dict[str, Any]] = []

    class FakeConversationItem:
        async def create(self, **kwargs: Any) -> None:
            item_create_calls.append(kwargs)

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    video_path = tmp_path / "scene-scan.mp4"
    video_path.write_bytes(b"fake-mp4")
    monkeypatch.setattr(stream_mod.gr, "Video", lambda **kwargs: {"video": kwargs["value"]})

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()

    await handler._handle_tool_result(
        ToolNotification(
            id="call_scan",
            tool_name="scan_scene",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={
                "status": "scene_scan_complete",
                "question": "What did you see?",
                "video_path": str(video_path),
                "frame_timestamps_seconds": [0.5, 7.0],
                "b64_images": ["first-jpeg", "second-jpeg"],
            },
        )
    )

    function_output = item_create_calls[0]["item"]
    image_message = item_create_calls[1]["item"]
    parsed_output = json.loads(function_output["output"])
    queued_response = handler._pending_responses.get_nowait()

    assert "b64_images" not in parsed_output
    assert parsed_output["video_path"] == str(video_path)
    assert image_message["content"][0]["type"] == "input_text"
    assert "chronological" in image_message["content"][0]["text"]
    assert image_message["content"][1:] == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,first-jpeg",
        },
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,second-jpeg",
        },
    ]
    assert queued_response["response"]["tool_choice"] == "none"
    assert "every chronological image" in queued_response["response"]["instructions"]

    tool_card = await handler.output_queue.get()
    video_preview = await handler.output_queue.get()
    assert isinstance(tool_card, stream_mod.AdditionalOutputs)
    assert isinstance(video_preview, stream_mod.AdditionalOutputs)
    assert tool_card.args[0]["metadata"]["title"] == "🛠️ Used tool scan_scene"
    assert video_preview.args[0]["content"] == {"video": str(video_path)}


@pytest.mark.asyncio
async def test_typed_realtime_message_waits_for_answer_after_tool_updates(monkeypatch: Any) -> None:
    """A tool card is an intermediate update, not the final typed-chat answer."""
    monkeypatch.setattr(stream_mod, "backend_config_error", lambda: None)

    class FakeConversationItem:
        async def create(self, **_kwargs: Any) -> None:
            return None

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()
    monkeypatch.setattr(handler, "_text_model_uses_realtime", lambda: True)

    async def ensure_text_session() -> bool:
        return True

    monkeypatch.setattr(handler, "_ensure_text_session", ensure_text_session)
    handler._response_done_event.clear()

    send_task = asyncio.create_task(handler.send_text_message("Take a picture", timeout=2.0))
    await asyncio.sleep(0)
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "🛠️ Used tool camera with args {}. The tool is now running.",
        }
    )
    handler._response_done_event.set()

    await asyncio.sleep(0.05)
    assert not send_task.done()

    handler._response_done_event.clear()
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "You are sitting in front of the camera.",
        }
    )
    handler._response_done_event.set()

    messages = await asyncio.wait_for(send_task, timeout=1.0)

    assert messages[-1] == {
        "role": "assistant",
        "content": "You are sitting in front of the camera.",
    }


@pytest.mark.asyncio
async def test_typed_realtime_output_is_not_stolen_by_stream_consumer(monkeypatch: Any) -> None:
    """FastRTC and typed chat should each receive a copy of assistant text."""
    monkeypatch.setattr(stream_mod, "backend_config_error", lambda: None)

    class FakeConversationItem:
        async def create(self, **_kwargs: Any) -> None:
            return None

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()
    monkeypatch.setattr(handler, "_text_model_uses_realtime", lambda: True)

    async def ensure_text_session() -> bool:
        return True

    monkeypatch.setattr(handler, "_ensure_text_session", ensure_text_session)
    handler._response_done_event.clear()

    send_task = asyncio.create_task(handler.send_text_message("Describe the picture", timeout=2.0))
    await asyncio.sleep(0)
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "I can see a person standing by a desk.",
        }
    )

    stream_output = await asyncio.wait_for(handler.output_queue.get(), timeout=1.0)
    handler._response_done_event.set()
    messages = await asyncio.wait_for(send_task, timeout=1.0)

    assert isinstance(stream_output, stream_mod.AdditionalOutputs)
    assert stream_output.args[0]["content"] == "I can see a person standing by a desk."
    assert messages[-1]["content"] == "I can see a person standing by a desk."


@pytest.mark.asyncio
async def test_typed_realtime_waits_past_tool_commentary_for_followup(monkeypatch: Any) -> None:
    """Pre-tool commentary must not finish a typed turn before the tool's final answer."""
    monkeypatch.setattr(stream_mod, "backend_config_error", lambda: None)

    class FakeConversationItem:
        async def create(self, **_kwargs: Any) -> None:
            return None

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler.connection = FakeConnection()
    monkeypatch.setattr(handler, "_text_model_uses_realtime", lambda: True)

    async def ensure_text_session() -> bool:
        return True

    monkeypatch.setattr(handler, "_ensure_text_session", ensure_text_session)
    handler._response_done_event.clear()

    send_task = asyncio.create_task(handler.send_text_message("What am I doing?", timeout=2.0))
    await asyncio.sleep(0)
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "Let me check the camera.",
        }
    )
    handler._typed_tool_calls_awaiting_followup.add("call_camera")
    handler._typed_followup_call_order.append("call_camera")
    handler._tool_call_response_ids.add("resp_tool_call")
    handler._response_done_event.set()

    await asyncio.sleep(0.05)
    assert not send_task.done()

    handler._response_done_event.clear()
    handler._mark_typed_followup_response("resp_camera_answer")
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "You are sitting at a desk with one hand near your mouth.",
        }
    )
    handler._response_done_event.set()

    messages = await asyncio.wait_for(send_task, timeout=1.0)

    assert messages[-1]["content"] == "You are sitting at a desk with one hand near your mouth."


@pytest.mark.asyncio
async def test_typed_realtime_uses_longer_timeout_while_tool_is_pending(monkeypatch: Any) -> None:
    """A long scan should stay attached after the normal response timeout expires."""
    monkeypatch.setattr(stream_mod, "backend_config_error", lambda: None)

    class FakeConversationItem:
        async def create(self, **_kwargs: Any) -> None:
            return None

    class FakeConversation:
        item = FakeConversationItem()

    class FakeConnection:
        conversation = FakeConversation()

    handler = stream_mod.ConversationStreamHandler(ToolDependencies())
    handler.connection = FakeConnection()
    monkeypatch.setattr(handler, "_text_model_uses_realtime", lambda: True)

    async def ensure_text_session() -> bool:
        return True

    monkeypatch.setattr(handler, "_ensure_text_session", ensure_text_session)
    handler._response_done_event.clear()

    send_task = asyncio.create_task(handler.send_text_message("Scan the room", timeout=0.05, tool_timeout=0.5))
    await asyncio.sleep(0)
    await handler._publish_chat_output({"role": "assistant", "content": "Okay, I’ll scan the room."})
    handler._typed_tool_calls_awaiting_followup.add("call_scan")
    handler._typed_followup_call_order.append("call_scan")
    handler._response_done_event.set()

    await asyncio.sleep(0.1)
    assert not send_task.done()

    handler._response_done_event.clear()
    handler._mark_typed_followup_response("resp_scan_answer")
    await handler._publish_chat_output(
        {
            "role": "assistant",
            "content": "I saw a desk, a chair, and a clear walking path.",
        }
    )
    handler._response_done_event.set()

    messages = await asyncio.wait_for(send_task, timeout=1.0)

    assert messages[-1]["content"] == "I saw a desk, a chair, and a clear walking path."
    assert not any(message.get("content", "").startswith("[error] Timed out") for message in messages)


def test_response_message_text_extracts_audio_transcript_fallback() -> None:
    """response.done can recover text when a backend omits transcript.done."""

    class Content:
        transcript = "I can see someone wearing a blue shirt."
        text = None

    class Message:
        type = "message"
        role = "assistant"
        content = [Content()]

    class Response:
        output = [Message()]

    assert ConversationStreamHandler._response_message_text(Response()) == "I can see someone wearing a blue shirt."


@pytest.mark.asyncio
async def test_receive_transcribes_microphone_audio_for_non_realtime_model(monkeypatch: Any) -> None:
    """Local-STT mic mode transcribes speech and sends the transcript through Chat Completions."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(stream_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(stream_mod.config, "CHAT_MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.setattr(stream_mod.config, "STT_API_KEY", "stt-key")
    monkeypatch.setattr(stream_mod.config, "STT_BASE_URL", "http://dgx-spark.test/v1")
    monkeypatch.setattr(stream_mod.config, "STT_MODEL_NAME", "whisper-large-v3")
    monkeypatch.setattr(stream_mod.config, "TTS_API_KEY", "tts-key")
    monkeypatch.setattr(stream_mod.config, "TTS_BASE_URL", "https://tts.test/v1")
    monkeypatch.setattr(stream_mod.config, "TTS_MODEL_NAME", "gpt-4o-mini-tts")
    monkeypatch.setattr(stream_mod.config, "TTS_VOICE", "cedar")
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_RMS_THRESHOLD", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_MIN_AUDIO_MS", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_SILENCE_MS", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_MAX_AUDIO_MS", 10_000.0)
    client_kwargs: list[dict[str, Any]] = []
    stt_calls: list[dict[str, Any]] = []
    chat_calls: list[dict[str, Any]] = []
    speech_calls: list[dict[str, Any]] = []

    class FakeTranscriptions:
        async def create(self, **kwargs: Any) -> dict[str, str]:
            stt_calls.append(kwargs)
            return {"text": "Hi Reachy"}

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeSpeech:
        async def create(self, **kwargs: Any) -> Any:
            speech_calls.append(kwargs)

            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1000, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeTtsAudio:
        speech = FakeSpeech()

    class FakeMessage:
        content = "Hello from the mic transcript."
        tool_calls: list[Any] = []

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            chat_calls.append(kwargs)
            return FakeCompletion()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            client_kwargs.append(kwargs)
            if kwargs["base_url"] == "http://dgx-spark.test/v1":
                self.audio = FakeAudio()
            elif kwargs["base_url"] == "https://tts.test/v1":
                self.audio = FakeTtsAudio()
            else:
                self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    await handler.receive((24000, np.full((1, 2400), 0.25, dtype=np.float64)))
    await handler.receive((24000, np.zeros((1, 2400), dtype=np.float64)))

    outputs: list[stream_mod.AdditionalOutputs] = []
    for _ in range(2):
        output = await asyncio.wait_for(handler.output_queue.get(), timeout=1)
        assert isinstance(output, stream_mod.AdditionalOutputs)
        outputs.append(output)
    audio_output = await asyncio.wait_for(handler.output_queue.get(), timeout=1)
    messages = [output.args[0] for output in outputs]

    assert messages == [
        {"role": "user", "content": "Hi Reachy"},
        {"role": "assistant", "content": "Hello from the mic transcript."},
    ]
    assert handler.connection is None
    assert client_kwargs == [
        {"api_key": "stt-key", "base_url": "http://dgx-spark.test/v1"},
        {"api_key": "chat-key", "base_url": "https://chat.test/v1"},
        {"api_key": "tts-key", "base_url": "https://tts.test/v1"},
    ]
    assert stt_calls[0]["model"] == "whisper-large-v3"
    assert stt_calls[0]["file"][0] == "microphone.wav"
    assert stt_calls[0]["file"][1].startswith(b"RIFF")
    assert stt_calls[0]["file"][2] == "audio/wav"
    assert chat_calls[0]["messages"][-1] == {"role": "user", "content": "Hi Reachy"}
    assert speech_calls[0]["model"] == "gpt-4o-mini-tts"
    assert speech_calls[0]["voice"] == "cedar"
    assert speech_calls[0]["input"] == "Hello from the mic transcript."
    assert isinstance(audio_output, tuple)
    assert audio_output[0] == 24000


@pytest.mark.asyncio
async def test_receive_local_stt_microphone_audio_runs_tool_and_speaks(monkeypatch: Any) -> None:
    """Mic local-STT mode can transcribe speech, run a real Reachy tool, and synthesize the answer."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_RMS_THRESHOLD", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_MIN_AUDIO_MS", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_SILENCE_MS", 1.0)
    monkeypatch.setattr(stream_mod.config, "MIC_TRANSCRIPTION_MAX_AUDIO_MS", 10_000.0)
    chat_calls: list[dict[str, Any]] = []
    speech_calls: list[dict[str, Any]] = []

    class FakeTranscription:
        text = "Reachy, look around and then tell me what you did."

    class FakeTranscriptions:
        async def create(self, **_kwargs: Any) -> FakeTranscription:
            return FakeTranscription()

    class FakeSttAudio:
        transcriptions = FakeTranscriptions()

    class FakeSpeech:
        async def create(self, **kwargs: Any) -> Any:
            speech_calls.append(kwargs)

            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1200, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeTtsAudio:
        speech = FakeSpeech()

    class FakeFunction:
        name = "sweep_look"
        arguments = "{}"

    class FakeToolCall:
        id = "call_sweep"
        type = "function"
        function = FakeFunction()

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeToolMessage:
        content = ""
        tool_calls = [FakeToolCall()]

    class FakeFinalMessage:
        content = "I swept my gaze left and right, then returned to center."
        tool_calls: list[Any] = []

    class FakeChoice:
        def __init__(self, message: Any) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: Any) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            chat_calls.append(kwargs)
            if len(chat_calls) == 1:
                return FakeCompletion(FakeToolMessage())
            return FakeCompletion(FakeFinalMessage())

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            if kwargs["base_url"] == stream_mod.config.STT_BASE_URL:
                self.audio = FakeSttAudio()
            elif kwargs["base_url"] == stream_mod.config.TTS_BASE_URL:
                self.audio = FakeTtsAudio()
            else:
                self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    reachy = MagicMock()
    reachy.get_current_head_pose.return_value = np.eye(4, dtype=np.float64)
    reachy.get_current_joint_positions.return_value = (
        np.array([0.0], dtype=np.float64),
        np.array([0.0, 0.0], dtype=np.float64),
    )
    movement_manager = MagicMock()
    deps = ToolDependencies(reachy_mini=reachy, movement_manager=movement_manager)
    handler = stream_mod.ConversationStreamHandler(deps)

    await handler.receive((24000, np.full(2400, 2000, dtype=np.int16)))
    await handler.receive((24000, np.zeros(2400, dtype=np.int16)))

    outputs: list[Any] = []
    for _ in range(4):
        outputs.append(await asyncio.wait_for(handler.output_queue.get(), timeout=1))

    messages = [output.args[0] for output in outputs if isinstance(output, stream_mod.AdditionalOutputs)]
    audio_outputs = [output for output in outputs if isinstance(output, tuple)]

    assert messages[0] == {"role": "user", "content": "Reachy, look around and then tell me what you did."}
    assert messages[1]["metadata"] == {"title": "Used tool sweep_look", "status": "done"}
    assert "sweeping look left-right-center" in messages[1]["content"]
    assert messages[2] == {
        "role": "assistant",
        "content": "I swept my gaze left and right, then returned to center.",
    }
    assert len(audio_outputs) == 1
    assert audio_outputs[0][0] == 24000
    assert movement_manager.clear_move_queue.call_count == 1
    assert movement_manager.queue_move.call_count == 6
    assert movement_manager.set_moving_state.call_count == 1
    assert speech_calls[0]["input"] == "I swept my gaze left and right, then returned to center."
    assert any(
        message["role"] == "tool" and message["tool_call_id"] == "call_sweep" for message in chat_calls[1]["messages"]
    )


@pytest.mark.asyncio
async def test_send_text_message_reports_missing_dotenv_key(monkeypatch: Any) -> None:
    """Text mode reports the actual missing-key problem instead of a generic connection error."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "CHAT_API_KEY", "")

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Hello")

    assert len(messages) == 1
    assert "CHAT_API_KEY is missing for BACKEND_PROVIDER=local_stt" in messages[0]["content"]
    assert "NVIDIA_INFERENCE_API_KEY" in messages[0]["content"]


@pytest.mark.asyncio
async def test_send_text_message_reports_missing_backend_provider(monkeypatch: Any) -> None:
    """Text mode should report selector errors before trying a backend-specific path."""
    monkeypatch.setattr(stream_mod.config, "BACKEND_PROVIDER", "")

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Hello")

    assert len(messages) == 1
    assert "BACKEND_PROVIDER is missing" in messages[0]["content"]


@pytest.mark.asyncio
async def test_send_text_message_reports_realtime_startup_failure(monkeypatch: Any) -> None:
    """Text mode includes model/base URL context when the Realtime provider rejects startup."""
    _set_openai_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(stream_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")

    class FakeSession:
        async def update(self, **_kw: Any) -> None:
            raise RuntimeError("404 not found")

    class FakeConn:
        session = FakeSession()

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Hello")

    assert len(messages) == 1
    content = messages[0]["content"]
    assert "Realtime session.update failed" in content
    assert "gpt-realtime" in content
    assert "https://api.openai.com/v1" in content


@pytest.mark.asyncio
async def test_send_text_message_uses_chat_completions_for_non_realtime_model(monkeypatch: Any) -> None:
    """Local-STT text input uses Chat Completions instead of the Realtime websocket."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "CHAT_MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
    create_calls: list[dict[str, Any]] = []

    class FakeMessage:
        content = "Hello from Nemotron."
        tool_calls: list[Any] = []

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            return FakeCompletion()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Hello")

    assert messages == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello from Nemotron."},
    ]
    assert len(create_calls) == 1
    assert create_calls[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert create_calls[0]["messages"][-1] == {"role": "user", "content": "Hello"}


@pytest.mark.asyncio
async def test_chat_completion_tool_follow_up_keeps_tools_param(monkeypatch: Any) -> None:
    """Anthropic-compatible backends require tools= on the follow-up after a tool result."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "CHAT_MODEL_NAME", "azure/anthropic/claude-opus-4-8")
    monkeypatch.setattr(
        chat_mod,
        "get_tool_specs_for_dependencies",
        lambda _deps: [
            {
                "type": "function",
                "name": "dance",
                "description": "Dance once.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )

    async def fake_dispatch_tool_call_with_manager(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch_tool_call_with_manager)
    create_calls: list[dict[str, Any]] = []

    class FakeFunction:
        name = "dance"
        arguments = "{}"

    class FakeToolCall:
        id = "call_1"
        type = "function"
        function = FakeFunction()

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeToolMessage:
        content = ""
        tool_calls = [FakeToolCall()]

    class FakeFinalMessage:
        content = "I danced."
        tool_calls: list[Any] = []

    class FakeChoice:
        def __init__(self, message: Any) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: Any) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(FakeToolMessage())
            return FakeCompletion(FakeFinalMessage())

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Dance")

    assert messages[-1] == {"role": "assistant", "content": "I danced."}
    assert len(create_calls) == 2
    assert create_calls[0]["tools"]
    assert create_calls[1]["tools"] == create_calls[0]["tools"]
    assert any(message["role"] == "tool" for message in create_calls[1]["messages"])


@pytest.mark.asyncio
async def test_chat_completion_supports_multiple_tool_rounds(monkeypatch: Any) -> None:
    """The local STT/chat backend can continue through several tool-call rounds."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(
        chat_mod,
        "get_tool_specs_for_dependencies",
        lambda _deps: [
            {
                "type": "function",
                "name": "play_emotion",
                "description": "Play an emotion.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "function",
                "name": "move_head",
                "description": "Move the head.",
                "parameters": {"type": "object", "properties": {}},
            },
        ],
    )
    dispatched_tools: list[tuple[str, str]] = []

    async def fake_dispatch_tool_call_with_manager(
        tool_name: str, args_json: str, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        dispatched_tools.append((tool_name, args_json))
        return {"status": "queued", "tool": tool_name}

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch_tool_call_with_manager)
    create_calls: list[dict[str, Any]] = []

    class FakeFunction:
        def __init__(self, name: str, arguments: str) -> None:
            self.name = name
            self.arguments = arguments

    class FakeToolCall:
        def __init__(self, call_id: str, name: str, arguments: str) -> None:
            self.id = call_id
            self.type = "function"
            self.function = FakeFunction(name, arguments)

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeMessage:
        def __init__(self, content: str, tool_calls: list[Any] | None = None) -> None:
            self.content = content
            self.tool_calls = tool_calls or []

    class FakeChoice:
        def __init__(self, message: FakeMessage) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: FakeMessage) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(FakeMessage("", [FakeToolCall("call_1", "play_emotion", '{"emotion":"happy"}')]))
            if len(create_calls) == 2:
                return FakeCompletion(FakeMessage("", [FakeToolCall("call_2", "move_head", '{"direction":"left"}')]))
            return FakeCompletion(FakeMessage("I smiled and looked left."))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Smile and look left")

    assert dispatched_tools == [
        ("play_emotion", '{"emotion":"happy"}'),
        ("move_head", '{"direction":"left"}'),
    ]
    assert messages[-1] == {"role": "assistant", "content": "I smiled and looked left."}
    assert [message.get("metadata", {}).get("title") for message in messages if message.get("metadata")] == [
        "Used tool play_emotion",
        "Used tool move_head",
    ]
    assert len(create_calls) == 3
    assert any(
        message["role"] == "tool" and message["tool_call_id"] == "call_1" for message in create_calls[1]["messages"]
    )
    assert any(
        message["role"] == "tool" and message["tool_call_id"] == "call_2" for message in create_calls[2]["messages"]
    )


@pytest.mark.asyncio
async def test_chat_completion_accepts_dict_shaped_messages_and_tool_calls(monkeypatch: Any) -> None:
    """Some OpenAI-compatible proxies return plain dict choices/messages instead of SDK objects."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(
        chat_mod,
        "get_tool_specs_for_dependencies",
        lambda _deps: [
            {
                "type": "function",
                "name": "play_emotion",
                "description": "Play an emotion.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    dispatched_tools: list[tuple[str, str]] = []

    async def fake_dispatch_tool_call_with_manager(
        tool_name: str, args_json: str, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        dispatched_tools.append((tool_name, args_json))
        return {"status": "queued"}

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch_tool_call_with_manager)
    create_calls: list[dict[str, Any]] = []

    class FakeCompletion:
        def __init__(self, message: dict[str, Any]) -> None:
            self.choices = [{"message": message}]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(
                    {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_emotion",
                                "type": "function",
                                "function": {
                                    "name": "play_emotion",
                                    "arguments": '{"emotion":"welcoming1"}',
                                },
                            }
                        ],
                    }
                )
            return FakeCompletion({"content": "I welcomed you.", "tool_calls": []})

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Introduce yourself")

    assert dispatched_tools == [("play_emotion", '{"emotion":"welcoming1"}')]
    assert messages[-1] == {"role": "assistant", "content": "I welcomed you."}
    assert create_calls[1]["messages"][2]["tool_calls"][0]["function"]["name"] == "play_emotion"
    assert create_calls[1]["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_emotion",
        "content": '{"status": "queued"}',
    }


@pytest.mark.asyncio
async def test_chat_completion_tool_follow_up_retries_rate_limit(monkeypatch: Any) -> None:
    """Provider 429s that include a wait hint are retried before surfacing an error."""
    _set_local_stt_test_config(monkeypatch)
    monkeypatch.setattr(stream_mod.config, "CHAT_MODEL_NAME", "azure/anthropic/claude-opus-4-8")
    monkeypatch.setattr(
        chat_mod,
        "get_tool_specs_for_dependencies",
        lambda _deps: [
            {
                "type": "function",
                "name": "sweep_look",
                "description": "Look around.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    sleep_delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fake_sleep)

    async def fake_dispatch_tool_call_with_manager(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "queued"}

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch_tool_call_with_manager)
    create_calls: list[dict[str, Any]] = []

    class FakeRateLimitError(Exception):
        status_code = 429

    class FakeFunction:
        name = "sweep_look"
        arguments = "{}"

    class FakeToolCall:
        id = "call_1"
        type = "function"
        function = FakeFunction()

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeToolMessage:
        content = ""
        tool_calls = [FakeToolCall()]

    class FakeFinalMessage:
        content = "I looked around."
        tool_calls: list[Any] = []

    class FakeChoice:
        def __init__(self, message: Any) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: Any) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(FakeToolMessage())
            if len(create_calls) == 2:
                raise FakeRateLimitError("Rate limit exceeded. Please wait 17 seconds before retrying.")
            return FakeCompletion(FakeFinalMessage())

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    messages = await handler.send_text_message("Look around")

    assert messages[-1] == {"role": "assistant", "content": "I looked around."}
    assert sleep_delays == [17.0]
    assert len(create_calls) == 3
    assert create_calls[1]["tools"] == create_calls[0]["tools"]
    assert create_calls[2]["tools"] == create_calls[0]["tools"]
    assert any(message["role"] == "tool" for message in create_calls[2]["messages"])


# ---- Cost calculation tests ----


def _make_usage(
    audio_in: int | None = 0,
    text_in: int | None = 0,
    image_in: int | None = 0,
    audio_out: int | None = 0,
    text_out: int | None = 0,
    has_input: bool = True,
    has_output: bool = True,
) -> MagicMock:
    """Build a fake usage object matching the OpenAI response.usage shape."""
    usage = MagicMock()
    if has_input:
        inp = MagicMock()
        inp.audio_tokens = audio_in
        inp.text_tokens = text_in
        inp.image_tokens = image_in
        usage.input_token_details = inp
    else:
        usage.input_token_details = None
    if has_output:
        out = MagicMock()
        out.audio_tokens = audio_out
        out.text_tokens = text_out
        usage.output_token_details = out
    else:
        usage.output_token_details = None
    return usage


@pytest.mark.parametrize(
    "usage_kwargs, expect_positive",
    [
        # All token types present → positive cost
        ({"audio_in": 1000, "text_in": 2000, "image_in": 500, "audio_out": 800, "text_out": 300}, True),
        # All None tokens → must not crash
        ({"audio_in": None, "text_in": None, "image_in": None, "audio_out": None, "text_out": None}, False),
        # Mix of None and valid ints
        ({"audio_in": None, "text_in": 500, "image_in": None, "audio_out": 1000, "text_out": None}, True),
        # Missing input/output details entirely
        ({"has_input": False, "has_output": False}, False),
    ],
    ids=["normal", "all_none", "mixed", "missing_details"],
)
def test_compute_response_cost(usage_kwargs: dict[str, Any], expect_positive: bool) -> None:
    """Verify _compute_response_cost handles various token combinations without crashing."""
    usage = _make_usage(**usage_kwargs)
    cost = _compute_response_cost(usage)
    if expect_positive:
        assert cost > 0
    else:
        assert cost == 0.0


# ---- Stress test: response.create rejection + retry ----


@pytest.mark.asyncio
async def test_response_sender_retries_on_active_response_rejection(monkeypatch: Any, caplog: Any) -> None:
    """Stress test: response.create rejection + retry via real event processing.

    Tool results (is_idle_tool_call=False) queue response.create calls via
    _safe_response_create.  When the server rejects some with
    ``conversation_already_has_active_response``, the error event flows through
    the event handler and _response_sender_loop retries the rejected request.

    The full _run_realtime_session event loop runs so that the error-handling
    code path (setting _last_response_rejected) is exercised by real event
    processing, not mocked out.
    """
    caplog.set_level(logging.DEBUG)
    _set_openai_test_config(monkeypatch)

    FakeCCE = type("FakeCCE", (Exception,), {})
    monkeypatch.setattr(stream_mod, "ConnectionClosedError", FakeCCE)
    monkeypatch.setattr(stream_mod, "get_session_instructions", lambda: "test")
    monkeypatch.setattr(stream_mod, "get_session_voice", lambda *_args: "alloy")
    monkeypatch.setattr(stream_mod, "get_tool_specs_for_dependencies", lambda _deps: [])

    N_TOOL_RESULTS = 400
    REJECT_CALL_NUMBERS = {1, 3, 5, 10, 25, 50, 75, 100, 150, 200, 300, 399}
    EXPECTED_TOTAL_CALLS = N_TOOL_RESULTS + len(REJECT_CALL_NUMBERS)

    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    response_create_log: list[tuple[int, dict[str, Any]]] = []
    handler_ref: list[Any] = []

    # ---- Fake event / error objects mirroring the OpenAI SDK shapes ----

    class FakeError:
        def __init__(self, message: str, code: str) -> None:
            self.message = message
            self.code = code
            self.type = "invalid_request_error"
            self.event_id = None
            self.param = None

        def __repr__(self) -> str:
            return (
                f"RealtimeError(message='{self.message}', type='{self.type}', "
                f"code='{self.code}', event_id=None, param=None)"
            )

    class FakeEvent:
        def __init__(self, etype: str, **kwargs: Any) -> None:
            self.type = etype
            for k, v in kwargs.items():
                setattr(self, k, v)

    # ---- Fake connection components ----

    class FakeResponseAPI:
        """Mimics connection.response.

        Pushes server events into the shared event_queue so they flow
        through the real event-handling code.  Also guards the serialization
        invariant: every create() must arrive when no response is active.
        """

        def __init__(self) -> None:
            self._call_count = 0
            self._serialization_violations: list[int] = []
            self._server_active_response = False

        async def create(self, **kwargs: Any) -> None:
            self._call_count += 1
            n = self._call_count
            response_create_log.append((n, kwargs))

            # Real backend rejects when a response is already active.
            if self._server_active_response:
                self._serialization_violations.append(n)
                await event_queue.put(
                    FakeEvent(
                        "error",
                        error=FakeError(
                            message=(
                                f"Conversation already has an active response in "
                                f"progress: resp_fake{n}. Wait until the response "
                                f"is finished before creating a new one."
                            ),
                            code="conversation_already_has_active_response",
                        ),
                    )
                )
                await asyncio.sleep(0)
                await event_queue.put(FakeEvent("response.done", response=MagicMock()))
                self._server_active_response = False
                return

            # Intentional rejections (simulating a race where another
            # response sneaks in right after our check).
            if n in REJECT_CALL_NUMBERS:
                await event_queue.put(
                    FakeEvent(
                        "error",
                        error=FakeError(
                            message=(
                                f"Conversation already has an active response in "
                                f"progress: resp_fake{n}. Wait until the response "
                                f"is finished before creating a new one."
                            ),
                            code="conversation_already_has_active_response",
                        ),
                    )
                )
                await asyncio.sleep(0)
            else:
                self._server_active_response = True
                await event_queue.put(FakeEvent("response.created"))

            await asyncio.sleep(0)
            await event_queue.put(FakeEvent("response.done", response=MagicMock()))
            await asyncio.sleep(0)
            self._server_active_response = False

        async def cancel(self, **_kw: Any) -> None:
            pass

    fake_response_api = FakeResponseAPI()

    class FakeSession:
        async def update(self, **_kw: Any) -> None:
            pass

    class FakeInputAudioBuffer:
        async def append(self, **_kw: Any) -> None:
            pass

    class FakeItem:
        async def create(self, **_kw: Any) -> None:
            pass

    class FakeConversation:
        item = FakeItem()

    class FakeConn:
        session = FakeSession()
        input_audio_buffer = FakeInputAudioBuffer()
        conversation = FakeConversation()
        response = fake_response_api

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def close(self) -> None:
            pass

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> FakeEvent:
            event: FakeEvent = await event_queue.get()
            if event is None:  # sentinel stops event iteration
                raise StopAsyncIteration
            return event

    class FakeRealtime:
        def connect(self, **_kw: Any) -> FakeConn:
            return FakeConn()

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            self.realtime = FakeRealtime()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    # Patch dispatch_tool_call so tools complete with a result.
    async def _fake_dispatch(tool_name: str, args_json: str, deps: Any, **_kw: Any) -> dict[str, Any]:
        await asyncio.sleep(random.uniform(0.3, 0.5))
        return {"ok": True, "tool": tool_name}

    monkeypatch.setattr(btm_mod, "dispatch_tool_call", _fake_dispatch)

    # ---- Build handler and start the full realtime session ----

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)
    handler_ref.append(handler)

    asyncio.create_task(handler.start_up())

    # ---- Start tools via the real BackgroundToolManager pipeline ----
    # start_tool -> _run_tool -> notification queue -> listener -> _handle_tool_result

    for i in range(N_TOOL_RESULTS):
        await handler.tool_manager.start_tool(
            call_id=f"call_{i}",
            tool_call_routine=ToolCallRoutine(
                tool_name="test_tool",
                args_json_str=f'{{"index": {i}}}',
                deps=deps,
            ),
            is_idle_tool_call=False,
        )

    def rejection_log_count() -> int:
        return len([r for r in caplog.records if "worker will retry" in getattr(r, "msg", "")])

    def retry_log_count() -> int:
        return len([r for r in caplog.records if "response.create was rejected; retrying" in getattr(r, "msg", "")])

    deadline = asyncio.get_running_loop().time() + 20
    while (
        fake_response_api._call_count < EXPECTED_TOTAL_CALLS
        or rejection_log_count() < len(REJECT_CALL_NUMBERS)
        or retry_log_count() < len(REJECT_CALL_NUMBERS)
    ):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.05)

    # ---- Tear down ----

    await event_queue.put(None)  # sentinel stops event iteration

    await handler.shutdown()

    # ---- Assertions ----

    # Serialization: every response.create() must have been called only when
    # no response was in-flight (_response_done_event was set).  Any violation
    # means the sender fired a new request before the previous one finished.
    assert fake_response_api._serialization_violations == [], (
        f"response.create() was called while a response was still active on "
        f"call(s) {fake_response_api._serialization_violations}"
    )

    # Total response.create() calls = tool results + retries for rejected ones
    assert fake_response_api._call_count == EXPECTED_TOTAL_CALLS, (
        f"Expected {EXPECTED_TOTAL_CALLS} response.create calls "
        f"({N_TOOL_RESULTS} results + {len(REJECT_CALL_NUMBERS)} retries), "
        f"got {fake_response_api._call_count}"
    )

    # The error event handler must have set _last_response_rejected for each
    # rejection (the log message comes from the event handler code path).
    rejection_logs = [r for r in caplog.records if "worker will retry" in getattr(r, "msg", "")]
    assert len(rejection_logs) == len(REJECT_CALL_NUMBERS), (
        f"Expected {len(REJECT_CALL_NUMBERS)} rejection entries from error handler, got {len(rejection_logs)}"
    )

    # The sender loop must have retried after each rejection.
    retry_logs = [r for r in caplog.records if "response.create was rejected; retrying" in getattr(r, "msg", "")]
    assert len(retry_logs) == len(REJECT_CALL_NUMBERS), (
        f"Expected {len(REJECT_CALL_NUMBERS)} retry entries from sender loop, got {len(retry_logs)}"
    )


@pytest.mark.asyncio
async def test_response_sender_marks_response_active_before_create_returns() -> None:
    """The local response guard should be cleared before response.create returns."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    create_started = asyncio.Event()
    create_returned = asyncio.Event()
    allow_done = asyncio.Event()
    event_states_at_create: list[bool] = []

    class FakeResponse:
        async def create(self, **_kw: Any) -> None:
            event_states_at_create.append(handler._response_done_event.is_set())
            create_started.set()
            await allow_done.wait()
            handler._response_done_event.set()
            create_returned.set()

        async def cancel(self, **_kw: Any) -> None:
            pass

    fake_conn = MagicMock()
    fake_conn.response = FakeResponse()
    handler.connection = fake_conn

    await handler._safe_response_create(instructions="req1")
    sender_task = asyncio.create_task(handler._response_sender_loop())

    await asyncio.wait_for(create_started.wait(), timeout=1)
    assert event_states_at_create == [False]

    allow_done.set()
    await asyncio.wait_for(create_returned.wait(), timeout=1)

    handler.connection = None
    await handler._safe_response_create(instructions="stop")
    await asyncio.wait_for(sender_task, timeout=1)


# ---- Response creation timeout guard tests ----


@pytest.mark.asyncio
async def test_response_sender_loop_times_out_waiting_for_response_done(
    monkeypatch: Any,
    caplog: Any,
) -> None:
    """If response.done is never received the sender loop should time out.

    Rather than hang forever, it force-sets the event and moves on.
    """
    caplog.set_level(logging.DEBUG)

    monkeypatch.setattr(stream_mod, "_RESPONSE_DONE_TIMEOUT", 0.3)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    create_count = 0

    class FakeResponse:
        async def create(self, **_kw: Any) -> None:
            nonlocal create_count
            create_count += 1
            # Simulate response.created clearing the event, but never
            # send response.done (so the event stays cleared forever).
            handler._response_done_event.clear()

        async def cancel(self, **_kw: Any) -> None:
            pass

    fake_conn = MagicMock()
    fake_conn.response = FakeResponse()
    handler.connection = fake_conn

    # Queue two requests
    await handler._safe_response_create(instructions="req1")
    await handler._safe_response_create(instructions="req2")

    sender_task = asyncio.create_task(handler._response_sender_loop())

    # Give enough time for both requests to time out (0.3s each + margin)
    await asyncio.sleep(1.5)

    handler.connection = None  # signal the loop to exit
    handler._response_done_event.set()
    await asyncio.wait_for(sender_task, timeout=2.0)

    assert create_count == 2, f"Expected 2 response.create calls, got {create_count}"

    timeout_logs = [r for r in caplog.records if "Timed out waiting for response.done" in r.getMessage()]
    assert len(timeout_logs) == 2, f"Expected 2 timeout warnings, got {len(timeout_logs)}"


@pytest.mark.asyncio
async def test_response_sender_loop_times_out_waiting_for_previous_response(
    monkeypatch: Any,
    caplog: Any,
) -> None:
    """If a previous response never completes, the pre-condition wait times out.

    It should force-set the event and proceed to send.
    """
    caplog.set_level(logging.DEBUG)

    monkeypatch.setattr(stream_mod, "_RESPONSE_DONE_TIMEOUT", 0.3)

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = stream_mod.ConversationStreamHandler(deps)

    # Pretend a response is already in-flight (event cleared)
    handler._response_done_event.clear()

    created = asyncio.Event()

    class FakeResponse:
        async def create(self, **_kw: Any) -> None:
            # Immediately complete the response cycle so the loop can finish
            handler._response_done_event.set()
            created.set()

        async def cancel(self, **_kw: Any) -> None:
            pass

    fake_conn = MagicMock()
    fake_conn.response = FakeResponse()
    handler.connection = fake_conn

    await handler._safe_response_create(instructions="waiting_req")

    sender_task = asyncio.create_task(handler._response_sender_loop())

    # Wait for the request to be sent (after timing out on the pre-condition)
    await asyncio.wait_for(created.wait(), timeout=2.0)

    handler.connection = None
    handler._response_done_event.set()
    await asyncio.wait_for(sender_task, timeout=2.0)

    timeout_logs = [r for r in caplog.records if "Timed out waiting for previous response" in r.getMessage()]
    assert len(timeout_logs) == 1, f"Expected 1 pre-condition timeout warning, got {len(timeout_logs)}"
