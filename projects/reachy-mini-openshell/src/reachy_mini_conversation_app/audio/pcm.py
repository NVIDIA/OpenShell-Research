"""PCM and WAV helpers shared by realtime and local-STT backends."""

import io
import wave
import asyncio
from typing import Any

import numpy as np
from fastrtc import audio_to_int16
from numpy.typing import NDArray
from scipy.signal import resample


def first_mono_channel(audio_frame: NDArray[Any]) -> NDArray[Any]:
    """Return one mono channel from common FastRTC and scipy audio layouts."""
    if audio_frame.ndim == 1:
        return audio_frame

    if audio_frame.ndim != 2:
        raise ValueError(f"Expected 1D or 2D audio, got shape {audio_frame.shape}")

    rows, columns = audio_frame.shape
    if rows == 1:
        return audio_frame[0, :]
    if columns == 1:
        return audio_frame[:, 0]
    if rows in {2, 6, 8} and columns > rows:
        return audio_frame[0, :]
    if columns in {2, 6, 8}:
        return audio_frame[:, 0]
    if rows < columns:
        return audio_frame[0, :]
    return audio_frame[:, 0]


def coerce_audio_to_int16(audio_frame: NDArray[Any]) -> NDArray[np.int16]:
    """Convert supported PCM audio arrays to int16.

    FastRTC accepts normalized float32 audio, while scipy resampling returns
    float64 values in the original PCM amplitude range. Preserve that scale
    instead of multiplying resampled PCM by 32767 a second time.
    """
    if audio_frame.dtype == np.int16:
        return audio_frame

    if np.issubdtype(audio_frame.dtype, np.floating):
        finite_audio = np.nan_to_num(audio_frame, nan=0.0, posinf=32767.0, neginf=-32768.0)
        max_abs = float(np.max(np.abs(finite_audio))) if finite_audio.size else 0.0
        if max_abs <= 1.0:
            return audio_to_int16(finite_audio.astype(np.float32, copy=False))
        return np.clip(finite_audio, -32768, 32767).astype(np.int16)

    if np.issubdtype(audio_frame.dtype, np.integer):
        return np.clip(audio_frame, -32768, 32767).astype(np.int16)

    raise TypeError(f"Unsupported audio data type: {audio_frame.dtype}")


def prepare_mono_int16_audio(
    frame: tuple[int, NDArray[Any]],
    target_sample_rate: int,
) -> NDArray[np.int16]:
    """Convert an incoming audio frame to mono int16 at the requested sample rate."""
    input_sample_rate, audio_frame = frame
    mono_audio = first_mono_channel(audio_frame)

    if target_sample_rate != input_sample_rate and len(mono_audio) > 0:
        mono_audio = resample(mono_audio, int(len(mono_audio) * target_sample_rate / input_sample_rate))

    return coerce_audio_to_int16(mono_audio)


def audio_rms(audio_frame: NDArray[np.int16]) -> float:
    """Return the RMS amplitude for a mono int16 audio frame."""
    if audio_frame.size == 0:
        return 0.0
    samples = audio_frame.astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


def wav_bytes(audio_frame: NDArray[np.int16], sample_rate: int) -> bytes:
    """Encode mono int16 PCM audio as a WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_frame.tobytes())
    return buffer.getvalue()


def read_wav_audio(wav_payload: bytes) -> tuple[int, NDArray[np.int16]]:
    """Decode a mono/stereo 16-bit WAV payload to mono int16 audio."""
    with wave.open(io.BytesIO(wav_payload), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit WAV audio, got sample width {sample_width}")

    audio_frame = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio_frame = audio_frame.reshape(-1, channels)[:, 0]
    return sample_rate, audio_frame


def normalize_wav_bytes(wav_payload: bytes) -> bytes:
    """Return a 16-bit mono WAV with concrete RIFF/data sizes."""
    sample_rate, audio_frame = read_wav_audio(wav_payload)
    return wav_bytes(audio_frame, sample_rate)


def wav_duration_seconds(wav_payload: bytes) -> float:
    """Return the duration of a WAV payload."""
    sample_rate, audio_frame = read_wav_audio(wav_payload)
    return len(audio_frame) / float(sample_rate) if sample_rate else 0.0


async def binary_response_bytes(response: Any) -> bytes:
    """Read bytes from an OpenAI binary response object."""
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content

    read = getattr(response, "read", None)
    if callable(read):
        result = read()
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, bytes):
            return result

    aread = getattr(response, "aread", None)
    if callable(aread):
        result = await aread()
        if isinstance(result, bytes):
            return result

    raise TypeError(f"Unsupported binary response type: {type(response).__name__}")
