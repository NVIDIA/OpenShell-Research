from typing import Any

import pytest

import reachy_mini_conversation_app.config as config_mod
import reachy_mini_conversation_app.realtime_backends as realtime_mod


@pytest.mark.asyncio
async def test_build_openai_realtime_client_uses_configured_base_url(monkeypatch: Any) -> None:
    """OpenAI Realtime client construction should be isolated from stream handling."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    client_kwargs: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            client_kwargs.update(kwargs)

    bundle = await realtime_mod.build_realtime_client("openai-key", client_factory=FakeClient)

    assert isinstance(bundle.client, FakeClient)
    assert bundle.connect_query == {}
    assert bundle.endpoint_url is None
    assert client_kwargs == {
        "api_key": "openai-key",
        "base_url": "https://api.openai.com/v1",
    }


@pytest.mark.asyncio
async def test_resolve_deployed_hf_realtime_url_uses_session_broker(monkeypatch: Any) -> None:
    """The deployed HF path should allocate a session URL before connecting."""
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_CONNECTION_MODE", config_mod.HF_REALTIME_CONNECTION_DEPLOYED)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_SESSION_URL", "https://hf-broker.test/session")
    monkeypatch.setattr(config_mod.config, "HF_TOKEN", "hf-token")
    init_kwargs: dict[str, Any] = {}
    post_calls: list[dict[str, Any]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "session_id": "session_123",
                "connect_url": "wss://hf-runtime.test/v1/realtime?session_id=session_123&model=ignored",
            }

    class FakeHTTPClient:
        def __init__(self, **kwargs: Any) -> None:
            init_kwargs.update(kwargs)

        async def __aenter__(self) -> "FakeHTTPClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
            post_calls.append({"url": url, "headers": headers})
            return FakeResponse()

    url = await realtime_mod.resolve_hf_realtime_url(http_client_factory=FakeHTTPClient)

    assert url == "wss://hf-runtime.test/v1/realtime?session_id=session_123&model=ignored"
    assert init_kwargs == {"timeout": 10.0}
    assert post_calls == [
        {
            "url": "https://hf-broker.test/session",
            "headers": {"Authorization": "Bearer hf-token"},
        }
    ]


def test_build_realtime_session_config_sets_hf_pcm_rates_to_none() -> None:
    """HF realtime expects PCM format without fixed sample-rate metadata."""
    session = realtime_mod.build_realtime_session_config(
        backend_provider=config_mod.BACKEND_HF_REALTIME,
        input_sample_rate=16000,
        output_sample_rate=16000,
        instructions="Be Reachy.",
        voice="Aiden",
        tools=[{"type": "function", "name": "wave"}],
        transcription_language="en",
    )

    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": None}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": None}
    assert session["audio"]["output"]["voice"] == "Aiden"
    assert session["tools"] == [{"type": "function", "name": "wave"}]
