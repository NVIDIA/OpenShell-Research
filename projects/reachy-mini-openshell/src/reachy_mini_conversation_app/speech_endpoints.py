"""OpenAI-compatible STT and TTS endpoint helpers for local-STT mode."""

from typing import Any, Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.audio.pcm import (
    wav_bytes,
    read_wav_audio,
    normalize_wav_bytes,
    binary_response_bytes,
)


def transcription_text(transcription: Any) -> str:
    """Extract text from an OpenAI-compatible transcription response."""
    error = transcription.get("error") if isinstance(transcription, dict) else getattr(transcription, "error", None)
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or error
        raise RuntimeError(f"STT endpoint returned an error: {message}")
    if error:
        raise RuntimeError(f"STT endpoint returned an error: {error}")

    if isinstance(transcription, str):
        return transcription.strip()
    if isinstance(transcription, dict):
        text = transcription.get("text")
        return text.strip() if isinstance(text, str) else ""
    text = getattr(transcription, "text", None)
    return text.strip() if isinstance(text, str) else ""


class SpeechEndpointClient:
    """Client wrapper for OpenAI-compatible transcription and speech endpoints."""

    def __init__(
        self,
        *,
        stt_api_key: str | None,
        stt_base_url: str | None,
        stt_model: str | None,
        tts_api_key: str | None,
        tts_base_url: str | None,
        tts_model: str | None,
        tts_voice: str | None,
        client_factory: Callable[..., Any],
    ) -> None:
        """Initialize endpoint configuration."""
        self.stt_api_key = (stt_api_key or "not-needed").strip() or "not-needed"
        self.stt_base_url = stt_base_url
        self.stt_model = stt_model
        self.tts_api_key = (tts_api_key or "not-needed").strip() or "not-needed"
        self.tts_base_url = tts_base_url
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.client_factory = client_factory
        self._stt_client: Any = None
        self._tts_client: Any = None

    def _get_stt_client(self) -> Any:
        """Return the OpenAI-compatible client used for speech-to-text."""
        if self._stt_client is None:
            self._stt_client = self.client_factory(
                api_key=self.stt_api_key,
                base_url=self.stt_base_url,
            )
        return self._stt_client

    def _get_tts_client(self) -> Any:
        """Return the OpenAI-compatible client used for text-to-speech."""
        if self._tts_client is None:
            self._tts_client = self.client_factory(
                api_key=self.tts_api_key,
                base_url=self.tts_base_url,
            )
        return self._tts_client

    async def transcribe_audio(
        self,
        audio_frame: NDArray[np.int16],
        sample_rate: int,
        *,
        filename: str = "microphone.wav",
    ) -> str:
        """Transcribe mono int16 audio through the configured STT endpoint."""
        return await self.transcribe_wav_bytes(wav_bytes(audio_frame, sample_rate), filename=filename)

    async def transcribe_wav_bytes(self, wav_payload: bytes, *, filename: str = "microphone.wav") -> str:
        """Transcribe a WAV payload through the configured STT endpoint."""
        transcription = await self._get_stt_client().audio.transcriptions.create(
            model=self.stt_model,
            file=(filename, wav_payload, "audio/wav"),
        )
        return transcription_text(transcription)

    async def synthesize_speech(self, text: str) -> tuple[int, NDArray[np.int16]]:
        """Synthesize assistant text through the configured TTS endpoint."""
        wav_payload = await self.synthesize_speech_wav_bytes(text)
        return read_wav_audio(wav_payload)

    async def synthesize_speech_wav_bytes(self, text: str) -> bytes:
        """Synthesize assistant text through the configured TTS endpoint as WAV bytes."""
        response = await self._get_tts_client().audio.speech.create(
            model=self.tts_model,
            voice=self.tts_voice,
            input=text,
            response_format="wav",
        )
        return normalize_wav_bytes(await binary_response_bytes(response))
