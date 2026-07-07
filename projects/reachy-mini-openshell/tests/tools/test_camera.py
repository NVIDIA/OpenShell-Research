"""Tests for the camera tool's routed vision behavior."""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini_conversation_app.tools.camera import Camera
from reachy_mini_conversation_app.vision_router import VisionAnalysis, VisionModelNotAllowed
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeCameraWorker:
    def get_latest_frame(self) -> np.ndarray[Any, Any]:
        return np.zeros((8, 8, 3), dtype=np.uint8)


class _FakeVisionRouter:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls: list[dict[str, Any]] = []

    async def analyze_jpeg(self, **kwargs: Any) -> VisionAnalysis:
        self.calls.append(kwargs)
        if self.reject:
            raise VisionModelNotAllowed("Vision model 'blocked' is not approved. Allowed models: approved.")
        return VisionAnalysis(
            description="The person is waving.",
            requested_model=kwargs["requested_model"],
            selected_model=kwargs["requested_model"] or "approved",
            response_id="resp_camera",
            usage={"total_tokens": 20},
        )


@pytest.mark.asyncio
async def test_camera_passes_exact_requested_model_to_router() -> None:
    """The tool should preserve the model name supplied by the Realtime tool call."""
    router = _FakeVisionRouter()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
        vision_router=router,
    )

    result = await Camera()(deps, question="What am I doing?", requested_model="gpt-5.5")

    assert router.calls[0]["requested_model"] == "gpt-5.5"
    assert router.calls[0]["question"] == "What am I doing?"
    assert result["status"] == "image_analyzed"
    assert result["selected_model"] == "gpt-5.5"
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_camera_surfaces_router_rejection_without_fallback_image() -> None:
    """A rejected route must not fall back to sending the image to the Realtime model."""
    router = _FakeVisionRouter(reject=True)
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
        vision_router=router,
    )

    result = await Camera()(deps, question="What am I doing?", requested_model="blocked")

    assert result["status"] == "model_not_allowed"
    assert "not approved" in result["error"]
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_camera_fails_closed_when_model_requested_without_router() -> None:
    """A named model must not silently fall back to the active conversation model."""
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        camera_worker=_FakeCameraWorker(),
    )

    result = await Camera()(deps, question="What am I doing?", requested_model="gpt-5.5")

    assert "routed camera vision is not configured" in result["error"]
    assert "b64_im" not in result
