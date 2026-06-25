import sys
import asyncio
from collections.abc import Callable

import pytest

from reachy_mini_conversation_app.riva_asr import RivaAsrConfig, RivaStreamingTranscriber


def test_riva_config_accepts_http_endpoint_url(monkeypatch):
    """Riva endpoint URLs should be normalized to the host:port form expected by the client."""
    monkeypatch.setenv("RIVA_SERVER_URI", "http://192.168.1.57:9000")
    monkeypatch.delenv("RIVA_USE_SSL", raising=False)

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "192.168.1.57:9000"
    assert config.use_ssl is False


def test_riva_config_infers_ssl_from_https_endpoint_url(monkeypatch):
    """HTTPS Riva endpoint URLs should enable TLS unless explicitly overridden."""
    monkeypatch.setenv("RIVA_SERVER_URI", "https://riva.example.test:443")
    monkeypatch.delenv("RIVA_USE_SSL", raising=False)

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "riva.example.test:443"
    assert config.use_ssl is True


def test_riva_use_ssl_env_overrides_endpoint_url_scheme(monkeypatch):
    """RIVA_USE_SSL should keep explicit operator configuration authoritative."""
    monkeypatch.setenv("RIVA_SERVER_URI", "https://riva.example.test:443")
    monkeypatch.setenv("RIVA_USE_SSL", "false")

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "riva.example.test:443"
    assert config.use_ssl is False


def test_blank_riva_use_ssl_env_keeps_endpoint_url_inference(monkeypatch):
    """Blank RIVA_USE_SSL values should not disable https:// inference."""
    monkeypatch.setenv("RIVA_SERVER_URI", "https://riva.example.test:443")
    monkeypatch.setenv("RIVA_USE_SSL", "")

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "riva.example.test:443"
    assert config.use_ssl is True


@pytest.mark.asyncio
async def test_riva_missing_dependency_reports_error_without_send_audio_raise(monkeypatch):
    """Missing nvidia-riva-client should report through callbacks without crashing send_audio."""
    monkeypatch.setitem(sys.modules, "riva", None)
    delivered: list[str] = []

    async def on_final(transcript: str) -> None:
        delivered.append(transcript)

    transcriber = RivaStreamingTranscriber(
        config=RivaAsrConfig(
            server_uri="localhost:50051",
            language_code="en-US",
            model="",
            use_ssl=False,
            ssl_cert=None,
            authorization=None,
            metadata=(),
            interim_results=True,
            automatic_punctuation=True,
        ),
        on_final_transcript=on_final,
    )

    await transcriber.start(16000)
    await asyncio.wait_for(_wait_until(lambda: bool(delivered)), timeout=1)
    await transcriber.send_audio(b"\x00\x00")

    assert "Riva STT mode requires the optional dependency" in delivered[0]


@pytest.mark.asyncio
async def test_repeated_final_transcripts_are_delivered() -> None:
    """Separate identical final utterances should both be routed to the text path."""
    delivered: list[str] = []

    async def on_final(transcript: str) -> None:
        delivered.append(transcript)

    transcriber = RivaStreamingTranscriber(
        config=RivaAsrConfig(
            server_uri="localhost:50051",
            language_code="en-US",
            model="",
            use_ssl=False,
            ssl_cert=None,
            authorization=None,
            metadata=(),
            interim_results=True,
            automatic_punctuation=True,
        ),
        on_final_transcript=on_final,
    )
    transcriber._loop = asyncio.get_running_loop()

    transcriber._schedule_final("yes")
    transcriber._schedule_final("yes")
    await asyncio.wait_for(_wait_until(lambda: len(delivered) == 2), timeout=1)

    assert delivered == ["yes", "yes"]


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until predicate returns true."""
    while not predicate():
        await asyncio.sleep(0)
