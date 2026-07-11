"""Process raw local-tool media before any result reaches a conversation model."""

from __future__ import annotations
import base64
import logging
from typing import TYPE_CHECKING, Any
from pathlib import Path
from dataclasses import dataclass


if TYPE_CHECKING:
    from reachy_mini_conversation_app.vision_router import VisionRouter, VisionAnalysis


logger = logging.getLogger(__name__)

MAX_SCENE_IMAGES = 9
_RAW_MEDIA_KEYS = frozenset({"b64_im", "b64_images"})


class MediaSecurityError(RuntimeError):
    """Raised when raw media would cross the model-visible boundary."""


@dataclass
class ProcessedToolResult:
    """Separate model-visible text from UI-only media artifacts."""

    model_payload: dict[str, Any]
    preview_image: Any | None = None
    video_path: Path | None = None


class MediaResultProcessor:
    """Route camera media to approved vision and sanitize the tool result."""

    def __init__(
        self,
        *,
        vision_router: VisionRouter | None,
        capture_directory: Path,
        require_routed_vision: bool,
    ) -> None:
        """Configure media routing and fail startup when strict routing is unavailable."""
        if require_routed_vision and vision_router is None:
            raise ValueError("REQUIRE_ROUTED_VISION=1 requires a configured VisionRouter")

        self.vision_router = vision_router
        self.capture_directory = capture_directory.expanduser().resolve()
        self.require_routed_vision = require_routed_vision

    async def process(self, tool_name: str, tool_result: dict[str, Any]) -> ProcessedToolResult:
        """Process supported media results and verify that model output has no raw bytes."""
        if tool_name == "camera" and "b64_im" in tool_result:
            processed = await self._process_camera(tool_result)
        elif tool_name == "scan_scene" and "b64_images" in tool_result:
            processed = await self._process_scene_scan(tool_result)
        elif contains_raw_media(tool_result):
            processed = self._security_failure(tool_name, "Unexpected raw media field in tool result")
        else:
            processed = ProcessedToolResult(model_payload=dict(tool_result))

        assert_no_raw_media(processed.model_payload)
        return processed

    async def _process_camera(self, tool_result: dict[str, Any]) -> ProcessedToolResult:
        result = dict(tool_result)
        raw_image = result.pop("b64_im", None)
        question = result.get("question")
        if not isinstance(raw_image, str) or not raw_image:
            return self._security_failure("camera", "Camera returned an invalid image")
        if not isinstance(question, str) or not question.strip():
            return self._security_failure("camera", "Camera returned an invalid question")

        try:
            preview_image = _decode_preview(raw_image)
        except ValueError:
            return self._security_failure("camera", "Camera returned an invalid JPEG")

        analysis = await self._analyze_images(
            tool_name="camera",
            images=[raw_image],
            question=question.strip(),
            timestamps=None,
        )
        if analysis is None:
            return self._vision_failure("camera")

        model_payload = _analysis_payload(analysis, question=question.strip(), status="image_analyzed")
        return ProcessedToolResult(model_payload=model_payload, preview_image=preview_image)

    async def _process_scene_scan(self, tool_result: dict[str, Any]) -> ProcessedToolResult:
        result = dict(tool_result)
        raw_images = result.pop("b64_images", None)
        question = result.get("question")
        timestamps = result.get("frame_timestamps_seconds")

        if (
            not isinstance(raw_images, list)
            or not 1 <= len(raw_images) <= MAX_SCENE_IMAGES
            or not all(isinstance(image, str) and image for image in raw_images)
        ):
            return self._security_failure(
                "scan_scene",
                f"Scene scan must contain between 1 and {MAX_SCENE_IMAGES} images",
            )
        if not isinstance(question, str) or not question.strip():
            return self._security_failure("scan_scene", "Scene scan returned an invalid question")
        if not _valid_timestamps(timestamps, len(raw_images)):
            return self._security_failure("scan_scene", "Scene scan returned invalid frame timestamps")
        try:
            for image in raw_images:
                base64.b64decode(image, validate=True)
        except (ValueError, TypeError):
            return self._security_failure("scan_scene", "Scene scan returned invalid image data")

        analysis = await self._analyze_images(
            tool_name="scan_scene",
            images=raw_images,
            question=question.strip(),
            timestamps=timestamps,
        )
        if analysis is None:
            return self._vision_failure("scan_scene")

        excluded = {
            "b64_images",
            "video_url",
            "video_path",
            "image_description",
            "selected_model",
            "response_id",
            "usage",
        }
        recording_metadata = {key: value for key, value in result.items() if key not in excluded}
        model_payload = {
            **recording_metadata,
            **_analysis_payload(analysis, question=question.strip(), status="scene_analyzed"),
        }

        try:
            video_path = await self._scene_video_path(result)
        except Exception as exc:
            logger.error("Scene video retrieval failed: %s", type(exc).__name__)
            model_payload["recording_status"] = "preview_unavailable"
            model_payload["recording_error"] = (
                "The scene was analyzed successfully, but the recording preview could not be retrieved"
            )
            return ProcessedToolResult(model_payload=model_payload)

        model_payload["recording_status"] = "available"
        return ProcessedToolResult(model_payload=model_payload, video_path=video_path)

    async def _analyze_images(
        self,
        *,
        tool_name: str,
        images: list[str],
        question: str,
        timestamps: list[float] | None,
    ) -> VisionAnalysis | None:
        if self.vision_router is None:
            logger.error("Routed vision unavailable for tool=%s", tool_name)
            return None
        try:
            return await self.vision_router.analyze_images(
                images_base64=images,
                question=question,
                frame_timestamps=timestamps,
            )
        except Exception as exc:
            logger.error("Approved vision request failed for tool=%s error=%s", tool_name, type(exc).__name__)
            return None

    async def _scene_video_path(self, result: dict[str, Any]) -> Path:
        local_path = result.get("video_path")
        if isinstance(local_path, str):
            return self._validated_local_video(local_path)
        raise MediaSecurityError("Missing local scene recording path")

    def _validated_local_video(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve(strict=True)
        if path.suffix.lower() != ".mp4" or path.is_symlink():
            raise MediaSecurityError("Invalid local scene recording")
        if path.parent != self.capture_directory:
            raise MediaSecurityError("Scene recording is outside the configured capture directory")
        return path

    @staticmethod
    def _security_failure(tool_name: str, message: str) -> ProcessedToolResult:
        logger.error("Media security check failed for tool=%s: %s", tool_name, message)
        return ProcessedToolResult(
            model_payload={
                "status": "media_security_error",
                "tool": tool_name,
                "error": message,
            }
        )

    @staticmethod
    def _vision_failure(tool_name: str) -> ProcessedToolResult:
        return ProcessedToolResult(
            model_payload={
                "status": "vision_error",
                "tool": tool_name,
                "error": "Approved vision analysis failed; raw media was discarded",
            }
        )


def contains_raw_media(value: Any) -> bool:
    """Return whether a value contains raw image fields or data URLs."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _RAW_MEDIA_KEYS:
                return True
            if str(key).lower() == "image_url" and isinstance(item, str) and item.startswith("data:image/"):
                return True
            if contains_raw_media(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_raw_media(item) for item in value)
    return False


def assert_no_raw_media(value: Any) -> None:
    """Reject any model payload that still contains raw images."""
    if contains_raw_media(value):
        raise MediaSecurityError("Raw media reached the model-visible tool result")


def _analysis_payload(analysis: VisionAnalysis, *, question: str, status: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "question": question,
        "image_description": analysis.description,
        "selected_model": analysis.selected_model,
    }
    if analysis.response_id:
        payload["response_id"] = analysis.response_id
    if analysis.usage is not None:
        payload["usage"] = _jsonable(analysis.usage)
    return payload


def _jsonable(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    return value


def _decode_preview(image_base64: str) -> Any:
    import cv2
    import numpy as np

    try:
        encoded = base64.b64decode(image_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid Base64 image") from exc
    try:
        frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise ValueError("Invalid JPEG image") from exc
    if frame is None:
        raise ValueError("Invalid JPEG image")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def _valid_timestamps(value: Any, image_count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == image_count
        and all(isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) for timestamp in value)
    )
