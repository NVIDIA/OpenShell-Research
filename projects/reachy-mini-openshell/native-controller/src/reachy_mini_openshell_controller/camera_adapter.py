"""Narrow trusted HTTP adapter for one-frame Reachy camera capture."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from PIL import Image

JPEG_MEDIA_TYPE = "image/jpeg"
DEFAULT_MAX_JPEG_BYTES = 2 * 1024 * 1024
DEFAULT_MIN_CAPTURE_INTERVAL_SECONDS = 1.0
DEFAULT_FRAME_WAIT_SECONDS = 2.0
DEFAULT_JPEG_QUALITY = 85


def _encode_bgr_frame_as_jpeg(frame: Any) -> bytes:
    """Encode one Reachy SDK BGR uint8 frame as a bounded-quality JPEG."""
    if (
        not isinstance(frame, np.ndarray)
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape[2] != 3
        or frame.size == 0
    ):
        raise ValueError("Reachy returned an invalid BGR camera frame")

    rgb_frame = np.ascontiguousarray(frame[:, :, ::-1])
    output = BytesIO()
    Image.fromarray(rgb_frame).save(
        output,
        format="JPEG",
        quality=DEFAULT_JPEG_QUALITY,
        optimize=True,
    )
    return output.getvalue()


class TrustedCameraAdapter:
    """Expose only one bounded snapshot operation from the trusted native app."""

    def __init__(
        self,
        media_provider: Callable[[], Any | None],
        *,
        max_jpeg_bytes: int = DEFAULT_MAX_JPEG_BYTES,
        min_capture_interval_seconds: float = DEFAULT_MIN_CAPTURE_INTERVAL_SECONDS,
        frame_wait_seconds: float = DEFAULT_FRAME_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        frame_encoder: Callable[[Any], bytes] = _encode_bgr_frame_as_jpeg,
    ) -> None:
        """Configure bounded capture without accepting model-controlled device options."""
        if max_jpeg_bytes <= 0:
            raise ValueError("max_jpeg_bytes must be positive")
        if min_capture_interval_seconds < 0:
            raise ValueError("min_capture_interval_seconds must be non-negative")
        if frame_wait_seconds <= 0:
            raise ValueError("frame_wait_seconds must be positive")

        self._media_provider = media_provider
        self._max_jpeg_bytes = max_jpeg_bytes
        self._min_capture_interval_seconds = min_capture_interval_seconds
        self._frame_wait_seconds = frame_wait_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._frame_encoder = frame_encoder
        self._capture_lock = threading.Lock()
        self._last_capture_at: float | None = None

    def register(self, application: FastAPI) -> None:
        """Register the fixed capture route on the Reachy App settings server."""

        @application.post("/camera/capture", response_class=Response)
        def capture() -> Response:
            return self.capture()

    def capture(self) -> Response:
        """Capture one JPEG or return a bounded, non-sensitive error."""
        if not self._capture_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="A camera capture is already in progress")

        try:
            now = self._clock()
            if self._last_capture_at is not None:
                retry_after = self._min_capture_interval_seconds - (now - self._last_capture_at)
                if retry_after > 0:
                    raise HTTPException(
                        status_code=429,
                        detail="Camera capture rate limit exceeded",
                        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
                    )

            media = self._media_provider()
            if media is None:
                raise HTTPException(status_code=503, detail="Reachy camera is not ready")

            deadline = now + self._frame_wait_seconds
            frame: Any = None
            while self._clock() < deadline:
                frame = media.get_frame()
                if frame is not None:
                    break
                self._sleeper(0.05)

            if frame is None:
                raise HTTPException(status_code=503, detail="No camera frame is available")
            try:
                jpeg = self._frame_encoder(frame)
            except (TypeError, ValueError, OSError) as exc:
                raise HTTPException(status_code=502, detail="Reachy returned an invalid camera frame") from exc
            if not isinstance(jpeg, bytes) or not jpeg:
                raise HTTPException(status_code=502, detail="Reachy returned an invalid JPEG frame")
            if len(jpeg) > self._max_jpeg_bytes:
                raise HTTPException(status_code=413, detail="Camera frame exceeds the configured size limit")
            if not jpeg.startswith(b"\xff\xd8"):
                raise HTTPException(status_code=502, detail="Reachy returned an invalid JPEG frame")

            self._last_capture_at = self._clock()
            return Response(
                content=jpeg,
                media_type=JPEG_MEDIA_TYPE,
                headers={
                    "Cache-Control": "no-store",
                    "Content-Disposition": 'inline; filename="reachy-capture.jpg"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        finally:
            self._capture_lock.release()
