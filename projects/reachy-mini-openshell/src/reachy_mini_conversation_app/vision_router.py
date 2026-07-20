"""Single-model routing for camera and scene-scan image analysis."""

from __future__ import annotations
import logging
from typing import Any, Iterable
from dataclasses import dataclass

from openai import AsyncOpenAI

from reachy_mini_conversation_app.config import config, vision_api_key


logger = logging.getLogger(__name__)

MAX_VISION_IMAGES = 9


@dataclass(frozen=True)
class VisionAnalysis:
    """Text and routing metadata returned by an approved vision request."""

    description: str
    selected_model: str
    response_id: str | None
    usage: Any | None

    def as_tool_result(self) -> dict[str, Any]:
        """Return a JSON-safe tool result without including the source image."""
        result: dict[str, Any] = {
            "status": "image_analyzed",
            "image_description": self.description,
            "selected_model": self.selected_model,
        }
        if self.response_id:
            result["response_id"] = self.response_id
        if self.usage is not None:
            result["usage"] = _jsonable(self.usage)
        return result


def _jsonable(value: Any) -> Any:
    """Convert OpenAI SDK response values to plain JSON-compatible objects."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    return value


class VisionRouter:
    """Route one or more images to the single approved Responses model."""

    def __init__(
        self,
        *,
        client: Any,
        default_model: str,
        allowed_models: Iterable[str],
    ) -> None:
        """Initialize and validate the routing policy."""
        self.client = client
        self.default_model = default_model.strip()
        self.allowed_models = tuple(dict.fromkeys(model.strip() for model in allowed_models if model.strip()))

        if len(self.allowed_models) != 1:
            raise ValueError("VISION_ALLOWED_MODELS must contain exactly one model")
        if not self.default_model:
            raise ValueError("VISION_DEFAULT_MODEL must be configured")
        if self.default_model != self.allowed_models[0]:
            raise ValueError(
                f"VISION_DEFAULT_MODEL={self.default_model!r} must equal the only VISION_ALLOWED_MODELS entry"
            )

    async def analyze_images(
        self,
        *,
        images_base64: list[str],
        question: str,
        frame_timestamps: list[float] | None = None,
    ) -> VisionAnalysis:
        """Analyze one or more chronological images with the configured default model."""
        if not 1 <= len(images_base64) <= MAX_VISION_IMAGES:
            raise ValueError(f"Vision analysis requires between 1 and {MAX_VISION_IMAGES} images")
        if not all(isinstance(image, str) and image for image in images_base64):
            raise ValueError("Vision images must be non-empty Base64 strings")
        if frame_timestamps is not None and len(frame_timestamps) != len(images_base64):
            raise ValueError("Frame timestamp count must match image count")

        return await self._analyze(
            images_base64=images_base64,
            question=question,
            frame_timestamps=frame_timestamps,
        )

    async def _analyze(
        self,
        *,
        images_base64: list[str],
        question: str,
        frame_timestamps: list[float] | None,
    ) -> VisionAnalysis:
        """Send one Responses request containing text followed by ordered images."""
        prompt = question
        if len(images_base64) > 1:
            prompt = (
                "These are chronological frames sampled across one Reachy scene sweep. "
                f"Frame timestamps in seconds: {frame_timestamps or []}. "
                "Combine evidence across every frame, deduplicate people and objects visible more than once, "
                "and describe only details supported by the images. "
                f"User question: {question}"
            )
        logger.info(
            "VISION request selected_model=%s image_count=%d question=%s",
            self.default_model,
            len(images_base64),
            question[:160],
        )

        response = await self.client.responses.create(
            model=self.default_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        *[
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{image_base64}",
                            }
                            for image_base64 in images_base64
                        ],
                    ],
                }
            ],
        )

        description = (getattr(response, "output_text", None) or "").strip()
        if not description:
            raise RuntimeError(f"Vision model {self.default_model!r} returned no text")

        response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        logger.info(
            "VISION response selected_model=%s response_id=%s usage=%s",
            self.default_model,
            response_id,
            _jsonable(usage),
        )
        return VisionAnalysis(
            description=description,
            selected_model=self.default_model,
            response_id=response_id if isinstance(response_id, str) else None,
            usage=usage,
        )


def build_vision_router() -> VisionRouter | None:
    """Build the configured router, or preserve the legacy path when no key exists."""
    api_key = vision_api_key()
    if not api_key:
        logger.warning(
            "Routed camera vision is disabled because neither VISION_API_KEY nor OPENAI_API_KEY is configured."
        )
        return None

    if not config.VISION_BASE_URL:
        raise ValueError("VISION_BASE_URL must be configured when routed camera vision is enabled")

    return VisionRouter(
        client=AsyncOpenAI(api_key=api_key, base_url=config.VISION_BASE_URL),
        default_model=config.VISION_DEFAULT_MODEL or "",
        allowed_models=config.VISION_ALLOWED_MODELS,
    )
