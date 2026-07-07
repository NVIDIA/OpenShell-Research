"""Allowlist-enforced routing for camera image analysis."""

from __future__ import annotations
import logging
from typing import Any, Iterable
from dataclasses import dataclass

from openai import AsyncOpenAI

from reachy_mini_conversation_app.config import config, vision_api_key


logger = logging.getLogger(__name__)


class VisionModelNotAllowed(ValueError):
    """Raised before upload when a requested vision model is not approved."""


@dataclass(frozen=True)
class VisionAnalysis:
    """Text and routing metadata returned by an approved vision request."""

    description: str
    requested_model: str | None
    selected_model: str
    response_id: str | None
    usage: Any | None

    def as_tool_result(self) -> dict[str, Any]:
        """Return a JSON-safe tool result without including the source image."""
        result: dict[str, Any] = {
            "status": "image_analyzed",
            "image_description": self.description,
            "requested_model": self.requested_model,
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
    """Route one camera image to a server-approved model through Responses."""

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

        if not self.allowed_models:
            raise ValueError("VISION_ALLOWED_MODELS must contain at least one model")
        if not self.default_model:
            raise ValueError("VISION_DEFAULT_MODEL must be configured")
        if self.default_model not in self.allowed_models:
            raise ValueError(f"VISION_DEFAULT_MODEL={self.default_model!r} must also appear in VISION_ALLOWED_MODELS")

    def select_model(self, requested_model: str | None) -> str:
        """Resolve a request to an approved model or fail before image upload."""
        requested = requested_model.strip() if isinstance(requested_model, str) else ""
        selected = requested or self.default_model
        if selected not in self.allowed_models:
            allowed = ", ".join(self.allowed_models)
            raise VisionModelNotAllowed(f"Vision model {selected!r} is not approved. Allowed models: {allowed}.")
        return selected

    async def analyze_jpeg(
        self,
        *,
        image_base64: str,
        question: str,
        requested_model: str | None,
    ) -> VisionAnalysis:
        """Analyze a Base64 JPEG with the selected model and return text only."""
        selected_model = self.select_model(requested_model)
        normalized_requested = requested_model.strip() if isinstance(requested_model, str) else ""
        logger.info(
            "VISION request requested_model=%s selected_model=%s question=%s",
            normalized_requested or "<default>",
            selected_model,
            question[:160],
        )

        response = await self.client.responses.create(
            model=selected_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{image_base64}",
                        },
                    ],
                }
            ],
        )

        description = (getattr(response, "output_text", None) or "").strip()
        if not description:
            raise RuntimeError(f"Vision model {selected_model!r} returned no text")

        response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        logger.info(
            "VISION response selected_model=%s response_id=%s usage=%s",
            selected_model,
            response_id,
            _jsonable(usage),
        )
        return VisionAnalysis(
            description=description,
            requested_model=normalized_requested or None,
            selected_model=selected_model,
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
