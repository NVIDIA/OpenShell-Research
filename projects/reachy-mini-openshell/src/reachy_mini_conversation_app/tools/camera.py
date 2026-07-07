import base64
import asyncio
import logging
from typing import Any, Dict

import cv2

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Camera(Tool):
    """Take a picture with the camera and ask a question about it."""

    name = "camera"
    description = "Take a picture with the camera and ask a question about it."
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask about the picture",
            },
            "requested_model": {
                "type": "string",
                "description": (
                    "The exact vision model explicitly requested by the user. "
                    "Omit this field when the user did not name a model."
                ),
            },
        },
        "required": ["question"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Take a picture with the camera and ask a question about it."""
        image_query = (kwargs.get("question") or "").strip()
        if not image_query:
            logger.warning("camera: empty question")
            return {"error": "question must be a non-empty string"}

        requested_model_value = kwargs.get("requested_model")
        requested_model = requested_model_value.strip() if isinstance(requested_model_value, str) else None
        requested_model = requested_model or None

        logger.info(
            "Tool call: camera question=%s requested_model=%s",
            image_query[:120],
            requested_model or "<default>",
        )

        # Get frame from camera worker buffer (like main_works.py)
        if deps.camera_worker is not None:
            frame = deps.camera_worker.get_latest_frame()
            if frame is None:
                logger.error("No frame available from camera worker")
                return {"error": "No frame available"}
        else:
            logger.error("Camera worker not available")
            return {"error": "Camera worker not available"}

        if requested_model is not None and deps.vision_router is None:
            return {
                "error": (
                    f"Vision model {requested_model!r} was requested, but routed camera vision is not configured."
                ),
                "requested_model": requested_model,
            }

        # Use the explicitly enabled local vision manager before the legacy
        # active-conversation path. Main does not install both local vision and
        # routed cloud vision at the same time.
        if deps.vision_manager is not None:
            vision_result = await asyncio.to_thread(
                deps.vision_manager.processor.process_image,
                frame,
                image_query,
            )
            if isinstance(vision_result, dict) and "error" in vision_result:
                return vision_result
            return (
                {"image_description": vision_result}
                if isinstance(vision_result, str)
                else {"error": "vision returned non-string"}
            )

        # Encode image directly to JPEG bytes without writing to file
        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            raise RuntimeError("Failed to encode frame as JPEG")

        b64_encoded = base64.b64encode(buffer.tobytes()).decode("utf-8")

        if deps.vision_router is not None:
            from reachy_mini_conversation_app.vision_router import VisionModelNotAllowed

            try:
                analysis = await deps.vision_router.analyze_jpeg(
                    image_base64=b64_encoded,
                    question=image_query,
                    requested_model=requested_model,
                )
            except VisionModelNotAllowed as exc:
                logger.warning("Camera vision request rejected before upload: %s", exc)
                return {
                    "error": str(exc),
                    "requested_model": requested_model,
                    "status": "model_not_allowed",
                }
            return analysis.as_tool_result()

        return {"b64_im": b64_encoded, "question": image_query}
