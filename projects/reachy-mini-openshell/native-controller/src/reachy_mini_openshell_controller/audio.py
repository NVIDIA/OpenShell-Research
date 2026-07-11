"""Small NumPy-only codec helpers for robot PCM audio."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

WIRE_SAMPLE_RATE = 16_000


def first_mono_channel(audio: NDArray[np.generic]) -> NDArray[np.generic]:
    """Select one channel from common frame-first and channel-first layouts."""
    if audio.ndim == 1:
        return audio
    if audio.ndim != 2:
        raise ValueError(f"Expected 1D or 2D audio, got {audio.shape}")
    rows, columns = audio.shape
    if rows == 1:
        return audio[0]
    if columns == 1:
        return audio[:, 0]
    if rows in {2, 6, 8} and columns > rows:
        return audio[0]
    return audio[:, 0]


def resample_linear(audio: NDArray[np.float32], source_rate: int, target_rate: int) -> NDArray[np.float32]:
    """Resample a mono float32 frame without pulling SciPy into the native app."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    target_length = max(1, round(audio.size * target_rate / source_rate))
    source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def encode_robot_audio(audio: NDArray[np.generic], source_rate: int) -> bytes:
    """Convert Reachy float/int audio to mono 16 kHz signed PCM."""
    mono = first_mono_channel(np.asarray(audio))
    if np.issubdtype(mono.dtype, np.floating):
        normalized = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0).astype(np.float32)
        max_abs = float(np.max(np.abs(normalized))) if normalized.size else 0.0
        if max_abs > 1.0:
            normalized = normalized / max_abs
    elif np.issubdtype(mono.dtype, np.integer):
        normalized = mono.astype(np.float32) / 32768.0
    else:
        raise TypeError(f"Unsupported robot audio dtype {mono.dtype}")
    resampled = resample_linear(normalized, source_rate, WIRE_SAMPLE_RATE)
    pcm = np.clip(resampled, -1.0, 1.0) * 32767.0
    return pcm.astype("<i2").tobytes()


def decode_agent_audio(payload: bytes, output_rate: int) -> NDArray[np.float32]:
    """Convert mono 16 kHz signed PCM into Reachy float32 playback audio."""
    if len(payload) % 2:
        raise ValueError("PCM payload must contain complete int16 samples")
    pcm = np.frombuffer(payload, dtype="<i2").astype(np.float32)
    normalized = pcm / 32768.0
    return resample_linear(normalized, WIRE_SAMPLE_RATE, output_rate)
