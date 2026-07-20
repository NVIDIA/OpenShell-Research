"""Microphone phrase buffering for local-STT transcription."""

from typing import Any
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.audio.pcm import audio_rms, prepare_mono_int16_audio


MIC_TRANSCRIPTION_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class MicPhraseConfig:
    """Voice activity settings for microphone-to-text phrase detection."""

    sample_rate: int = MIC_TRANSCRIPTION_SAMPLE_RATE
    rms_threshold: float = 500.0
    min_audio_ms: float = 250.0
    silence_ms: float = 800.0
    max_audio_ms: float = 12_000.0


@dataclass(frozen=True)
class MicPhraseResult:
    """Result from adding one microphone frame to a phrase buffer."""

    phrase_audio: NDArray[np.int16] | None
    saw_speech: bool


class MicPhraseBuffer:
    """Accumulate microphone frames into speech phrases."""

    def __init__(self, config: MicPhraseConfig):
        """Initialize an empty phrase buffer."""
        self.config = config
        self._buffer: list[NDArray[np.int16]] = []
        self._buffer_ms: float = 0.0
        self._silence_ms: float = 0.0
        self._has_speech: bool = False

    def push_frame(self, frame: tuple[int, NDArray[Any]]) -> MicPhraseResult:
        """Add one audio frame and return a completed phrase when ready."""
        audio_frame = prepare_mono_int16_audio(frame, self.config.sample_rate)
        if audio_frame.size == 0:
            return MicPhraseResult(phrase_audio=None, saw_speech=False)

        frame_ms = len(audio_frame) * 1000.0 / self.config.sample_rate
        is_speech = audio_rms(audio_frame) >= self.config.rms_threshold

        if is_speech:
            self._has_speech = True
            self._silence_ms = 0.0
        elif self._has_speech:
            self._silence_ms += frame_ms
        else:
            return MicPhraseResult(phrase_audio=None, saw_speech=False)

        self._buffer.append(audio_frame)
        self._buffer_ms += frame_ms

        should_flush = self._buffer_ms >= self.config.max_audio_ms or (
            self._buffer_ms >= self.config.min_audio_ms and self._silence_ms >= self.config.silence_ms
        )
        if not should_flush:
            return MicPhraseResult(phrase_audio=None, saw_speech=is_speech)

        return MicPhraseResult(phrase_audio=self.flush(), saw_speech=is_speech)

    def flush(self) -> NDArray[np.int16] | None:
        """Return the buffered phrase and reset phrase-detection state."""
        if not self._buffer:
            return None

        audio_frame = np.concatenate(self._buffer).astype(np.int16, copy=False)
        self.reset()
        return audio_frame

    def reset(self) -> None:
        """Clear buffered audio and voice activity state."""
        self._buffer = []
        self._buffer_ms = 0.0
        self._silence_ms = 0.0
        self._has_speech = False
