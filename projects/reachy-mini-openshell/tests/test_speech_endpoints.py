from typing import Any

import numpy as np
import pytest

from reachy_mini_conversation_app.audio.pcm import wav_bytes
from reachy_mini_conversation_app.speech_endpoints import SpeechEndpointClient, transcription_text


def test_transcription_text_accepts_common_response_shapes() -> None:
    """Transcription helpers should accept SDK objects, dicts, and strings."""

    class FakeTranscription:
        text = "  hello object  "

    assert transcription_text("  hello string  ") == "hello string"
    assert transcription_text({"text": "  hello dict  "}) == "hello dict"
    assert transcription_text(FakeTranscription()) == "hello object"
    assert transcription_text({"text": None}) == ""


def test_transcription_text_raises_for_provider_error_payload() -> None:
    """OpenAI-compatible STT servers may return errors inside a 200-shaped object."""
    with pytest.raises(RuntimeError, match="Please install vllm\\[audio\\]"):
        transcription_text(
            {
                "text": None,
                "error": {
                    "message": "Please install vllm[audio] for audio support",
                    "code": 500,
                },
            }
        )


@pytest.mark.asyncio
async def test_speech_endpoint_client_transcribes_and_synthesizes() -> None:
    """The local-STT speech client should isolate OpenAI-compatible STT/TTS plumbing."""
    client_kwargs: list[dict[str, Any]] = []
    transcription_calls: list[dict[str, Any]] = []
    speech_calls: list[dict[str, Any]] = []

    class FakeTranscriptions:
        async def create(self, **kwargs: Any) -> dict[str, str]:
            transcription_calls.append(kwargs)
            return {"text": "Hi Reachy"}

    class FakeSttAudio:
        transcriptions = FakeTranscriptions()

    class FakeSpeech:
        async def create(self, **kwargs: Any) -> Any:
            speech_calls.append(kwargs)

            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1000, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeTtsAudio:
        speech = FakeSpeech()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            client_kwargs.append(kwargs)
            if kwargs["base_url"] == "https://stt.test/v1":
                self.audio = FakeSttAudio()
            else:
                self.audio = FakeTtsAudio()

    speech_client = SpeechEndpointClient(
        stt_api_key="stt-key",
        stt_base_url="https://stt.test/v1",
        stt_model="whisper-large-v3",
        tts_api_key="tts-key",
        tts_base_url="https://tts.test/v1",
        tts_model="gpt-4o-mini-tts",
        tts_voice="cedar",
        client_factory=FakeClient,
    )

    transcript = await speech_client.transcribe_audio(np.array([0, 2000, 0], dtype=np.int16), 16000)
    sample_rate, audio_frame = await speech_client.synthesize_speech("Hello back")

    assert transcript == "Hi Reachy"
    assert client_kwargs == [
        {"api_key": "stt-key", "base_url": "https://stt.test/v1"},
        {"api_key": "tts-key", "base_url": "https://tts.test/v1"},
    ]
    assert transcription_calls[0]["model"] == "whisper-large-v3"
    assert transcription_calls[0]["file"][0] == "microphone.wav"
    assert transcription_calls[0]["file"][1].startswith(b"RIFF")
    assert transcription_calls[0]["file"][2] == "audio/wav"
    assert speech_calls == [
        {
            "model": "gpt-4o-mini-tts",
            "voice": "cedar",
            "input": "Hello back",
            "response_format": "wav",
        }
    ]
    assert sample_rate == 24000
    assert audio_frame.dtype == np.int16
