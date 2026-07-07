"""Record a synchronized Reachy sweep and return representative vision frames."""

from __future__ import annotations
import time
import base64
import asyncio
import logging
from typing import Any, Dict
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies
from reachy_mini_conversation_app.profiles._reachy_mini_conversation_app_locked_profile.sweep_look import (
    SWEEP_TOTAL_DURATION_SECONDS,
    SweepLook,
)


logger = logging.getLogger(__name__)

CAPTURE_FPS = 15.0
MAX_ANALYSIS_FRAMES = 9
FRAME_WAIT_TIMEOUT_SECONDS = 3.0
SWEEP_RECORDING_SETTLE_SECONDS = 0.25
JPEG_QUALITY = 85


@dataclass
class _FrameCandidate:
    """Sharpest frame observed in one chronological section of the sweep."""

    sharpness: float
    elapsed_seconds: float
    frame: NDArray[np.uint8]


def _frame_sharpness(frame: NDArray[np.uint8]) -> float:
    """Return a simple focus score used to avoid motion-blurred samples."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _open_video_writer(path: Path, frame: NDArray[np.uint8]) -> Any:
    """Create an MP4 writer matching the camera frame size."""
    height, width = frame.shape[:2]
    fourcc = cv2.VideoWriter.fourcc(*"mp4v")
    return cv2.VideoWriter(str(path), fourcc, CAPTURE_FPS, (width, height))


def _encode_analysis_frames(
    candidates: list[_FrameCandidate | None],
) -> tuple[list[str], list[float]]:
    """JPEG/base64 encode selected frames in chronological order."""
    images: list[str] = []
    timestamps: list[float] = []
    for candidate in candidates:
        if candidate is None:
            continue
        success, buffer = cv2.imencode(
            ".jpg",
            candidate.frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )
        if not success:
            logger.warning("Skipping a scene-scan frame that failed JPEG encoding")
            continue
        images.append(base64.b64encode(buffer.tobytes()).decode("utf-8"))
        timestamps.append(round(candidate.elapsed_seconds, 2))
    return images, timestamps


class ScanScene(Tool):
    """Sweep, record a video, and provide chronological frames for visual analysis."""

    name = "scan_scene"
    description = (
        "Sweep Reachy from left to right while recording a video, then analyze representative "
        "frames to answer a question about everything visible during the sweep. Use this instead "
        "of separate sweep_look and camera calls when the user asks to scan, record, or survey a scene."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to determine from the complete scene scan, for example: "
                    "'List the people, objects, text, and notable surroundings you saw.'"
                ),
            },
        },
        "required": ["question"],
    }

    async def _wait_for_frame(self, camera_worker: Any) -> NDArray[np.uint8] | None:
        """Wait briefly for the camera worker to publish its first frame."""
        deadline = time.monotonic() + FRAME_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            frame = camera_worker.get_latest_frame()
            if frame is not None:
                return frame
            await asyncio.sleep(0.05)
        return None

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Record the complete sweep and return sampled frames to the conversation model."""
        question = (kwargs.get("question") or "").strip()
        if not question:
            return {"error": "question must be a non-empty string"}
        if deps.camera_worker is None:
            return {"error": "Camera worker not available"}

        first_frame = await self._wait_for_frame(deps.camera_worker)
        if first_frame is None:
            return {"error": "No frame available from camera worker"}

        capture_directory = (deps.capture_directory or Path("captures")).expanduser().resolve()
        capture_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        video_path = capture_directory / f"reachy-scene-scan-{timestamp}.mp4"
        writer = _open_video_writer(video_path, first_frame)
        if not writer.isOpened():
            writer.release()
            return {"error": f"Could not open video writer for {video_path}"}

        was_tracking_enabled = bool(getattr(deps.camera_worker, "is_head_tracking_enabled", False))
        scan_completed = False
        frames_recorded = 0
        candidates: list[_FrameCandidate | None] = [None] * MAX_ANALYSIS_FRAMES
        recording_duration = SWEEP_TOTAL_DURATION_SECONDS + SWEEP_RECORDING_SETTLE_SECONDS
        started_at = time.monotonic()

        logger.info(
            "Tool call: scan_scene question=%s video=%s duration=%.2fs",
            question[:120],
            video_path,
            recording_duration,
        )

        try:
            deps.camera_worker.set_head_tracking_enabled(False)
            clear_offsets = getattr(deps.camera_worker, "clear_face_tracking_offsets", None)
            if callable(clear_offsets):
                clear_offsets()

            await SweepLook()(deps)

            frame_period = 1.0 / CAPTURE_FPS
            next_frame_at = started_at
            while True:
                now = time.monotonic()
                elapsed = now - started_at
                if elapsed > recording_duration:
                    break

                frame = deps.camera_worker.get_latest_frame()
                if frame is not None:
                    writer.write(frame)
                    frames_recorded += 1

                    bin_index = min(
                        MAX_ANALYSIS_FRAMES - 1,
                        int((elapsed / recording_duration) * MAX_ANALYSIS_FRAMES),
                    )
                    sharpness = _frame_sharpness(frame)
                    current = candidates[bin_index]
                    if current is None or sharpness > current.sharpness:
                        candidates[bin_index] = _FrameCandidate(sharpness, elapsed, frame.copy())

                next_frame_at += frame_period
                await asyncio.sleep(max(0.0, next_frame_at - time.monotonic()))

            scan_completed = True
        finally:
            writer.release()
            if was_tracking_enabled:
                deps.camera_worker.set_head_tracking_enabled(True)
            if not scan_completed:
                deps.require_movement_manager().clear_move_queue()
                video_path.unlink(missing_ok=True)

        b64_images, frame_timestamps = await asyncio.to_thread(_encode_analysis_frames, candidates)
        if not b64_images:
            video_path.unlink(missing_ok=True)
            return {"error": "The sweep recorded no usable analysis frames"}

        elapsed_total = round(time.monotonic() - started_at, 2)
        logger.info(
            "Scene scan captured video=%s frames_recorded=%d analysis_frames=%d elapsed=%.2fs",
            video_path,
            frames_recorded,
            len(b64_images),
            elapsed_total,
        )
        return {
            "status": "scene_scan_complete",
            "question": question,
            "video_path": str(video_path),
            "duration_seconds": elapsed_total,
            "frames_recorded": frames_recorded,
            "frames_selected": len(b64_images),
            "frame_timestamps_seconds": frame_timestamps,
            "b64_images": b64_images,
        }
