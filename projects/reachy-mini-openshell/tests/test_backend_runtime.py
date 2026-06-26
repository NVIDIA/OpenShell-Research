from typing import Any

import reachy_mini_conversation_app.config as config_mod
import reachy_mini_conversation_app.backend_runtime as runtime_mod


def test_selected_backend_for_openai_realtime(monkeypatch: Any) -> None:
    """OpenAI Realtime uses realtime transport with the configured model and voice."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_VOICE", "cedar")

    backend = runtime_mod.selected_backend()

    assert backend.provider == config_mod.BACKEND_OPENAI_REALTIME
    assert backend.uses_realtime is True
    assert backend.uses_local_stt is False
    assert backend.stream_sample_rate == runtime_mod.OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE
    assert backend.realtime_model == "gpt-realtime"
    assert backend.realtime_voice == "cedar"
    assert backend.refresh_realtime_client_on_retry is False


def test_selected_backend_for_hf_realtime(monkeypatch: Any) -> None:
    """HF realtime keeps model configurable and refreshes session-backed clients."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_HF_REALTIME)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_MODEL", "hf/model")
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_VOICE", "Aiden")

    backend = runtime_mod.selected_backend()

    assert backend.provider == config_mod.BACKEND_HF_REALTIME
    assert backend.uses_realtime is True
    assert backend.uses_local_stt is False
    assert backend.stream_sample_rate == runtime_mod.HF_REALTIME_STREAM_SAMPLE_RATE
    assert backend.realtime_model == "hf/model"
    assert backend.realtime_voice == "Aiden"
    assert backend.refresh_realtime_client_on_retry is True


def test_selected_backend_for_local_stt(monkeypatch: Any) -> None:
    """Local STT mode routes microphone input through STT plus Chat Completions."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)

    backend = runtime_mod.selected_backend()

    assert backend.provider == config_mod.BACKEND_LOCAL_STT
    assert backend.uses_realtime is False
    assert backend.uses_local_stt is True
    assert backend.stream_sample_rate == runtime_mod.OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE
    assert backend.realtime_model == ""
    assert backend.realtime_voice == ""
    assert backend.refresh_realtime_client_on_retry is False


def test_selected_backend_for_unknown_provider(monkeypatch: Any) -> None:
    """Unknown providers are represented explicitly while validation reports the error."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "bad-backend")

    backend = runtime_mod.selected_backend()

    assert backend.provider == "bad-backend"
    assert backend.transport == "unknown"
    assert backend.uses_realtime is False
    assert backend.uses_local_stt is False
