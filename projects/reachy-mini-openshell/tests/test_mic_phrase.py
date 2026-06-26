import numpy as np

from reachy_mini_conversation_app.audio.mic_phrase import MicPhraseBuffer, MicPhraseConfig


def test_mic_phrase_buffer_ignores_leading_silence() -> None:
    """Leading silence should not create a phrase or mark speech activity."""
    buffer = MicPhraseBuffer(MicPhraseConfig(rms_threshold=10.0, min_audio_ms=1.0, silence_ms=1.0))

    result = buffer.push_frame((16000, np.zeros(160, dtype=np.int16)))

    assert result.phrase_audio is None
    assert result.saw_speech is False
    assert buffer.flush() is None


def test_mic_phrase_buffer_flushes_after_trailing_silence() -> None:
    """Speech followed by enough silence should produce one complete phrase."""
    buffer = MicPhraseBuffer(MicPhraseConfig(rms_threshold=10.0, min_audio_ms=1.0, silence_ms=5.0))

    speech = buffer.push_frame((16000, np.full(160, 2000, dtype=np.int16)))
    silence = buffer.push_frame((16000, np.zeros(160, dtype=np.int16)))

    assert speech.phrase_audio is None
    assert speech.saw_speech is True
    assert silence.saw_speech is False
    assert silence.phrase_audio is not None
    assert silence.phrase_audio.dtype == np.int16
    assert silence.phrase_audio.shape == (320,)
    assert buffer.flush() is None


def test_mic_phrase_buffer_flushes_at_max_duration() -> None:
    """Long phrases should be forced out even before trailing silence arrives."""
    buffer = MicPhraseBuffer(
        MicPhraseConfig(
            rms_threshold=10.0,
            min_audio_ms=1_000.0,
            silence_ms=1_000.0,
            max_audio_ms=10.0,
        )
    )

    result = buffer.push_frame((16000, np.full(160, 2000, dtype=np.int16)))

    assert result.phrase_audio is not None
    assert result.phrase_audio.shape == (160,)
    assert result.saw_speech is True


def test_mic_phrase_buffer_accepts_browser_float64_frames() -> None:
    """Browser frames can arrive as normalized float64 channel-first audio."""
    buffer = MicPhraseBuffer(MicPhraseConfig(rms_threshold=10.0, min_audio_ms=1.0, silence_ms=1.0))

    speech = buffer.push_frame((16000, np.full((1, 160), 0.25, dtype=np.float64)))
    silence = buffer.push_frame((16000, np.zeros((1, 160), dtype=np.float64)))

    assert speech.saw_speech is True
    assert speech.phrase_audio is None
    assert silence.phrase_audio is not None
    assert int(np.max(silence.phrase_audio)) > 8_000
