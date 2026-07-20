import wave
from pathlib import Path

import numpy as np

from reachy_mini_conversation_app.audio.pcm import wav_bytes, normalize_wav_bytes, wav_duration_seconds


def test_wav_duration_uses_actual_payload_for_streaming_size_placeholders() -> None:
    """Streaming WAV responses can use 0xffffffff chunk sizes."""
    payload = bytearray(wav_bytes(np.zeros(24_000, dtype=np.int16), 24_000))
    payload[4:8] = b"\xff\xff\xff\xff"

    data_offset = payload.index(b"data")
    payload[data_offset + 4 : data_offset + 8] = b"\xff\xff\xff\xff"

    assert wav_duration_seconds(bytes(payload)) == 1.0


def test_normalize_wav_bytes_rewrites_streaming_size_placeholders(tmp_path: Path) -> None:
    """Some local STT servers reject WAV files with streaming placeholder sizes."""
    payload = bytearray(wav_bytes(np.zeros(24_000, dtype=np.int16), 24_000))
    payload[4:8] = b"\xff\xff\xff\xff"

    data_offset = payload.index(b"data")
    payload[data_offset + 4 : data_offset + 8] = b"\xff\xff\xff\xff"

    normalized = normalize_wav_bytes(bytes(payload))
    assert normalized[4:8] != b"\xff\xff\xff\xff"

    normalized_path = tmp_path / "normalized.wav"
    normalized_path.write_bytes(normalized)
    with wave.open(str(normalized_path), "rb") as wav_file:
        assert wav_file.getnframes() == 24_000
