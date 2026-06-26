import os
from typing import Any
from pathlib import Path

import reachy_mini_conversation_app.config as config_mod
import reachy_mini_conversation_app.backend_runtime as runtime_mod


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ATTRS = (
    "BACKEND_PROVIDER",
    "REALTIME_TRANSCRIPTION_LANGUAGE",
    "OPENAI_REALTIME_API_KEY",
    "OPENAI_REALTIME_BASE_URL",
    "OPENAI_REALTIME_MODEL",
    "OPENAI_REALTIME_VOICE",
    "HF_REALTIME_CONNECTION_MODE",
    "HF_REALTIME_SESSION_URL",
    "HF_REALTIME_WS_URL",
    "HF_REALTIME_MODEL",
    "HF_REALTIME_VOICE",
    "HF_TOKEN",
    "CHAT_API_KEY",
    "CHAT_BASE_URL",
    "CHAT_MODEL_NAME",
    "STT_API_KEY",
    "STT_BASE_URL",
    "STT_MODEL_NAME",
    "TTS_API_KEY",
    "TTS_BASE_URL",
    "TTS_MODEL_NAME",
    "TTS_VOICE",
    "MIC_TRANSCRIPTION_RMS_THRESHOLD",
    "MIC_TRANSCRIPTION_MIN_AUDIO_MS",
    "MIC_TRANSCRIPTION_SILENCE_MS",
    "MIC_TRANSCRIPTION_MAX_AUDIO_MS",
    "HF_HOME",
    "LOCAL_VISION_MODEL",
)


def _config_snapshot() -> dict[str, Any]:
    return {name: getattr(config_mod.config, name) for name in CONFIG_ATTRS}


def _restore_config_snapshot(snapshot: dict[str, Any]) -> None:
    for name, value in snapshot.items():
        setattr(config_mod.config, name, value)


def _restore_process_env(snapshot: dict[str, str | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_documented_env_templates_validate_with_exported_system_keys(monkeypatch: Any) -> None:
    """Checked-in starter env files should validate without copying global OpenAI secrets."""
    snapshot = _config_snapshot()
    monkeypatch.setattr(config_mod, "_dotenv_loaded_keys", set())
    monkeypatch.setattr(config_mod, "_dotenv_values", {})
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", "global-openai-key")
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "NVIDIA_INFERENCE_API_KEY", "global-nvidia-key")

    expectations = {
        ".env.example": config_mod.BACKEND_LOCAL_STT,
        ".env.local-stt.example": config_mod.BACKEND_LOCAL_STT,
        ".env.hf-realtime.example": config_mod.BACKEND_HF_REALTIME,
        ".env.openai-realtime.example": config_mod.BACKEND_OPENAI_REALTIME,
    }

    try:
        for filename, expected_backend in expectations.items():
            raw_values = config_mod.dotenv_values(PROJECT_ROOT / filename, interpolate=False)
            expanded_values = config_mod._expand_dotenv_values(raw_values)

            config_mod.apply_config_values(expanded_values, inherit_current=False)

            assert config_mod.config.BACKEND_PROVIDER == expected_backend
            assert runtime_mod.backend_config_error() is None

            if filename == ".env.openai-realtime.example":
                assert "OPENAI_REALTIME_API_KEY" not in raw_values
                assert "OPENAI_API_KEY" not in raw_values
                assert config_mod.openai_realtime_api_key() == "global-openai-key"
    finally:
        _restore_config_snapshot(snapshot)


def test_load_dotenv_file_expands_system_env_and_applies_values(tmp_path: Any, monkeypatch: Any) -> None:
    """Explicit instance .env loading supports values sourced from system env names."""
    tracked_attrs = {
        "BACKEND_PROVIDER": config_mod.config.BACKEND_PROVIDER,
        "OPENAI_REALTIME_API_KEY": config_mod.config.OPENAI_REALTIME_API_KEY,
        "CHAT_API_KEY": config_mod.config.CHAT_API_KEY,
        "MIC_TRANSCRIPTION_RMS_THRESHOLD": config_mod.config.MIC_TRANSCRIPTION_RMS_THRESHOLD,
    }
    previous_dotenv_path = config_mod._dotenv_path
    previous_dotenv_values = dict(config_mod._dotenv_values)
    previous_loaded_keys = set(config_mod._dotenv_loaded_keys)
    env_snapshot = {
        name: os.environ.get(name) for name in {"BACKEND_PROVIDER", "CHAT_API_KEY", "MIC_TRANSCRIPTION_RMS_THRESHOLD"}
    }
    monkeypatch.setattr(config_mod, "_skip_dotenv", False)
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "NVIDIA_INFERENCE_API_KEY", "system-secret")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "BACKEND_PROVIDER=local_stt",
                "CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}",
                "MIC_TRANSCRIPTION_RMS_THRESHOLD=123.5",
            ]
        )
    )

    try:
        assert config_mod.load_dotenv_file(env_path) is True
        assert config_mod.config.BACKEND_PROVIDER == config_mod.BACKEND_LOCAL_STT
        assert config_mod.config.CHAT_API_KEY == "system-secret"
        assert config_mod.config.MIC_TRANSCRIPTION_RMS_THRESHOLD == 123.5
    finally:
        for name, value in tracked_attrs.items():
            setattr(config_mod.config, name, value)
        config_mod._dotenv_path = previous_dotenv_path
        config_mod._dotenv_values = previous_dotenv_values
        config_mod._dotenv_loaded_keys = previous_loaded_keys
        _restore_process_env(env_snapshot)


def test_load_dotenv_file_resets_missing_values_instead_of_inheriting_stale_config(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    """Candidate dotenv files should be validated as one config, not layered over stale values."""
    tracked_attrs = {
        "BACKEND_PROVIDER": config_mod.config.BACKEND_PROVIDER,
        "CHAT_API_KEY": config_mod.config.CHAT_API_KEY,
        "CHAT_BASE_URL": config_mod.config.CHAT_BASE_URL,
        "CHAT_MODEL_NAME": config_mod.config.CHAT_MODEL_NAME,
        "STT_API_KEY": config_mod.config.STT_API_KEY,
        "STT_BASE_URL": config_mod.config.STT_BASE_URL,
        "STT_MODEL_NAME": config_mod.config.STT_MODEL_NAME,
        "TTS_API_KEY": config_mod.config.TTS_API_KEY,
        "TTS_BASE_URL": config_mod.config.TTS_BASE_URL,
        "TTS_MODEL_NAME": config_mod.config.TTS_MODEL_NAME,
    }
    previous_dotenv_path = config_mod._dotenv_path
    previous_dotenv_values = dict(config_mod._dotenv_values)
    previous_loaded_keys = set(config_mod._dotenv_loaded_keys)
    env_snapshot = {
        name: os.environ.get(name)
        for name in {
            "BACKEND_PROVIDER",
            "CHAT_API_KEY",
            "CHAT_BASE_URL",
            "CHAT_MODEL_NAME",
            "STT_BASE_URL",
            "TTS_BASE_URL",
        }
    }
    monkeypatch.setattr(config_mod, "_skip_dotenv", False)
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)
    monkeypatch.setattr(config_mod.config, "CHAT_API_KEY", "stale-chat-key")
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "https://stale-chat.test/v1")
    monkeypatch.setattr(config_mod.config, "CHAT_MODEL_NAME", "stale-chat-model")
    monkeypatch.setattr(config_mod.config, "STT_API_KEY", "stale-stt-key")
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "https://stale-stt.test/v1")
    monkeypatch.setattr(config_mod.config, "STT_MODEL_NAME", "stale-stt-model")
    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "stale-tts-key")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "https://stale-tts.test/v1")
    monkeypatch.setattr(config_mod.config, "TTS_MODEL_NAME", "stale-tts-model")
    env_path = tmp_path / ".env.local-stt"
    env_path.write_text(
        "\n".join(
            [
                "BACKEND_PROVIDER=local_stt",
                "CHAT_API_KEY=candidate-chat-key",
                "CHAT_BASE_URL=https://candidate-chat.test/v1",
                "CHAT_MODEL_NAME=candidate-chat-model",
                "STT_BASE_URL=https://candidate-stt.test/v1",
            ]
        ),
        encoding="utf-8",
    )

    try:
        assert config_mod.load_dotenv_file(env_path) is True
        assert config_mod.config.CHAT_API_KEY == "candidate-chat-key"
        assert config_mod.config.STT_API_KEY == "not-needed"
        assert config_mod.config.STT_MODEL_NAME == "whisper-1"
        assert config_mod.config.TTS_BASE_URL is None
        assert runtime_mod.backend_config_error() == "TTS_BASE_URL is missing for BACKEND_PROVIDER=local_stt."
    finally:
        for name, value in tracked_attrs.items():
            setattr(config_mod.config, name, value)
        config_mod._dotenv_path = previous_dotenv_path
        config_mod._dotenv_values = previous_dotenv_values
        config_mod._dotenv_loaded_keys = previous_loaded_keys
        _restore_process_env(env_snapshot)


def test_load_dotenv_file_does_not_expand_from_previous_dotenv_values(tmp_path: Any, monkeypatch: Any) -> None:
    """A previous dotenv's OPENAI_API_KEY should not masquerade as a global shell key."""
    tracked_attrs = {
        "BACKEND_PROVIDER": config_mod.config.BACKEND_PROVIDER,
        "OPENAI_REALTIME_API_KEY": config_mod.config.OPENAI_REALTIME_API_KEY,
        "CHAT_API_KEY": config_mod.config.CHAT_API_KEY,
    }
    previous_dotenv_path = config_mod._dotenv_path
    previous_dotenv_values = dict(config_mod._dotenv_values)
    previous_loaded_keys = set(config_mod._dotenv_loaded_keys)
    env_snapshot = {name: os.environ.get(name) for name in {"OPENAI_API_KEY", "CHAT_API_KEY", "BACKEND_PROVIDER"}}
    monkeypatch.setattr(config_mod, "_skip_dotenv", False)
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    first_env = tmp_path / ".env.first"
    first_env.write_text("OPENAI_API_KEY=stale-dotenv-key\n", encoding="utf-8")
    second_env = tmp_path / ".env.second"
    second_env.write_text(
        "\n".join(
            [
                "BACKEND_PROVIDER=local_stt",
                "CHAT_API_KEY=${OPENAI_API_KEY}",
            ]
        ),
        encoding="utf-8",
    )

    try:
        assert config_mod.load_dotenv_file(first_env) is True
        assert os.environ.get("OPENAI_API_KEY") == "stale-dotenv-key"
        assert config_mod.load_dotenv_file(second_env) is True
        assert "OPENAI_API_KEY" not in os.environ
        assert config_mod.config.CHAT_API_KEY is None
    finally:
        for name, value in tracked_attrs.items():
            setattr(config_mod.config, name, value)
        config_mod._dotenv_path = previous_dotenv_path
        config_mod._dotenv_values = previous_dotenv_values
        config_mod._dotenv_loaded_keys = previous_loaded_keys
        _restore_process_env(env_snapshot)


def test_apply_config_values_accepts_exact_backend_and_normalizes_hf_mode(monkeypatch: Any) -> None:
    """Instance .env loading accepts the documented backend selector values."""
    tracked_attrs = {
        "BACKEND_PROVIDER": config_mod.config.BACKEND_PROVIDER,
        "HF_REALTIME_CONNECTION_MODE": config_mod.config.HF_REALTIME_CONNECTION_MODE,
        "HF_REALTIME_SESSION_URL": config_mod.config.HF_REALTIME_SESSION_URL,
        "HF_REALTIME_WS_URL": config_mod.config.HF_REALTIME_WS_URL,
        "HF_REALTIME_MODEL": config_mod.config.HF_REALTIME_MODEL,
        "HF_REALTIME_VOICE": config_mod.config.HF_REALTIME_VOICE,
        "HF_TOKEN": config_mod.config.HF_TOKEN,
    }
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_CONNECTION_MODE", config_mod.HF_REALTIME_CONNECTION_DEPLOYED)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_SESSION_URL", config_mod.HF_REALTIME_SESSION_PROXY_URL)

    try:
        config_mod.apply_config_values(
            {
                "BACKEND_PROVIDER": "hf_realtime",
                "HF_REALTIME_CONNECTION_MODE": "local",
                "HF_REALTIME_SESSION_URL": "https://hf-session.test/session",
                "HF_REALTIME_WS_URL": "ws://localhost:8765/v1/realtime",
                "HF_REALTIME_MODEL": "hf/realtime-model",
                "HF_REALTIME_VOICE": "Aiden",
                "HF_TOKEN": "hf-token",
            }
        )

        assert config_mod.config.BACKEND_PROVIDER == config_mod.BACKEND_HF_REALTIME
        assert config_mod.config.HF_REALTIME_CONNECTION_MODE == config_mod.HF_REALTIME_CONNECTION_LOCAL
        assert config_mod.config.HF_REALTIME_SESSION_URL == "https://hf-session.test/session"
        assert config_mod.config.HF_REALTIME_WS_URL == "ws://localhost:8765/v1/realtime"
        assert config_mod.config.HF_REALTIME_MODEL == "hf/realtime-model"
        assert config_mod.config.HF_REALTIME_VOICE == "Aiden"
        assert config_mod.config.HF_TOKEN == "hf-token"
    finally:
        for name, value in tracked_attrs.items():
            setattr(config_mod.config, name, value)


def test_backend_config_error_requires_explicit_backend_provider(monkeypatch: Any) -> None:
    """The backend selector is required; the app should not silently choose a backend."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "")

    assert runtime_mod.backend_config_error() == (
        "BACKEND_PROVIDER is missing; set it to one of hf_realtime, local_stt, openai_realtime."
    )


def test_backend_config_error_rejects_unknown_backend_provider(monkeypatch: Any) -> None:
    """Unknown backend values should fail instead of falling back to another provider."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "huggingface")

    assert runtime_mod.backend_config_error() == (
        "Unknown BACKEND_PROVIDER='huggingface'; expected one of hf_realtime, local_stt, openai_realtime."
    )


def test_apply_config_values_uses_global_openai_key_for_realtime(monkeypatch: Any) -> None:
    """OpenAI Realtime can use the globally exported OPENAI_API_KEY directly."""
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", "global-openai-key")

    config_mod.apply_config_values(
        {
            "BACKEND_PROVIDER": "openai_realtime",
            "OPENAI_REALTIME_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_REALTIME_MODEL": "gpt-realtime",
        },
        inherit_current=False,
    )

    assert config_mod.config.OPENAI_REALTIME_API_KEY == "global-openai-key"
    assert config_mod.openai_realtime_api_key() == "global-openai-key"
    assert runtime_mod.backend_config_error() is None


def test_apply_config_values_normalizes_markdown_urls(monkeypatch: Any) -> None:
    """Rendered Markdown links pasted into .env should become plain URL values."""
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "")
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")

    config_mod.apply_config_values(
        {
            "CHAT_BASE_URL": "[https://inference-api.nvidia.com/v1](https://inference-api.nvidia.com/v1)",
            "STT_BASE_URL": "<http://192.168.1.57:8000/v1>",
            "TTS_BASE_URL": "[https://api.openai.com/v1](https://api.openai.com/v1)",
        }
    )

    assert config_mod.config.CHAT_BASE_URL == "https://inference-api.nvidia.com/v1"
    assert config_mod.config.STT_BASE_URL == "http://192.168.1.57:8000/v1"
    assert config_mod.config.TTS_BASE_URL == "https://api.openai.com/v1"


def test_apply_config_values_preserves_placeholder_urls(monkeypatch: Any) -> None:
    """Placeholder URL values should remain detectable as missing config."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)
    monkeypatch.setattr(config_mod.config, "CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(config_mod.config, "CHAT_MODEL_NAME", "test-chat-model")
    monkeypatch.setattr(config_mod.config, "STT_API_KEY", "not-needed")
    monkeypatch.setattr(config_mod.config, "STT_MODEL_NAME", "whisper-1")
    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "not-needed")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config_mod.config, "TTS_MODEL_NAME", "gpt-4o-mini-tts")

    config_mod.apply_config_values({"STT_BASE_URL": "<set-me>"})

    assert config_mod.config.STT_BASE_URL == "<set-me>"
    assert runtime_mod.backend_config_error() == "STT_BASE_URL is missing for BACKEND_PROVIDER=local_stt."


def test_backend_config_error_for_openai_realtime(monkeypatch: Any) -> None:
    """OpenAI Realtime mode requires the standard OpenAI key and model settings."""
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")

    error = runtime_mod.backend_config_error()

    assert error is not None
    assert "OPENAI_API_KEY is missing for BACKEND_PROVIDER=openai_realtime." in error
    assert "OPENAI_REALTIME_API_KEY only if this app needs a different OpenAI key" in error

    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "test-key")
    assert runtime_mod.backend_config_error() is None


def test_backend_config_error_for_openai_realtime_accepts_global_openai_key(monkeypatch: Any) -> None:
    """The OpenAI Realtime backend can use the standard global OpenAI key."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", "global-openai-key")

    assert config_mod.openai_realtime_api_key() == "global-openai-key"
    assert runtime_mod.backend_config_error() is None


def test_openai_realtime_api_key_accepts_current_process_env(monkeypatch: Any) -> None:
    """OpenAI Realtime can see a global OpenAI key added after config import."""
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "_dotenv_loaded_keys", set())
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "late-global-openai-key")

    assert config_mod.openai_realtime_api_key() == "late-global-openai-key"


def test_openai_realtime_api_key_ignores_dotenv_managed_process_env(monkeypatch: Any) -> None:
    """A dotenv-loaded OPENAI_API_KEY should not masquerade as a global key."""
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "_dotenv_loaded_keys", {"OPENAI_API_KEY"})
    monkeypatch.setattr(config_mod, "_dotenv_values", {"OPENAI_API_KEY": "dotenv-openai-key"})
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "dotenv-openai-key")

    assert config_mod.openai_realtime_api_key() is None


def test_openai_realtime_api_key_accepts_current_process_env_after_blank_dotenv(monkeypatch: Any) -> None:
    """A blank dotenv key should not prevent a real process OPENAI_API_KEY from working."""
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "_dotenv_loaded_keys", {"OPENAI_API_KEY"})
    monkeypatch.setattr(config_mod, "_dotenv_values", {"OPENAI_API_KEY": ""})
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "late-global-openai-key")

    assert config_mod.openai_realtime_api_key() == "late-global-openai-key"


def test_apply_config_values_uses_openai_api_key_for_realtime(monkeypatch: Any) -> None:
    """Explicit dotenv loading should promote OPENAI_API_KEY into the realtime config."""
    monkeypatch.delitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")

    config_mod.apply_config_values({"OPENAI_API_KEY": "dotenv-openai-key"})

    assert config_mod.config.OPENAI_REALTIME_API_KEY == "dotenv-openai-key"


def test_backend_config_error_for_hf_local_realtime(monkeypatch: Any) -> None:
    """HF local realtime mode requires a websocket URL."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_HF_REALTIME)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_CONNECTION_MODE", config_mod.HF_REALTIME_CONNECTION_LOCAL)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_WS_URL", "")

    assert runtime_mod.backend_config_error() == "HF_REALTIME_WS_URL is missing for HF_REALTIME_CONNECTION_MODE=local."

    monkeypatch.setattr(config_mod.config, "HF_REALTIME_WS_URL", "ws://localhost:8765/v1/realtime")
    assert runtime_mod.backend_config_error() is None


def test_backend_config_error_for_local_stt(monkeypatch: Any) -> None:
    """Local STT mode requires chat, STT, and TTS endpoints."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)
    monkeypatch.setattr(config_mod.config, "CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(config_mod.config, "CHAT_MODEL_NAME", "test-chat-model")
    monkeypatch.setattr(config_mod.config, "STT_API_KEY", "not-needed")
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "https://stt.test/v1")
    monkeypatch.setattr(config_mod.config, "STT_MODEL_NAME", "whisper-large-v3")
    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "not-needed")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")
    monkeypatch.setattr(config_mod.config, "TTS_MODEL_NAME", "test-tts-model")

    assert runtime_mod.backend_config_error() == "TTS_BASE_URL is missing for BACKEND_PROVIDER=local_stt."

    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "https://tts.test/v1")
    assert runtime_mod.backend_config_error() is None

    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "<set-me>")
    assert runtime_mod.backend_config_error() == "TTS_API_KEY is missing for BACKEND_PROVIDER=local_stt."
