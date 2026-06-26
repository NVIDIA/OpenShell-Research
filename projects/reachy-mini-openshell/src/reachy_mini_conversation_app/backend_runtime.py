from typing import Final, Literal
from dataclasses import dataclass

from reachy_mini_conversation_app.config import (
    BACKEND_LOCAL_STT,
    BACKEND_PROVIDERS,
    BACKEND_HF_REALTIME,
    BACKEND_OPENAI_REALTIME,
    HF_REALTIME_CONNECTION_LOCAL,
    config,
    is_configured_value,
    openai_realtime_api_key,
)


OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE: Final[int] = 24_000
HF_REALTIME_STREAM_SAMPLE_RATE: Final[int] = 16_000

BackendTransport = Literal["realtime", "local_stt", "unknown"]


@dataclass(frozen=True)
class BackendRuntimeSpec:
    """Runtime traits derived from the single BACKEND_PROVIDER selector."""

    provider: str
    transport: BackendTransport
    stream_sample_rate: int
    realtime_model: str = ""
    realtime_voice: str = ""
    refresh_realtime_client_on_retry: bool = False

    @property
    def uses_realtime(self) -> bool:
        """Return whether microphone/text input should use a realtime websocket."""
        return self.transport == "realtime"

    @property
    def uses_local_stt(self) -> bool:
        """Return whether microphone input should use STT plus Chat Completions."""
        return self.transport == "local_stt"


def selected_backend() -> BackendRuntimeSpec:
    """Return the runtime backend selected by BACKEND_PROVIDER."""
    provider = config.BACKEND_PROVIDER
    if provider == BACKEND_OPENAI_REALTIME:
        return BackendRuntimeSpec(
            provider=provider,
            transport="realtime",
            stream_sample_rate=OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE,
            realtime_model=config.OPENAI_REALTIME_MODEL or "",
            realtime_voice=config.OPENAI_REALTIME_VOICE or "",
            refresh_realtime_client_on_retry=False,
        )

    if provider == BACKEND_HF_REALTIME:
        return BackendRuntimeSpec(
            provider=provider,
            transport="realtime",
            stream_sample_rate=HF_REALTIME_STREAM_SAMPLE_RATE,
            realtime_model=(config.HF_REALTIME_MODEL or "").strip(),
            realtime_voice=config.HF_REALTIME_VOICE or "",
            refresh_realtime_client_on_retry=True,
        )

    if provider == BACKEND_LOCAL_STT:
        return BackendRuntimeSpec(
            provider=provider,
            transport="local_stt",
            stream_sample_rate=OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE,
        )

    return BackendRuntimeSpec(
        provider=provider,
        transport="unknown",
        stream_sample_rate=OPENAI_COMPATIBLE_STREAM_SAMPLE_RATE,
    )


def configured_marker(value: str | None) -> str:
    """Return a display-safe configured/missing marker."""
    return "configured" if is_configured_value(value) else "missing"


def local_stt_chat_config_error() -> str | None:
    """Return a config error for the local-STT Chat Completions component."""
    if not is_configured_value(config.CHAT_API_KEY):
        return (
            "CHAT_API_KEY is missing for BACKEND_PROVIDER=local_stt. If .env uses "
            "CHAT_API_KEY=${NVIDIA_INFERENCE_API_KEY}, make sure NVIDIA_INFERENCE_API_KEY is exported "
            "in the shell that starts the app."
        )
    if not is_configured_value(config.CHAT_BASE_URL):
        return "CHAT_BASE_URL is missing for BACKEND_PROVIDER=local_stt."
    if not is_configured_value(config.CHAT_MODEL_NAME):
        return "CHAT_MODEL_NAME is missing for BACKEND_PROVIDER=local_stt."
    return None


def local_stt_transcription_config_error() -> str | None:
    """Return a config error for the local-STT speech-to-text component."""
    if not is_configured_value(config.STT_API_KEY):
        return "STT_API_KEY is missing for BACKEND_PROVIDER=local_stt."
    if not is_configured_value(config.STT_BASE_URL):
        return "STT_BASE_URL is missing for BACKEND_PROVIDER=local_stt."
    if not is_configured_value(config.STT_MODEL_NAME):
        return "STT_MODEL_NAME is missing for BACKEND_PROVIDER=local_stt."
    return None


def local_stt_tts_config_error() -> str | None:
    """Return a config error for the local-STT text-to-speech component."""
    if not is_configured_value(config.TTS_API_KEY):
        return "TTS_API_KEY is missing for BACKEND_PROVIDER=local_stt."
    if not is_configured_value(config.TTS_BASE_URL):
        return "TTS_BASE_URL is missing for BACKEND_PROVIDER=local_stt."
    if not is_configured_value(config.TTS_MODEL_NAME):
        return "TTS_MODEL_NAME is missing for BACKEND_PROVIDER=local_stt."
    return None


def backend_config_error() -> str | None:
    """Return a startup-blocking config error for the selected backend, if any."""
    backend = selected_backend()
    expected = ", ".join(sorted(BACKEND_PROVIDERS))
    if not is_configured_value(backend.provider):
        return f"BACKEND_PROVIDER is missing; set it to one of {expected}."

    if backend.provider not in BACKEND_PROVIDERS:
        return f"Unknown BACKEND_PROVIDER={backend.provider!r}; expected one of {expected}."

    if backend.provider == BACKEND_OPENAI_REALTIME:
        if openai_realtime_api_key() is None:
            return (
                "OPENAI_API_KEY is missing for BACKEND_PROVIDER=openai_realtime. "
                "Export OPENAI_API_KEY in the shell that starts the app; set OPENAI_REALTIME_API_KEY "
                "only if this app needs a different OpenAI key."
            )
        if not is_configured_value(config.OPENAI_REALTIME_BASE_URL):
            return "OPENAI_REALTIME_BASE_URL is missing for BACKEND_PROVIDER=openai_realtime."
        if not is_configured_value(backend.realtime_model):
            return "OPENAI_REALTIME_MODEL is missing for BACKEND_PROVIDER=openai_realtime."
        return None

    if backend.provider == BACKEND_HF_REALTIME:
        if config.HF_REALTIME_CONNECTION_MODE == HF_REALTIME_CONNECTION_LOCAL and not is_configured_value(
            config.HF_REALTIME_WS_URL
        ):
            return "HF_REALTIME_WS_URL is missing for HF_REALTIME_CONNECTION_MODE=local."
        return None

    if backend.provider == BACKEND_LOCAL_STT:
        return local_stt_chat_config_error() or local_stt_transcription_config_error() or local_stt_tts_config_error()

    return None


def local_stt_stage_config_error(stage: str) -> str | None:
    """Return the config error relevant to a local_stt live-check stage."""
    if stage in {"stt", "stt-probe"}:
        return local_stt_transcription_config_error()
    if stage == "chat":
        return local_stt_chat_config_error()
    if stage == "tts":
        return local_stt_tts_config_error()
    return backend_config_error()


def describe_selected_backend() -> list[str]:
    """Return a non-secret summary of the selected backend configuration."""
    backend = selected_backend()
    lines = [f"backend={backend.provider}"]
    if backend.provider == BACKEND_OPENAI_REALTIME:
        lines.extend(
            [
                f"openai_realtime.base_url={config.OPENAI_REALTIME_BASE_URL}",
                f"openai_realtime.model={backend.realtime_model}",
                f"openai_realtime.voice={backend.realtime_voice}",
                f"openai_realtime.api_key={configured_marker(openai_realtime_api_key())}",
            ]
        )
    elif backend.provider == BACKEND_HF_REALTIME:
        lines.extend(
            [
                f"hf_realtime.connection_mode={config.HF_REALTIME_CONNECTION_MODE}",
                f"hf_realtime.session_url={config.HF_REALTIME_SESSION_URL}",
                f"hf_realtime.ws_url={config.HF_REALTIME_WS_URL or '<deployed session broker>'}",
                f"hf_realtime.model={backend.realtime_model or '<backend default>'}",
                f"hf_realtime.voice={backend.realtime_voice}",
                f"hf_realtime.token={configured_marker(config.HF_TOKEN)}",
            ]
        )
    elif backend.provider == BACKEND_LOCAL_STT:
        lines.extend(
            [
                f"chat.base_url={config.CHAT_BASE_URL}",
                f"chat.model={config.CHAT_MODEL_NAME}",
                f"chat.api_key={configured_marker(config.CHAT_API_KEY)}",
                f"stt.base_url={config.STT_BASE_URL}",
                f"stt.model={config.STT_MODEL_NAME}",
                f"stt.api_key={configured_marker(config.STT_API_KEY)}",
                f"tts.base_url={config.TTS_BASE_URL}",
                f"tts.model={config.TTS_MODEL_NAME}",
                f"tts.voice={config.TTS_VOICE}",
                f"tts.api_key={configured_marker(config.TTS_API_KEY)}",
            ]
        )
    return lines
