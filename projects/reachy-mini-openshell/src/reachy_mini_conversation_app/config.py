import os
import re
import sys
import logging
from typing import Mapping
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl, urlunsplit

from dotenv import find_dotenv, dotenv_values


LOCKED_PROFILE = "_reachy_mini_conversation_app_locked_profile"
DEFAULT_PROFILES_DIRECTORY = Path(__file__).parent / "profiles"

BACKEND_OPENAI_REALTIME = "openai_realtime"
BACKEND_HF_REALTIME = "hf_realtime"
BACKEND_LOCAL_STT = "local_stt"
BACKEND_PROVIDERS = {
    BACKEND_OPENAI_REALTIME,
    BACKEND_HF_REALTIME,
    BACKEND_LOCAL_STT,
}

HF_REALTIME_CONNECTION_DEPLOYED = "deployed"
HF_REALTIME_CONNECTION_LOCAL = "local"
HF_REALTIME_SESSION_PROXY_URL = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"

logger = logging.getLogger(__name__)
_MARKDOWN_URL_RE = re.compile(r"^\[(?P<label>(?:https?|wss?)://[^\]]+)\]\((?P<url>(?:https?|wss?)://[^)]+)\)$")
_ENV_REF_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
_PLACEHOLDER_VALUES = {"<set-me>", "set-me"}
_ORIGINAL_PROCESS_ENV = dict(os.environ)


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag."""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


_profile_path = DEFAULT_PROFILES_DIRECTORY / LOCKED_PROFILE
_instructions_file = _profile_path / "instructions.txt"
if not _profile_path.is_dir():
    print(f"Error: locked profile '{LOCKED_PROFILE}' does not exist in {DEFAULT_PROFILES_DIRECTORY}", file=sys.stderr)
    sys.exit(1)
if not _instructions_file.is_file():
    print(f"Error: locked profile '{LOCKED_PROFILE}' has no instructions.txt", file=sys.stderr)
    sys.exit(1)

_skip_dotenv = _env_flag("REACHY_MINI_SKIP_DOTENV", default=False)
_dotenv_path = ""
_dotenv_values: dict[str, str | None] = {}
_dotenv_loaded_keys: set[str] = set()


def _expand_dotenv_values(raw_values: Mapping[str, str | None]) -> dict[str, str | None]:
    """Expand ${VAR} references using only this file plus the original shell env."""
    context = dict(_ORIGINAL_PROCESS_ENV)
    context.update({key: value for key, value in raw_values.items() if value is not None})

    for _ in range(5):
        changed = False
        for key, value in list(context.items()):
            if not isinstance(value, str):
                continue

            expanded = _ENV_REF_RE.sub(lambda match: context.get(match.group("name"), "") or "", value)
            if expanded != value:
                context[key] = expanded
                changed = True
        if not changed:
            break

    return {key: context.get(key) if value is not None else None for key, value in raw_values.items()}


def _apply_dotenv_values_to_process_env(values: Mapping[str, str | None]) -> None:
    """Expose only the active dotenv keys to libraries that read os.environ."""
    global _dotenv_loaded_keys

    next_keys = {key for key, value in values.items() if value is not None}
    for key in _dotenv_loaded_keys - next_keys:
        original_value = _ORIGINAL_PROCESS_ENV.get(key)
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value

    for key in next_keys:
        value = values[key]
        if value is not None:
            os.environ[key] = value

    _dotenv_loaded_keys = next_keys


def _load_dotenv_file_values(env_path: str | Path) -> bool:
    """Load a dotenv file into process env and the module's cached values."""
    global _dotenv_path, _dotenv_values

    path = Path(env_path)
    if not path.is_file():
        return False

    _dotenv_path = str(path)
    _dotenv_values = _expand_dotenv_values(dotenv_values(path, interpolate=False))
    _apply_dotenv_values_to_process_env(_dotenv_values)
    logger.info("Configuration loaded from %s", path)
    return True


if _skip_dotenv:
    logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
else:
    explicit_dotenv_path = (os.getenv("REACHY_MINI_DOTENV_PATH") or "").strip()
    if explicit_dotenv_path:
        if not _load_dotenv_file_values(explicit_dotenv_path):
            logger.warning("REACHY_MINI_DOTENV_PATH does not point to a readable file: %s", explicit_dotenv_path)
    else:
        # Locate .env file (search upward from current working directory)
        dotenv_path = find_dotenv(usecwd=True)

        if dotenv_path:
            _load_dotenv_file_values(dotenv_path)
        else:
            logger.warning("No .env file found")


def _dotenv_value(name: str, default: str | None = None) -> str | None:
    """Return a value from the loaded .env file only."""
    value = _dotenv_values.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _clean_url_value(name: str, value: str | None) -> str | None:
    """Normalize URL values that are often pasted from rendered Markdown."""
    if value is None:
        return None

    raw_candidate = value.strip()
    if raw_candidate.lower() in _PLACEHOLDER_VALUES:
        return "<set-me>"

    candidate = raw_candidate.strip("<>")
    markdown_match = _MARKDOWN_URL_RE.match(candidate)
    if markdown_match is not None:
        url = markdown_match.group("url").strip()
        logger.warning("%s looked like a Markdown link; using URL target %s", name, url)
        return url

    return candidate if candidate else None


def _has_config_value(value: str | None) -> bool:
    """Return whether a required config value is set to something usable."""
    if value is None:
        return False
    candidate = str(value).strip()
    return bool(candidate) and candidate.lower() not in _PLACEHOLDER_VALUES


def is_configured_value(value: str | None) -> bool:
    """Return whether a config value is set to something usable."""
    return _has_config_value(value)


def _configured_value(value: str | None, default: str | None = None) -> str | None:
    """Return a stripped configured value, treating placeholders as missing."""
    if not _has_config_value(value):
        return default
    return str(value).strip()


def _process_env_value(name: str, default: str | None = None) -> str | None:
    """Return a configured value from the process environment."""
    original_value = _configured_value(_ORIGINAL_PROCESS_ENV.get(name))
    if original_value is not None:
        return original_value

    current_value = _configured_value(os.environ.get(name))
    if current_value is not None:
        dotenv_value = _configured_value(_dotenv_values.get(name))
        if name not in _dotenv_loaded_keys or current_value != dotenv_value:
            return current_value

    return default


def _dotenv_url(name: str, default: str | None = None) -> str | None:
    """Return a URL value from the loaded .env file only."""
    return _clean_url_value(name, _dotenv_value(name, default))


def _dotenv_float(name: str, default: float) -> float:
    """Return a float from the loaded .env file only."""
    value = _dotenv_value(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float value for %s=%r, using default=%s", name, value, default)
        return default


def _mapping_value(values: Mapping[str, str | None], name: str, default: str | None = None) -> str | None:
    """Return a stripped value from a dotenv mapping."""
    value = values.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _mapping_url(values: Mapping[str, str | None], name: str, default: str | None = None) -> str | None:
    """Return a URL value from a dotenv mapping."""
    return _clean_url_value(name, _mapping_value(values, name, default))


def _mapping_float(values: Mapping[str, str | None], name: str, default: float) -> float:
    """Return a float from a dotenv mapping."""
    value = _mapping_value(values, name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float value for %s=%r, using default=%s", name, value, default)
        return default


def _normalize_backend_provider(value: str | None) -> str:
    """Normalize the configured conversation backend provider."""
    return (value or "").strip().lower()


def _normalize_hf_connection_mode(value: str | None) -> str:
    """Normalize the Hugging Face realtime connection mode."""
    candidate = (value or HF_REALTIME_CONNECTION_DEPLOYED).strip().lower()
    if candidate in {HF_REALTIME_CONNECTION_DEPLOYED, HF_REALTIME_CONNECTION_LOCAL}:
        return candidate
    logger.warning("Invalid HF_REALTIME_CONNECTION_MODE=%r; using deployed.", value)
    return HF_REALTIME_CONNECTION_DEPLOYED


@dataclass(frozen=True)
class HFRealtimeURLParts:
    """Parsed Hugging Face realtime URL pieces for OpenAI-compatible clients."""

    base_url: str
    websocket_base_url: str
    connect_query: dict[str, str]
    has_realtime_path: bool


def parse_hf_realtime_url(realtime_url: str) -> HFRealtimeURLParts:
    """Parse a Hugging Face realtime websocket/base URL."""
    parsed = urlsplit(realtime_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss", "http", "https"}:
        raise ValueError(
            f"Expected HF realtime URL to start with ws://, wss://, http://, or https://, got: {realtime_url}"
        )

    path = parsed.path.rstrip("/")
    has_realtime_path = path.endswith("/realtime")
    base_path = path[: -len("/realtime")] if has_realtime_path else path
    http_scheme = "https" if scheme in {"wss", "https"} else "http"
    websocket_scheme = "wss" if scheme in {"wss", "https"} else "ws"
    connect_query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "model"}
    return HFRealtimeURLParts(
        base_url=urlunsplit((http_scheme, parsed.netloc, base_path, "", "")),
        websocket_base_url=urlunsplit((websocket_scheme, parsed.netloc, base_path, "", "")),
        connect_query=connect_query,
        has_realtime_path=has_realtime_path,
    )


class Config:
    """Configuration class for the conversation app."""

    BACKEND_PROVIDER = _normalize_backend_provider(_dotenv_value("BACKEND_PROVIDER"))

    REALTIME_TRANSCRIPTION_LANGUAGE = _dotenv_value("REALTIME_TRANSCRIPTION_LANGUAGE", "en")

    OPENAI_REALTIME_API_KEY = (
        _configured_value(_dotenv_value("OPENAI_REALTIME_API_KEY"))
        or _configured_value(_dotenv_value("OPENAI_API_KEY"))
        or _process_env_value("OPENAI_API_KEY")
    )
    OPENAI_REALTIME_BASE_URL = _dotenv_url("OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    OPENAI_REALTIME_MODEL = _dotenv_value("OPENAI_REALTIME_MODEL", "gpt-realtime")
    OPENAI_REALTIME_VOICE = _dotenv_value("OPENAI_REALTIME_VOICE", "cedar")

    HF_REALTIME_CONNECTION_MODE = _normalize_hf_connection_mode(_dotenv_value("HF_REALTIME_CONNECTION_MODE"))
    HF_REALTIME_SESSION_URL = _dotenv_url("HF_REALTIME_SESSION_URL", HF_REALTIME_SESSION_PROXY_URL)
    HF_REALTIME_WS_URL = _dotenv_url("HF_REALTIME_WS_URL")
    HF_REALTIME_MODEL = _dotenv_value("HF_REALTIME_MODEL", "")
    HF_REALTIME_VOICE = _dotenv_value("HF_REALTIME_VOICE", "Aiden")

    CHAT_API_KEY = _dotenv_value("CHAT_API_KEY")
    CHAT_BASE_URL = _dotenv_url("CHAT_BASE_URL")
    CHAT_MODEL_NAME = _dotenv_value("CHAT_MODEL_NAME")

    STT_API_KEY = _dotenv_value("STT_API_KEY", "not-needed")
    STT_BASE_URL = _dotenv_url("STT_BASE_URL")
    STT_MODEL_NAME = _dotenv_value("STT_MODEL_NAME", "whisper-1")
    TTS_API_KEY = _dotenv_value("TTS_API_KEY", "not-needed")
    TTS_BASE_URL = _dotenv_url("TTS_BASE_URL")
    TTS_MODEL_NAME = _dotenv_value("TTS_MODEL_NAME", "gpt-4o-mini-tts")
    TTS_VOICE = _dotenv_value("TTS_VOICE", OPENAI_REALTIME_VOICE)
    MIC_TRANSCRIPTION_RMS_THRESHOLD = _dotenv_float("MIC_TRANSCRIPTION_RMS_THRESHOLD", 500.0)
    MIC_TRANSCRIPTION_MIN_AUDIO_MS = _dotenv_float("MIC_TRANSCRIPTION_MIN_AUDIO_MS", 250.0)
    MIC_TRANSCRIPTION_SILENCE_MS = _dotenv_float("MIC_TRANSCRIPTION_SILENCE_MS", 800.0)
    MIC_TRANSCRIPTION_MAX_AUDIO_MS = _dotenv_float("MIC_TRANSCRIPTION_MAX_AUDIO_MS", 12_000.0)
    HF_HOME = _dotenv_value("HF_HOME", "./cache")
    LOCAL_VISION_MODEL = _dotenv_value("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    HF_TOKEN = _dotenv_value("HF_TOKEN")

    logger.debug(
        "Backend: %s, realtime_model=%s, chat_model=%s, STT=%s, TTS=%s, HF mode=%s, HF_HOME=%s, Vision Model=%s",
        BACKEND_PROVIDER,
        OPENAI_REALTIME_MODEL,
        CHAT_MODEL_NAME,
        STT_MODEL_NAME,
        TTS_MODEL_NAME,
        HF_REALTIME_CONNECTION_MODE,
        HF_HOME,
        LOCAL_VISION_MODEL,
    )
    logger.debug(f"Locked profile: {LOCKED_PROFILE}")
    logger.debug("Dotenv path: %s", _dotenv_path or "<none>")

    def __init__(self) -> None:
        """Initialize the configuration."""
        logger.info("Using locked profile '%s' from %s", LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY)


config = Config()


def apply_config_values(values: Mapping[str, str | None], *, inherit_current: bool = True) -> None:
    """Apply an already-loaded dotenv mapping to the runtime config object."""
    backend_provider_default = config.BACKEND_PROVIDER if inherit_current else ""
    realtime_language_default = config.REALTIME_TRANSCRIPTION_LANGUAGE if inherit_current else "en"
    openai_realtime_base_url_default = (
        config.OPENAI_REALTIME_BASE_URL if inherit_current else "https://api.openai.com/v1"
    )
    openai_realtime_model_default = config.OPENAI_REALTIME_MODEL if inherit_current else "gpt-realtime"
    openai_realtime_voice_default = config.OPENAI_REALTIME_VOICE if inherit_current else "cedar"
    hf_realtime_connection_mode_default = (
        config.HF_REALTIME_CONNECTION_MODE if inherit_current else HF_REALTIME_CONNECTION_DEPLOYED
    )
    hf_realtime_session_url_default = (
        config.HF_REALTIME_SESSION_URL if inherit_current else HF_REALTIME_SESSION_PROXY_URL
    )
    hf_realtime_ws_url_default = config.HF_REALTIME_WS_URL if inherit_current else None
    hf_realtime_model_default = config.HF_REALTIME_MODEL if inherit_current else ""
    hf_realtime_voice_default = config.HF_REALTIME_VOICE if inherit_current else "Aiden"
    hf_token_default = config.HF_TOKEN if inherit_current else None
    chat_api_key_default = config.CHAT_API_KEY if inherit_current else None
    chat_base_url_default = config.CHAT_BASE_URL if inherit_current else None
    chat_model_name_default = config.CHAT_MODEL_NAME if inherit_current else None
    stt_api_key_default = config.STT_API_KEY if inherit_current else "not-needed"
    stt_base_url_default = config.STT_BASE_URL if inherit_current else None
    stt_model_name_default = config.STT_MODEL_NAME if inherit_current else "whisper-1"
    tts_api_key_default = config.TTS_API_KEY if inherit_current else "not-needed"
    tts_base_url_default = config.TTS_BASE_URL if inherit_current else None
    tts_model_name_default = config.TTS_MODEL_NAME if inherit_current else "gpt-4o-mini-tts"
    tts_voice_default = config.TTS_VOICE if inherit_current else openai_realtime_voice_default
    mic_rms_threshold_default = config.MIC_TRANSCRIPTION_RMS_THRESHOLD if inherit_current else 500.0
    mic_min_audio_default = config.MIC_TRANSCRIPTION_MIN_AUDIO_MS if inherit_current else 250.0
    mic_silence_default = config.MIC_TRANSCRIPTION_SILENCE_MS if inherit_current else 800.0
    mic_max_audio_default = config.MIC_TRANSCRIPTION_MAX_AUDIO_MS if inherit_current else 12_000.0
    local_vision_model_default = (
        config.LOCAL_VISION_MODEL if inherit_current else "HuggingFaceTB/SmolVLM2-2.2B-Instruct"
    )
    hf_home_default = config.HF_HOME if inherit_current else "./cache"

    config.BACKEND_PROVIDER = _normalize_backend_provider(
        _mapping_value(values, "BACKEND_PROVIDER", backend_provider_default)
    )

    config.REALTIME_TRANSCRIPTION_LANGUAGE = _mapping_value(
        values,
        "REALTIME_TRANSCRIPTION_LANGUAGE",
        realtime_language_default,
    )

    config.OPENAI_REALTIME_API_KEY = (
        _configured_value(_mapping_value(values, "OPENAI_REALTIME_API_KEY"))
        or _configured_value(_mapping_value(values, "OPENAI_API_KEY"))
        or (_configured_value(config.OPENAI_REALTIME_API_KEY) if inherit_current else None)
        or _process_env_value("OPENAI_API_KEY")
    )
    config.OPENAI_REALTIME_BASE_URL = _mapping_url(
        values,
        "OPENAI_REALTIME_BASE_URL",
        openai_realtime_base_url_default,
    )
    config.OPENAI_REALTIME_MODEL = _mapping_value(
        values,
        "OPENAI_REALTIME_MODEL",
        openai_realtime_model_default,
    )
    config.OPENAI_REALTIME_VOICE = _mapping_value(
        values,
        "OPENAI_REALTIME_VOICE",
        openai_realtime_voice_default,
    )

    config.HF_REALTIME_CONNECTION_MODE = _normalize_hf_connection_mode(
        _mapping_value(values, "HF_REALTIME_CONNECTION_MODE", hf_realtime_connection_mode_default)
    )
    config.HF_REALTIME_SESSION_URL = _mapping_url(
        values,
        "HF_REALTIME_SESSION_URL",
        hf_realtime_session_url_default,
    )
    config.HF_REALTIME_WS_URL = _mapping_url(values, "HF_REALTIME_WS_URL", hf_realtime_ws_url_default)
    config.HF_REALTIME_MODEL = _mapping_value(values, "HF_REALTIME_MODEL", hf_realtime_model_default)
    config.HF_REALTIME_VOICE = _mapping_value(values, "HF_REALTIME_VOICE", hf_realtime_voice_default)
    config.HF_TOKEN = _mapping_value(values, "HF_TOKEN", hf_token_default)

    config.CHAT_API_KEY = _mapping_value(values, "CHAT_API_KEY", chat_api_key_default)
    config.CHAT_BASE_URL = _mapping_url(values, "CHAT_BASE_URL", chat_base_url_default)
    config.CHAT_MODEL_NAME = _mapping_value(values, "CHAT_MODEL_NAME", chat_model_name_default)

    config.STT_API_KEY = _mapping_value(values, "STT_API_KEY", stt_api_key_default)
    config.STT_BASE_URL = _mapping_url(values, "STT_BASE_URL", stt_base_url_default)
    config.STT_MODEL_NAME = _mapping_value(values, "STT_MODEL_NAME", stt_model_name_default)

    config.TTS_API_KEY = _mapping_value(values, "TTS_API_KEY", tts_api_key_default)
    config.TTS_BASE_URL = _mapping_url(values, "TTS_BASE_URL", tts_base_url_default)
    config.TTS_MODEL_NAME = _mapping_value(values, "TTS_MODEL_NAME", tts_model_name_default)
    config.TTS_VOICE = _mapping_value(values, "TTS_VOICE", tts_voice_default)

    config.MIC_TRANSCRIPTION_RMS_THRESHOLD = _mapping_float(
        values,
        "MIC_TRANSCRIPTION_RMS_THRESHOLD",
        mic_rms_threshold_default,
    )
    config.MIC_TRANSCRIPTION_MIN_AUDIO_MS = _mapping_float(
        values,
        "MIC_TRANSCRIPTION_MIN_AUDIO_MS",
        mic_min_audio_default,
    )
    config.MIC_TRANSCRIPTION_SILENCE_MS = _mapping_float(
        values,
        "MIC_TRANSCRIPTION_SILENCE_MS",
        mic_silence_default,
    )
    config.MIC_TRANSCRIPTION_MAX_AUDIO_MS = _mapping_float(
        values,
        "MIC_TRANSCRIPTION_MAX_AUDIO_MS",
        mic_max_audio_default,
    )

    config.LOCAL_VISION_MODEL = (
        _mapping_value(values, "LOCAL_VISION_MODEL", local_vision_model_default) or local_vision_model_default
    )
    config.HF_HOME = _mapping_value(values, "HF_HOME", hf_home_default) or hf_home_default


def load_dotenv_file(env_path: str | Path) -> bool:
    """Load a specific dotenv file and apply it to the runtime config."""
    if _skip_dotenv:
        logger.info("Skipping explicit .env loading because REACHY_MINI_SKIP_DOTENV is set")
        return False

    if not _load_dotenv_file_values(env_path):
        return False

    apply_config_values(_dotenv_values, inherit_current=False)
    return True


def loaded_dotenv_path() -> str | None:
    """Return the dotenv path loaded for this process, if any."""
    return _dotenv_path or None


def loaded_dotenv_keys() -> set[str]:
    """Return the dotenv keys loaded for this process."""
    return set(_dotenv_values)


def openai_realtime_api_key() -> str | None:
    """Return the OpenAI Realtime key, falling back to the standard OpenAI key."""
    return _configured_value(config.OPENAI_REALTIME_API_KEY) or _process_env_value("OPENAI_API_KEY")
