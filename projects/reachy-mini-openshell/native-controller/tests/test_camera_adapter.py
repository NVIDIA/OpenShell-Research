"""Tests for the trusted native camera adapter."""

# ruff: noqa: D101, D102, D103, D107

from typing import Any

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_mini_openshell_controller.camera_adapter import TrustedCameraAdapter

JPEG = b"\xff\xd8test-jpeg\xff\xd9"
FRAME = np.zeros((8, 12, 3), dtype=np.uint8)


class Media:
    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames
        self.calls = 0

    def get_frame(self) -> Any:
        self.calls += 1
        return self.frames.pop(0) if self.frames else None


def app_for(adapter: TrustedCameraAdapter) -> FastAPI:
    application = FastAPI()
    adapter.register(application)
    return application


def test_capture_returns_one_uncached_bounded_jpeg() -> None:
    media = Media([FRAME])
    adapter = TrustedCameraAdapter(lambda: media, min_capture_interval_seconds=0)

    with TestClient(app_for(adapter)) as client:
        response = client.post("/camera/capture")

    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")
    assert response.content.endswith(b"\xff\xd9")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert media.calls == 1


def test_capture_exposes_no_get_route_or_model_controlled_options() -> None:
    adapter = TrustedCameraAdapter(lambda: Media([FRAME, FRAME]), min_capture_interval_seconds=0)

    with TestClient(app_for(adapter)) as client:
        get_response = client.get("/camera/capture")
        body_response = client.post("/camera/capture", json={"filename": "/tmp/anything"})

    assert get_response.status_code == 405
    assert body_response.status_code == 200
    assert body_response.content.startswith(b"\xff\xd8")


def test_capture_rejects_unavailable_oversized_and_invalid_frames() -> None:
    cases = [
        (lambda: None, {}, 503),
        (lambda: Media([FRAME]), {"max_jpeg_bytes": 4}, 413),
        (lambda: Media([object()]), {}, 502),
    ]

    for provider, options, expected_status in cases:
        adapter = TrustedCameraAdapter(
            provider,
            min_capture_interval_seconds=0,
            frame_wait_seconds=0.01,
            **options,
        )
        with TestClient(app_for(adapter)) as client:
            response = client.post("/camera/capture")
        assert response.status_code == expected_status


def test_capture_rate_limits_successive_snapshots() -> None:
    times = iter([10.0, 10.0, 10.0, 10.2])
    media = Media([FRAME, FRAME])
    adapter = TrustedCameraAdapter(media_provider=lambda: media, clock=lambda: next(times))

    with TestClient(app_for(adapter)) as client:
        first = client.post("/camera/capture")
        second = client.post("/camera/capture")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
