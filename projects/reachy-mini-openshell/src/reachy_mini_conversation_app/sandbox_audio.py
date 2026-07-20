"""Headless audio service for the OpenShell-hosted conversation agent."""

from __future__ import annotations
import os
import json
import asyncio
import logging
from typing import Any, Protocol
from pathlib import Path
from collections.abc import Callable

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastrtc import AdditionalOutputs
from numpy.typing import NDArray

from reachy_mini_conversation_app.audio.pcm import prepare_mono_int16_audio


logger = logging.getLogger(__name__)

WIRE_FORMAT = "pcm_s16le"
WIRE_SAMPLE_RATE = 16_000
WIRE_CHANNELS = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_AUDIO_FRAME_BYTES = WIRE_SAMPLE_RATE * 2 * 2


class ConversationHandler(Protocol):
    """Subset of ConversationStreamHandler used by the audio service."""

    async def start_up(self) -> None:
        """Run the model session until shutdown."""
        ...

    async def receive(self, frame: tuple[int, NDArray[Any]]) -> None:
        """Accept one microphone frame."""
        ...

    async def emit(self) -> Any:
        """Return the next audio or message output."""
        ...

    async def shutdown(self) -> None:
        """Close model and tool resources."""
        ...


HandlerFactory = Callable[[], ConversationHandler]


def _build_handler() -> ConversationHandler:
    """Build the normal conversation handler with policy-routed REST tools."""
    from reachy_mini_conversation_app.main import _build_tool_transport_factory
    from reachy_mini_conversation_app.config import TOOL_TRANSPORT_REST, config
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.conversation_stream import ConversationStreamHandler

    tool_transport_mode = config.REACHY_TOOL_TRANSPORT
    if tool_transport_mode != TOOL_TRANSPORT_REST:
        raise RuntimeError(
            "The sandbox audio service requires REACHY_TOOL_TRANSPORT=rest so all robot actions cross OpenShell policy"
        )

    dependencies = ToolDependencies(
        capture_directory=Path(os.getenv("REACHY_CAPTURE_DIR", "/sandbox/captures")).expanduser(),
    )
    tool_transport_factory = _build_tool_transport_factory(
        tool_transport_mode,
        dependencies,
        rest_base_url=config.REACHY_REST_BASE_URL,
        camera_base_url=config.REACHY_CAMERA_BASE_URL,
        rest_timeout_seconds=config.REACHY_REST_TIMEOUT_SECONDS,
        motion_duration_seconds=config.REACHY_MOTION_DURATION_SECONDS,
        motion_poll_interval_seconds=config.REACHY_MOTION_POLL_INTERVAL_SECONDS,
        motion_completion_timeout_seconds=config.REACHY_MOTION_COMPLETION_TIMEOUT_SECONDS,
    )
    return ConversationStreamHandler(
        dependencies,
        gradio_mode=False,
        model_logs=os.getenv("REACHY_MODEL_LOGS", "1").strip().lower() not in {"0", "false", "no", "off"},
        tool_transport_factory=tool_transport_factory,
    )


def _hello_payload() -> dict[str, Any]:
    return {
        "type": "hello",
        "format": WIRE_FORMAT,
        "sample_rate": WIRE_SAMPLE_RATE,
        "channels": WIRE_CHANNELS,
    }


def _validate_hello(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("type") != "hello":
        return "first message must be a hello object"
    if payload.get("format") != WIRE_FORMAT:
        return f"format must be {WIRE_FORMAT}"
    if payload.get("sample_rate") != WIRE_SAMPLE_RATE:
        return f"sample_rate must be {WIRE_SAMPLE_RATE}"
    if payload.get("channels") != WIRE_CHANNELS:
        return f"channels must be {WIRE_CHANNELS}"
    return None


def _safe_additional_outputs(output: AdditionalOutputs) -> list[dict[str, str]]:
    """Return only text metadata needed by the trusted native bridge."""
    messages: list[dict[str, str]] = []
    for item in output.args:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return messages


async def _receive_audio(websocket: WebSocket, handler: ConversationHandler) -> None:
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            return

        payload = message.get("bytes")
        if payload is not None:
            if len(payload) == 0:
                continue
            if len(payload) > MAX_AUDIO_FRAME_BYTES:
                await websocket.close(code=1009, reason="audio frame too large")
                return
            if len(payload) % 2:
                await websocket.close(code=1003, reason="PCM frame must contain complete int16 samples")
                return
            audio = np.frombuffer(payload, dtype="<i2").astype(np.int16, copy=True)
            await handler.receive((WIRE_SAMPLE_RATE, audio))
            continue

        text_payload = message.get("text")
        if text_payload is None:
            continue
        try:
            control = json.loads(text_payload)
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "message": "invalid JSON control message"})
            continue
        if control == {"type": "ping"}:
            await websocket.send_json({"type": "pong"})
        elif control == {"type": "close"}:
            return
        else:
            await websocket.send_json({"type": "error", "message": "unsupported control message"})


async def _send_outputs(websocket: WebSocket, handler: ConversationHandler) -> None:
    while True:
        output = await handler.emit()
        if output is None:
            continue
        if isinstance(output, AdditionalOutputs):
            messages = _safe_additional_outputs(output)
            if messages:
                await websocket.send_json({"type": "messages", "messages": messages})
            continue

        if not isinstance(output, tuple) or len(output) != 2:
            logger.warning("Ignoring unsupported handler output type %s", type(output).__name__)
            continue

        sample_rate, audio = output
        if not isinstance(sample_rate, int) or not isinstance(audio, np.ndarray):
            logger.warning("Ignoring malformed audio output")
            continue
        wire_audio = prepare_mono_int16_audio((sample_rate, audio), WIRE_SAMPLE_RATE)
        if wire_audio.size:
            await websocket.send_bytes(wire_audio.astype("<i2", copy=False).tobytes())


async def _close_handler(handler: ConversationHandler, startup_task: asyncio.Task[None]) -> None:
    try:
        await handler.shutdown()
    finally:
        if not startup_task.done():
            try:
                await asyncio.wait_for(startup_task, timeout=3.0)
            except TimeoutError:
                startup_task.cancel()
        if startup_task.cancelled():
            return
        if startup_task.done():
            try:
                startup_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Conversation startup task failed during shutdown")


def create_audio_app(handler_factory: HandlerFactory = _build_handler) -> FastAPI:
    """Create the single-client, loopback-only audio service."""
    application = FastAPI(title="Reachy OpenShell Audio", docs_url=None, redoc_url=None)
    client_lock = asyncio.Lock()

    @application.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "active_audio_client": client_lock.locked(),
            **_hello_payload(),
        }

    @application.websocket("/audio")
    async def audio(websocket: WebSocket) -> None:
        if client_lock.locked():
            await websocket.close(code=1013, reason="another Reachy audio client is already connected")
            return

        async with client_lock:
            await websocket.accept()
            try:
                hello = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            except (TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
                await websocket.close(code=1002, reason="valid hello message required")
                return

            hello_error = _validate_hello(hello)
            if hello_error is not None:
                await websocket.close(code=1002, reason=hello_error)
                return
            await websocket.send_json({**_hello_payload(), "type": "ready"})

            try:
                handler = handler_factory()
            except Exception as exc:
                logger.exception("Unable to build conversation handler")
                await websocket.send_json({"type": "error", "message": f"agent startup failed: {type(exc).__name__}"})
                await websocket.close(code=1011)
                return

            startup_task = asyncio.create_task(handler.start_up(), name="conversation-handler")
            receive_task = asyncio.create_task(_receive_audio(websocket, handler), name="robot-audio-input")
            send_task = asyncio.create_task(_send_outputs(websocket, handler), name="robot-audio-output")
            try:
                done, pending = await asyncio.wait(
                    {startup_task, receive_task, send_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is startup_task and task.exception() is not None:
                        raise task.exception()  # type: ignore[misc]
                for task in pending:
                    if task is not startup_task:
                        task.cancel()
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("Audio session failed")
            finally:
                receive_task.cancel()
                send_task.cancel()
                await asyncio.gather(receive_task, send_task, return_exceptions=True)
                await _close_handler(handler, startup_task)
                try:
                    await websocket.close(code=1000)
                except RuntimeError:
                    # The peer may already have sent a disconnect frame.
                    pass

    return application


app = create_audio_app()


def main() -> None:
    """Run the loopback audio service inside the OpenShell sandbox."""
    host = os.getenv("REACHY_AUDIO_HOST", DEFAULT_HOST)
    port = int(os.getenv("REACHY_AUDIO_PORT", str(DEFAULT_PORT)))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("REACHY_AUDIO_HOST must remain loopback-only")
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("REACHY_AUDIO_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
