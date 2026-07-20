"""Tests for the sandbox audio WebSocket service."""

# ruff: noqa: D101, D102, D103, D107

import time
import asyncio
from typing import Any

import numpy as np
from fastrtc import AdditionalOutputs
from fastapi.testclient import TestClient

from reachy_mini_conversation_app.sandbox_audio import WIRE_SAMPLE_RATE, create_audio_app


class FakeHandler:
    def __init__(self, outputs: list[Any] | None = None) -> None:
        self.outputs: asyncio.Queue[Any] = asyncio.Queue()
        for output in outputs or []:
            self.outputs.put_nowait(output)
        self.received: list[tuple[int, np.ndarray[Any, Any]]] = []
        self.stopped = asyncio.Event()
        self.shutdown_called = False

    async def start_up(self) -> None:
        await self.stopped.wait()

    async def receive(self, frame: tuple[int, np.ndarray[Any, Any]]) -> None:
        self.received.append(frame)

    async def emit(self) -> Any:
        return await self.outputs.get()

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.stopped.set()


def hello() -> dict[str, Any]:
    return {
        "type": "hello",
        "format": "pcm_s16le",
        "sample_rate": WIRE_SAMPLE_RATE,
        "channels": 1,
    }


def test_health_describes_audio_contract() -> None:
    with TestClient(create_audio_app(lambda: FakeHandler())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "active_audio_client": False,
        **hello(),
    }


def test_audio_websocket_bridges_pcm_and_text_outputs() -> None:
    expected_audio = np.array([100, -100, 200, -200], dtype=np.int16)
    handler = FakeHandler(
        [
            (WIRE_SAMPLE_RATE, expected_audio.reshape(1, -1)),
            AdditionalOutputs({"role": "assistant", "content": "OpenShell blocked that movement."}),
        ]
    )
    with TestClient(create_audio_app(lambda: handler)) as client:
        with client.websocket_connect("/audio") as websocket:
            websocket.send_json(hello())
            assert websocket.receive_json()["type"] == "ready"

            microphone_audio = np.array([1, -2, 3], dtype="<i2")
            websocket.send_bytes(microphone_audio.tobytes())
            assert np.array_equal(np.frombuffer(websocket.receive_bytes(), dtype="<i2"), expected_audio)
            message = websocket.receive_json()
            assert message == {
                "type": "messages",
                "messages": [{"role": "assistant", "content": "OpenShell blocked that movement."}],
            }
            websocket.send_json({"type": "close"})
            deadline = time.monotonic() + 1.0
            while not handler.received and time.monotonic() < deadline:
                time.sleep(0.01)

    assert len(handler.received) == 1
    assert handler.received[0][0] == WIRE_SAMPLE_RATE
    assert np.array_equal(handler.received[0][1], microphone_audio)
    assert handler.shutdown_called is True


def test_audio_websocket_rejects_wrong_format() -> None:
    with TestClient(create_audio_app(lambda: FakeHandler())) as client:
        with client.websocket_connect("/audio") as websocket:
            websocket.send_json({**hello(), "format": "float32"})
            message = websocket.receive()

    assert message["type"] == "websocket.close"
    assert message["code"] == 1002
