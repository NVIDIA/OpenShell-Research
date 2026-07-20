"""Tests for the trusted native audio bridge."""

# ruff: noqa: D103

from threading import Event
from typing import Any

import numpy as np
import pytest

from reachy_mini_openshell_controller.audio import decode_agent_audio, encode_robot_audio
from reachy_mini_openshell_controller.bridge import NativeAudioBridge
from reachy_mini_openshell_controller.settings import ControllerSettings


def test_audio_service_uses_gateway_hostname_with_loopback_tcp_destination() -> None:
    settings = ControllerSettings()

    assert settings.audio_websocket_url == "ws://reachy-agent--audio.openshell.localhost:17670/audio"
    assert settings.gateway_connect_host == "127.0.0.1"


def test_encode_robot_audio_selects_mono_and_resamples() -> None:
    stereo = np.column_stack(
        [
            np.linspace(-1.0, 1.0, 800, dtype=np.float32),
            np.ones(800, dtype=np.float32),
        ]
    )

    payload = encode_robot_audio(stereo, 8_000)

    decoded = np.frombuffer(payload, dtype="<i2")
    assert decoded.shape == (1_600,)
    assert decoded[0] < 0
    assert decoded[-1] > 0


def test_decode_agent_audio_rejects_incomplete_sample() -> None:
    with pytest.raises(ValueError, match="complete int16"):
        decode_agent_audio(b"\x01", 16_000)


@pytest.mark.asyncio
async def test_play_loop_pushes_audio_and_mutes_microphone() -> None:
    stop_event = Event()
    pushed: list[np.ndarray[Any, Any]] = []

    class Media:
        def push_audio_sample(self, audio: np.ndarray[Any, Any]) -> None:
            pushed.append(audio)

    class WebSocket:
        async def recv(self) -> bytes:
            stop_event.set()
            return np.array([0, 16_384, -16_384], dtype="<i2").tobytes()

    bridge = NativeAudioBridge(Media(), ControllerSettings())
    await bridge._play_loop(WebSocket(), stop_event, 16_000)

    assert len(pushed) == 1
    assert np.allclose(pushed[0], [0.0, 0.5, -0.5])
    assert bridge._mute_microphone_until > 0
