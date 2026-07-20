"""Tests for single-model camera and scene-scan routing."""

from typing import Any

import pytest

from reachy_mini_conversation_app.vision_router import VisionRouter


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
async def test_router_sends_one_camera_frame_to_the_only_approved_model() -> None:
    """One camera frame should upload once and return no source image bytes."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini",),
    )

    analysis = await router.analyze_images(
        images_base64=["jpeg-base64-data"],
        question="Use gpt-5.5 to tell me what the person is doing.",
    )
    result = analysis.as_tool_result()

    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["model"] == "gpt-5.4-mini"
    assert client.responses.calls[0]["input"][0]["content"] == [
        {"type": "input_text", "text": "Use gpt-5.5 to tell me what the person is doing."},
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,jpeg-base64-data",
        },
    ]
    assert result["selected_model"] == "gpt-5.4-mini"
    assert result["image_description"] == "The person is sitting at a desk."
    assert result["usage"]["total_tokens"] == 54
    assert "b64_im" not in result
    assert "requested_model" not in result
    assert "jpeg-base64-data" not in str(result)


@pytest.mark.asyncio
async def test_router_sends_nine_ordered_frames_in_one_response_request() -> None:
    """A scene scan should become one request with frames preserved in chronological order."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini",),
    )
    images = [f"jpeg-{index}" for index in range(9)]
    timestamps = [float(index) for index in range(9)]

    analysis = await router.analyze_images(
        images_base64=images,
        question="What did Reachy see?",
        frame_timestamps=timestamps,
    )

    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == "gpt-5.4-mini"
    content = request["input"][0]["content"]
    assert "deduplicate" in content[0]["text"]
    assert str(timestamps) in content[0]["text"]
    assert [item["image_url"] for item in content[1:]] == [f"data:image/jpeg;base64,{image}" for image in images]
    assert analysis.selected_model == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_router_rejects_more_than_nine_images_before_upload() -> None:
    """An oversized frame set must fail before the client receives any images."""
    client = _FakeClient()
    router = VisionRouter(
        client=client,
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini",),
    )

    with pytest.raises(ValueError, match="between 1 and 9"):
        await router.analyze_images(
            images_base64=["must-not-upload"] * 10,
            question="Describe this image.",
        )

    assert client.responses.calls == []


def test_router_requires_exactly_one_allowed_model() -> None:
    """Multiple allowed models would reintroduce a user-selectable routing path."""
    with pytest.raises(ValueError, match="exactly one model"):
        VisionRouter(
            client=_FakeClient(),
            default_model="gpt-5.4-mini",
            allowed_models=("gpt-5.4-mini", "gpt-5.5"),
        )


def test_router_requires_default_model_to_be_allowed() -> None:
    """An invalid routing policy should fail during startup."""
    with pytest.raises(ValueError, match="must equal the only"):
        VisionRouter(
            client=_FakeClient(),
            default_model="gpt-5.5",
            allowed_models=("gpt-5.4-mini",),
        )
