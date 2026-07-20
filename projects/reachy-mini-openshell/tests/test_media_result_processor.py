"""Tests for the optional local raw-media boundary."""

import base64
from typing import Any
from pathlib import Path

import cv2
import numpy as np
import pytest

from reachy_mini_conversation_app.vision_router import VisionAnalysis
from reachy_mini_conversation_app.media_result_processor import (
    MediaResultProcessor,
    contains_raw_media,
    assert_no_raw_media,
)


def _jpeg_base64(value: int = 0) -> str:
    frame = np.full((4, 4, 3), value, dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", frame)
    assert encoded
    return base64.b64encode(buffer.tobytes()).decode("ascii")


class _FakeVisionRouter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def analyze_images(self, **kwargs: Any) -> VisionAnalysis:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("vision unavailable")
        return VisionAnalysis(
            description="A person is sitting at a desk.",
            selected_model="approved-vision-model",
            response_id="resp_vision",
            usage={"total_tokens": 42},
        )


def _processor(tmp_path: Path, router: Any) -> MediaResultProcessor:
    return MediaResultProcessor(
        vision_router=router,
        capture_directory=tmp_path,
        require_routed_vision=True,
    )


@pytest.mark.asyncio
async def test_camera_is_analyzed_before_model_payload_is_created(tmp_path: Path) -> None:
    """A local camera result should expose text to the model and pixels only to the UI."""
    router = _FakeVisionRouter()
    processor = _processor(tmp_path, router)
    image = _jpeg_base64(25)

    processed = await processor.process(
        "camera",
        {
            "status": "image_captured",
            "question": "What am I doing?",
            "b64_im": image,
        },
    )

    assert router.calls == [
        {
            "images_base64": [image],
            "question": "What am I doing?",
            "frame_timestamps": None,
        }
    ]
    assert processed.model_payload == {
        "status": "image_analyzed",
        "question": "What am I doing?",
        "image_description": "A person is sitting at a desk.",
        "selected_model": "approved-vision-model",
        "response_id": "resp_vision",
        "usage": {"total_tokens": 42},
    }
    assert processed.preview_image is not None
    assert processed.preview_image.shape == (4, 4, 3)
    assert not contains_raw_media(processed.model_payload)


@pytest.mark.asyncio
async def test_scene_scan_routes_ordered_frames_and_keeps_local_video(tmp_path: Path) -> None:
    """A local scan should retain its validated MP4 as a UI-only artifact."""
    router = _FakeVisionRouter()
    processor = _processor(tmp_path, router)
    images = [_jpeg_base64(value) for value in range(3)]
    timestamps = [0.0, 1.0, 2.0]
    video_path = tmp_path / "scan.mp4"
    video_path.write_bytes(b"fake-mp4")

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_complete",
            "question": "What did you see?",
            "video_path": str(video_path),
            "frame_timestamps_seconds": timestamps,
            "frames_selected": 3,
            "b64_images": images,
        },
    )

    assert router.calls == [
        {
            "images_base64": images,
            "question": "What did you see?",
            "frame_timestamps": timestamps,
        }
    ]
    assert processed.model_payload["status"] == "scene_analyzed"
    assert processed.model_payload["recording_status"] == "available"
    assert processed.model_payload["image_description"] == "A person is sitting at a desk."
    assert "b64_images" not in processed.model_payload
    assert "video_path" not in processed.model_payload
    assert processed.video_path == video_path


@pytest.mark.asyncio
async def test_scene_scan_preserves_interruption_metadata(tmp_path: Path) -> None:
    """Vision analysis must not erase the physical scan's incomplete status."""
    router = _FakeVisionRouter()
    processor = _processor(tmp_path, router)
    video_path = tmp_path / "partial.mp4"
    video_path.write_bytes(b"partial")

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_incomplete",
            "scan_status": "scene_scan_incomplete",
            "scan_warning": "Reachy lost its control connection during the sweep",
            "returned_to_front": True,
            "front_verified": True,
            "question": "What did you see?",
            "video_path": str(video_path),
            "frame_timestamps_seconds": [0.0],
            "b64_images": [_jpeg_base64()],
        },
    )

    assert processed.model_payload["status"] == "scene_analyzed"
    assert processed.model_payload["scan_status"] == "scene_scan_incomplete"
    assert processed.model_payload["returned_to_front"] is True
    assert processed.model_payload["front_verified"] is True
    assert "lost its control connection" in processed.model_payload["scan_warning"]


@pytest.mark.asyncio
async def test_scene_scan_preserves_analysis_when_local_preview_is_invalid(tmp_path: Path) -> None:
    """An invalid local recording must not discard successful vision output."""
    router = _FakeVisionRouter()
    processor = _processor(tmp_path, router)

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_complete",
            "question": "What did you see?",
            "video_path": str(tmp_path / "missing.mp4"),
            "frame_timestamps_seconds": [0.0],
            "b64_images": [_jpeg_base64()],
        },
    )

    assert processed.model_payload["status"] == "scene_analyzed"
    assert processed.model_payload["recording_status"] == "preview_unavailable"
    assert processed.video_path is None
    assert not contains_raw_media(processed.model_payload)


@pytest.mark.asyncio
async def test_vision_failure_discards_camera_bytes(tmp_path: Path) -> None:
    """A failed local vision route must discard raw camera bytes."""
    processor = _processor(tmp_path, _FakeVisionRouter(fail=True))

    processed = await processor.process(
        "camera",
        {"question": "Describe this.", "b64_im": _jpeg_base64()},
    )

    assert processed.model_payload == {
        "status": "vision_error",
        "tool": "camera",
        "error": "Approved vision analysis failed; raw media was discarded",
    }
    assert processed.preview_image is None
    assert not contains_raw_media(processed.model_payload)


@pytest.mark.asyncio
async def test_scene_scan_rejects_more_than_nine_frames_before_upload(tmp_path: Path) -> None:
    """Oversized local frame sets should fail before model upload."""
    router = _FakeVisionRouter()
    processor = _processor(tmp_path, router)

    processed = await processor.process(
        "scan_scene",
        {
            "question": "What did you see?",
            "frame_timestamps_seconds": list(range(10)),
            "b64_images": [_jpeg_base64()] * 10,
        },
    )

    assert processed.model_payload["status"] == "media_security_error"
    assert router.calls == []


def test_strict_routing_requires_a_vision_router(tmp_path: Path) -> None:
    """Explicit local strict mode should require a configured vision router."""
    with pytest.raises(ValueError, match="requires a configured VisionRouter"):
        MediaResultProcessor(
            vision_router=None,
            capture_directory=tmp_path,
            require_routed_vision=True,
        )


def test_recursive_raw_media_guard_rejects_data_urls() -> None:
    """The serialization guard should detect nested raw image data URLs."""
    payload = {"nested": [{"image_url": "data:image/jpeg;base64,secret"}]}

    assert contains_raw_media(payload)
    with pytest.raises(RuntimeError, match="Raw media reached"):
        assert_no_raw_media(payload)
