from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.config as config_mod
from reachy_mini_conversation_app.audio.pcm import wav_bytes
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.local_stt_backend import LocalSTTBackend
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


def _set_local_stt_test_config(monkeypatch: Any) -> None:
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)
    monkeypatch.setattr(config_mod.config, "CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(config_mod.config, "CHAT_MODEL_NAME", "test-chat-model")
    monkeypatch.setattr(config_mod.config, "STT_API_KEY", "stt-key")
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "https://stt.test/v1")
    monkeypatch.setattr(config_mod.config, "STT_MODEL_NAME", "test-stt-model")
    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "tts-key")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "https://tts.test/v1")
    monkeypatch.setattr(config_mod.config, "TTS_MODEL_NAME", "test-tts-model")
    monkeypatch.setattr(config_mod.config, "TTS_VOICE", "cedar")


@pytest.mark.asyncio
async def test_local_stt_backend_runs_stt_chat_and_tts(monkeypatch: Any) -> None:
    """The local-STT adapter owns endpoint/client construction for the cascade."""
    _set_local_stt_test_config(monkeypatch)
    client_kwargs: list[dict[str, Any]] = []
    transcription_calls: list[dict[str, Any]] = []
    chat_calls: list[dict[str, Any]] = []
    speech_calls: list[dict[str, Any]] = []

    class FakeTranscription:
        text = "Hi Reachy"

    class FakeTranscriptions:
        async def create(self, **kwargs: Any) -> FakeTranscription:
            transcription_calls.append(kwargs)
            return FakeTranscription()

    class FakeSpeech:
        async def create(self, **kwargs: Any) -> Any:
            speech_calls.append(kwargs)

            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1200, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeAudio:
        transcriptions = FakeTranscriptions()
        speech = FakeSpeech()

    class FakeMessage:
        content = "Hello from chat."
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
            self.audio = FakeAudio()
            self.chat = FakeChat()

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    backend = LocalSTTBackend(
        deps=deps,
        tool_manager=BackgroundToolManager(),
        client_factory=FakeClient,
    )

    transcript = await backend.transcribe_audio(np.array([0, 1000], dtype=np.int16), 16000)
    messages = await backend.send_text_message("Hi")
    sample_rate, audio_frame = await backend.synthesize_speech("Hello from chat.")

    assert transcript == "Hi Reachy"
    assert messages[-1] == {"role": "assistant", "content": "Hello from chat."}
    assert sample_rate == 24000
    assert audio_frame.dtype == np.int16
    assert client_kwargs == [
        {"api_key": "stt-key", "base_url": "https://stt.test/v1"},
        {"api_key": "chat-key", "base_url": "https://chat.test/v1"},
        {"api_key": "tts-key", "base_url": "https://tts.test/v1"},
    ]
    assert transcription_calls[0]["model"] == "test-stt-model"
    assert chat_calls[0]["model"] == "test-chat-model"
    assert speech_calls[0]["model"] == "test-tts-model"
    assert speech_calls[0]["voice"] == "cedar"
