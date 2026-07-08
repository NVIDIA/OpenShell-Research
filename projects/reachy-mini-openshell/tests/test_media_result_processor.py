"""Tests for the raw-media boundary in front of conversation models."""

import base64
from typing import Any
from pathlib import Path
from collections.abc import AsyncIterator

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


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "video/mp4") -> None:
        self.body = body
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(body)),
        }

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.body


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse, calls: list[dict[str, Any]], **kwargs: Any) -> None:
        self.response = response
        self.calls = calls
        self.calls.append({"client": kwargs})

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakeResponse:
        self.calls.append({"method": method, "url": url})
        return self.response


def _processor(
    tmp_path: Path,
    router: Any,
    *,
    response: _FakeResponse | None = None,
) -> tuple[MediaResultProcessor, list[dict[str, Any]]]:
    http_calls: list[dict[str, Any]] = []
    fake_response = response or _FakeResponse(b"fake-mp4")

    def client_factory(**kwargs: Any) -> _FakeHttpClient:
        return _FakeHttpClient(fake_response, http_calls, **kwargs)

    return (
        MediaResultProcessor(
            vision_router=router,
            mcp_token="secret-token",
            capture_directory=tmp_path,
            require_routed_vision=True,
            mcp_url="http://127.0.0.1:8766/mcp",
            http_client_factory=client_factory,
        ),
        http_calls,
    )


@pytest.mark.asyncio
async def test_camera_is_analyzed_before_model_payload_is_created(tmp_path: Path) -> None:
    """A camera result should expose text to the model and pixels only to the UI."""
    router = _FakeVisionRouter()
    processor, _ = _processor(tmp_path, router)
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
async def test_scene_scan_routes_nine_ordered_frames_once_and_downloads_video(tmp_path: Path) -> None:
    """A scan should use one vision request and retain its MP4 as a UI artifact."""
    router = _FakeVisionRouter()
    processor, http_calls = _processor(tmp_path, router)
    images = [_jpeg_base64(value) for value in range(9)]
    timestamps = [float(value) for value in range(9)]

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_complete",
            "question": "What did you see?",
            "capture_id": "capture_123",
            "video_url": "http://host.openshell.internal:8766/captures/capture_123.mp4",
            "frame_timestamps_seconds": timestamps,
            "frames_selected": 9,
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
    assert "video_url" not in processed.model_payload
    assert "video_path" not in processed.model_payload
    assert processed.video_path == tmp_path / "capture_123.mp4"
    video_path = processed.video_path
    assert video_path is not None
    assert video_path.read_bytes() == b"fake-mp4"
    assert http_calls[0]["client"]["headers"] == {"Authorization": "Bearer secret-token"}
    assert http_calls[1] == {
        "method": "GET",
        "url": "http://127.0.0.1:8766/captures/capture_123.mp4",
    }


@pytest.mark.asyncio
async def test_scene_scan_preserves_interruption_and_front_recovery_metadata(tmp_path: Path) -> None:
    """Vision analysis must not erase the physical scan's incomplete status."""
    router = _FakeVisionRouter()
    processor, _ = _processor(tmp_path, router)
    image = _jpeg_base64()

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_incomplete",
            "scan_status": "scene_scan_incomplete",
            "scan_warning": "Reachy lost its control connection during the sweep",
            "returned_to_front": True,
            "front_verified": True,
            "question": "What did you see?",
            "capture_id": "capture_partial",
            "video_url": "http://host.openshell.internal:8766/captures/capture_partial.mp4",
            "frame_timestamps_seconds": [0.0],
            "b64_images": [image],
        },
    )

    assert processed.model_payload["status"] == "scene_analyzed"
    assert processed.model_payload["scan_status"] == "scene_scan_incomplete"
    assert processed.model_payload["returned_to_front"] is True
    assert processed.model_payload["front_verified"] is True
    assert "lost its control connection" in processed.model_payload["scan_warning"]
    assert processed.model_payload["image_description"] == "A person is sitting at a desk."


@pytest.mark.asyncio
async def test_scene_scan_preserves_analysis_when_video_preview_fails(tmp_path: Path) -> None:
    """A capture-download failure should not discard successful vision output."""
    router = _FakeVisionRouter()
    processor, _ = _processor(
        tmp_path,
        router,
        response=_FakeResponse(b"not-video", content_type="text/plain"),
    )
    image = _jpeg_base64()

    processed = await processor.process(
        "scan_scene",
        {
            "status": "scene_scan_complete",
            "question": "What did you see?",
            "capture_id": "capture_456",
            "video_url": "http://host.openshell.internal:8766/captures/capture_456.mp4",
            "frame_timestamps_seconds": [0.0],
            "b64_images": [image],
        },
    )

    assert processed.model_payload["status"] == "scene_analyzed"
    assert processed.model_payload["image_description"] == "A person is sitting at a desk."
    assert processed.model_payload["recording_status"] == "preview_unavailable"
    assert "recording preview could not be retrieved" in processed.model_payload["recording_error"]
    assert processed.video_path is None
    assert not contains_raw_media(processed.model_payload)


@pytest.mark.asyncio
async def test_vision_failure_discards_camera_bytes(tmp_path: Path) -> None:
    """A failed approved route must not fall back to the conversation model."""
    processor, _ = _processor(tmp_path, _FakeVisionRouter(fail=True))

    processed = await processor.process(
        "camera",
        {
            "question": "Describe this.",
            "b64_im": _jpeg_base64(),
        },
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
    """Oversized frame sets should fail before any external model upload."""
    router = _FakeVisionRouter()
    processor, _ = _processor(tmp_path, router)

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
    assert not contains_raw_media(processed.model_payload)


def test_strict_routing_requires_a_vision_router(tmp_path: Path) -> None:
    """Strict mode should fail during startup when no approved router exists."""
    with pytest.raises(ValueError, match="requires a configured VisionRouter"):
        MediaResultProcessor(
            vision_router=None,
            mcp_token="token",
            capture_directory=tmp_path,
            require_routed_vision=True,
        )


def test_recursive_raw_media_guard_rejects_data_urls() -> None:
    """The final serialization guard should find raw media at any nesting depth."""
    payload = {
        "nested": [
            {
                "image_url": "data:image/jpeg;base64,secret",
            }
        ]
    }

    assert contains_raw_media(payload)
    with pytest.raises(RuntimeError, match="Raw media reached"):
        assert_no_raw_media(payload)
