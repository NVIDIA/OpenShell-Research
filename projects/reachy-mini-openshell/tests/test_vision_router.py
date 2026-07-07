"""Tests for allowlist-enforced camera model routing."""

from typing import Any

import pytest

from reachy_mini_conversation_app.vision_router import VisionRouter, VisionModelNotAllowed


class _FakeUsage:
    def model_dump(self, mode: str = "python") -> dict[str, int]:
        assert mode in {"python", "json"}
        return {"input_tokens": 42, "output_tokens": 12, "total_tokens": 54}


class _FakeResponse:
    id = "resp_vision_123"
    output_text = "The person is sitting at a desk."
    usage = _FakeUsage()


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse()


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


@pytest.mark.asyncio
async def test_router_uses_requested_allowed_model_and_returns_no_image() -> None:
    """An approved request should upload once and return only text plus routing metadata."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini", "gpt-5.5"),
    )

    analysis = await router.analyze_jpeg(
        image_base64="jpeg-base64-data",
        question="What is the person doing?",
        requested_model="gpt-5.5",
    )
    result = analysis.as_tool_result()

    assert client.responses.calls[0]["model"] == "gpt-5.5"
    assert client.responses.calls[0]["input"][0]["content"] == [
        {"type": "input_text", "text": "What is the person doing?"},
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,jpeg-base64-data",
        },
    ]
    assert result["selected_model"] == "gpt-5.5"
    assert result["requested_model"] == "gpt-5.5"
    assert result["image_description"] == "The person is sitting at a desk."
    assert result["usage"]["total_tokens"] == 54
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_router_uses_default_model_when_none_requested() -> None:
    """A camera request without a model should use VISION_DEFAULT_MODEL."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini", "gpt-5.5"),
    )

    analysis = await router.analyze_jpeg(
        image_base64="jpeg-base64-data",
        question="Describe this image.",
        requested_model=None,
    )

    assert client.responses.calls[0]["model"] == "gpt-5.4-mini"
    assert analysis.requested_model is None
    assert analysis.selected_model == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_router_rejects_disallowed_model_before_upload() -> None:
    """A disallowed model must fail before the client receives the image."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini", "gpt-5.5"),
    )

    with pytest.raises(VisionModelNotAllowed, match="not approved"):
        await router.analyze_jpeg(
            image_base64="must-not-upload",
            question="Describe this image.",
            requested_model="unapproved-model",
        )

    assert client.responses.calls == []


def test_router_requires_default_model_to_be_allowed() -> None:
    """An invalid routing policy should fail during startup."""
    with pytest.raises(ValueError, match="must also appear"):
        VisionRouter(
            client=_FakeClient(),
            default_model="gpt-5.5",
            allowed_models=("gpt-5.4-mini",),
        )
