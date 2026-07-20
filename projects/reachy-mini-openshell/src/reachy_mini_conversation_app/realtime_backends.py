import logging
from typing import Any
from dataclasses import dataclass
from collections.abc import Callable

import httpx

from reachy_mini_conversation_app.config import (
    BACKEND_HF_REALTIME,
    BACKEND_OPENAI_REALTIME,
    HF_REALTIME_CONNECTION_LOCAL,
    config,
    parse_hf_realtime_url,
)
from reachy_mini_conversation_app.backend_runtime import selected_backend


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RealtimeClientBundle:
    """OpenAI-compatible realtime client plus provider-specific connect metadata."""

    client: Any
    connect_query: dict[str, str]
    endpoint_url: str | None = None


def realtime_context() -> str:
    """Return non-secret Realtime configuration for diagnostics."""
    backend = selected_backend()
    return (
        f"backend={backend.provider!r}, model={backend.realtime_model!r}, "
        f"base_url={config.OPENAI_REALTIME_BASE_URL!r}, hf_mode={config.HF_REALTIME_CONNECTION_MODE!r}"
    )


def provider_realtime_hint() -> str:
    """Return a provider-specific hint for common compatibility failures."""
    base_url = str(config.OPENAI_REALTIME_BASE_URL)
    if "integrate.api.nvidia.com" in base_url or "inference-api.nvidia.com" in base_url:
        return (
            " NVIDIA OpenAI-compatible chat endpoints use Chat Completions; "
            "choose BACKEND_PROVIDER=local_stt for STT + Chat Completions, or configure a Realtime endpoint."
        )
    return ""


async def resolve_hf_realtime_url(
    *,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> str:
    """Resolve the selected Hugging Face realtime websocket URL."""
    if config.HF_REALTIME_CONNECTION_MODE == HF_REALTIME_CONNECTION_LOCAL:
        if not config.HF_REALTIME_WS_URL:
            raise RuntimeError("HF_REALTIME_WS_URL must be set when HF_REALTIME_CONNECTION_MODE=local")
        return config.HF_REALTIME_WS_URL

    if not config.HF_REALTIME_SESSION_URL:
        raise RuntimeError("HF_REALTIME_SESSION_URL is not configured")

    headers = {"Authorization": f"Bearer {config.HF_TOKEN}"} if config.HF_TOKEN else None
    async with http_client_factory(timeout=10.0) as http_client:
        response = await http_client.post(config.HF_REALTIME_SESSION_URL, headers=headers)
        response.raise_for_status()
        payload = response.json()

    connect_url = payload.get("connect_url")
    if not isinstance(connect_url, str) or not connect_url:
        raise RuntimeError(f"HF realtime session response did not contain connect_url: {payload!r}")
    if not parse_hf_realtime_url(connect_url).has_realtime_path:
        raise RuntimeError(f"HF realtime connect_url must end with /realtime: {connect_url}")
    logger.info("Allocated HF realtime session %s", payload.get("session_id") or "<unknown>")
    return connect_url


async def build_realtime_client(
    realtime_api_key: str,
    *,
    client_factory: Callable[..., Any],
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> RealtimeClientBundle:
    """Build the selected OpenAI-compatible realtime client."""
    backend = selected_backend()
    if backend.provider == BACKEND_OPENAI_REALTIME:
        return RealtimeClientBundle(
            client=client_factory(
                api_key=realtime_api_key,
                base_url=config.OPENAI_REALTIME_BASE_URL,
            ),
            connect_query={},
        )

    if backend.provider != BACKEND_HF_REALTIME:
        raise RuntimeError(f"Backend {backend.provider!r} does not use a realtime client")

    realtime_url = await resolve_hf_realtime_url(http_client_factory=http_client_factory)
    parsed = parse_hf_realtime_url(realtime_url)
    logger.info("Using Hugging Face realtime endpoint %s", realtime_url)
    return RealtimeClientBundle(
        client=client_factory(
            api_key=realtime_api_key,
            base_url=parsed.base_url,
            websocket_base_url=parsed.websocket_base_url,
        ),
        connect_query=parsed.connect_query,
        endpoint_url=realtime_url,
    )


def build_realtime_connect_kwargs(connect_query: dict[str, str]) -> dict[str, Any]:
    """Build kwargs for the OpenAI-compatible realtime websocket connect call."""
    connect_kwargs: dict[str, Any] = {}
    backend = selected_backend()
    if backend.realtime_model:
        connect_kwargs["model"] = backend.realtime_model
    if connect_query:
        connect_kwargs["extra_query"] = dict(connect_query)
    return connect_kwargs


def build_realtime_session_config(
    *,
    backend_provider: str,
    input_sample_rate: int,
    output_sample_rate: int,
    instructions: str,
    voice: str,
    tools: list[dict[str, Any]],
    transcription_language: str | None,
) -> dict[str, Any]:
    """Build the session.update payload shared by realtime backends."""
    input_format: dict[str, Any] = {"type": "audio/pcm", "rate": input_sample_rate}
    output_format: dict[str, Any] = {"type": "audio/pcm", "rate": output_sample_rate}
    if backend_provider == BACKEND_HF_REALTIME:
        input_format["rate"] = None
        output_format["rate"] = None

    return {
        "type": "realtime",
        "instructions": instructions,
        "audio": {
            "input": {
                "format": input_format,
                "transcription": {
                    "model": "gpt-4o-transcribe",
                    "language": transcription_language,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "interrupt_response": True,
                },
            },
            "output": {
                "format": output_format,
                "voice": voice,
            },
        },
        "tools": tools,
        "tool_choice": "auto",
    }
