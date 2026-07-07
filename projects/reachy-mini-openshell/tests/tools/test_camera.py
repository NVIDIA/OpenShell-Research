"""Tests for the camera tool's routed vision behavior."""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini_conversation_app.tools.camera import Camera
from reachy_mini_conversation_app.vision_router import VisionAnalysis
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeCameraWorker:
    def get_latest_frame(self) -> np.ndarray[Any, Any]:
        return np.zeros((8, 8, 3), dtype=np.uint8)


class _FakeVisionRouter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def analyze_images(self, **kwargs: Any) -> VisionAnalysis:
        self.calls.append(kwargs)
        return VisionAnalysis(
            description="The person is waving.",
            selected_model="gpt-5.4-mini",
            response_id="resp_camera",
            usage={"total_tokens": 20},
        )


@pytest.mark.asyncio
async def test_camera_sends_one_image_without_a_model_selection() -> None:
    """The tool should send one frame while leaving model selection to the router."""
    router = _FakeVisionRouter()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
        vision_router=router,
    )

    result = await Camera()(deps, question="What am I doing?")

    assert len(router.calls[0]["images_base64"]) == 1
    assert router.calls[0]["question"] == "What am I doing?"
    assert "requested_model" not in router.calls[0]
    assert result["status"] == "image_analyzed"
    assert result["selected_model"] == "gpt-5.4-mini"
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_camera_schema_and_runtime_ignore_user_model_selection() -> None:
    """Neither the public schema nor direct kwargs should provide a model override."""
    router = _FakeVisionRouter()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
        vision_router=router,
    )

    result = await Camera()(deps, question="Use gpt-5.5.", requested_model="gpt-5.5")

    properties = Camera.parameters_schema["properties"]
    assert isinstance(properties, dict)
    assert "requested_model" not in properties
    assert Camera.parameters_schema["additionalProperties"] is False
    assert "requested_model" not in router.calls[0]
    assert result["selected_model"] == "gpt-5.4-mini"
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_camera_without_a_router_returns_raw_media_for_the_internal_processor() -> None:
    """The unprocessed path should return an internal image for MediaResultProcessor."""
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
    )

    result = await Camera()(deps, question="What am I doing?")

    assert result["question"] == "What am I doing?"
    assert isinstance(result["b64_im"], str)
    assert result["b64_im"]
