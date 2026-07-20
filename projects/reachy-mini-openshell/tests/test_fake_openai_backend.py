import wave
import importlib.util
from typing import Any
from pathlib import Path

from fastapi.testclient import TestClient


def _load_fake_backend_module() -> Any:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "fake_openai_backend.py"
    spec = importlib.util.spec_from_file_location("fake_openai_backend", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fake_openai_backend_exposes_stt_chat_and_tts(tmp_path: Path) -> None:
    """The local smoke backend should mimic the OpenAI-compatible endpoints the app calls."""
    fake_backend = _load_fake_backend_module()
    app = fake_backend.build_app(
        transcript="Reachy, use the sweep_look tool.",
        assistant_text="I swept my gaze.",
        tool_name="sweep_look",
        call_tool=True,
        audio_duration_seconds=0.1,
    )
    client = TestClient(app)

    models_response = client.get("/v1/models")
    assert models_response.status_code == 200
    assert {model["id"] for model in models_response.json()["data"]} == {
        "fake-whisper",
        "fake-chat",
        "fake-tts",
    }

    transcription_response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("input.wav", b"fake audio", "audio/wav")},
        data={"model": "fake-whisper"},
    )
    assert transcription_response.status_code == 200
    assert transcription_response.json() == {"text": "Reachy, use the sweep_look tool."}

    first_chat_response = client.post(
        "/v1/chat/completions",
        json={"model": "fake-chat", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert first_chat_response.status_code == 200
    first_message = first_chat_response.json()["choices"][0]["message"]
    assert first_message["tool_calls"][0]["function"] == {"name": "sweep_look", "arguments": "{}"}

    final_chat_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fake-chat",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "tool", "tool_call_id": "call_fake_reachy_tool", "content": "{}"},
            ],
        },
    )
    assert final_chat_response.status_code == 200
    assert final_chat_response.json()["choices"][0]["message"]["content"] == "I swept my gaze."

    speech_response = client.post(
        "/v1/audio/speech",
        json={"model": "fake-tts", "voice": "fake-voice", "input": "I swept my gaze."},
    )
    assert speech_response.status_code == 200
    assert speech_response.headers["content-type"] == "audio/wav"
    speech_path = tmp_path / "fake-backend-speech.wav"
    try:
        speech_path.write_bytes(speech_response.content)
        with wave.open(str(speech_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 24_000
            assert wav_file.getnframes() > 0
    finally:
        speech_path.unlink(missing_ok=True)
