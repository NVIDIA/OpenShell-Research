import logging
from typing import Any, Dict

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_EMOTION_LIBRARY_REPO = "pollen-robotics/reachy-mini-emotions-library"
_RECORDED_MOVES: Any | None = None
_EMOTION_IMPORT_ERROR: str | None = None


def _get_recorded_moves() -> Any | None:
    """Load the emotion library on first use instead of while building tool specs."""
    global _RECORDED_MOVES, _EMOTION_IMPORT_ERROR

    if _RECORDED_MOVES is not None:
        return _RECORDED_MOVES
    if _EMOTION_IMPORT_ERROR is not None:
        return None

    try:
        from reachy_mini.motion.recorded_move import RecordedMoves
    except ImportError as e:
        _EMOTION_IMPORT_ERROR = str(e)
        logger.warning("Emotion library not available: %s", e)
        return None

    try:
        # huggingface_hub automatically reads HF_TOKEN from environment variables.
        _RECORDED_MOVES = RecordedMoves(_EMOTION_LIBRARY_REPO)
    except Exception as e:
        _EMOTION_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        logger.warning("Emotion library could not be loaded: %s", e)
        return None

    return _RECORDED_MOVES


def get_available_emotions_and_descriptions() -> str:
    """Get formatted list of available emotions with descriptions."""
    recorded_moves = _get_recorded_moves()
    if recorded_moves is None:
        return "Emotions not available"

    try:
        emotion_names = recorded_moves.list_moves()
        output = "Available emotions:\n"
        for name in emotion_names:
            description = recorded_moves.get(name).description
            output += f" - {name}: {description}\n"
        return output
    except Exception as e:
        return f"Error getting emotions: {e}"


class PlayEmotion(Tool):
    """Play a pre-recorded emotion."""

    name = "play_emotion"
    description = "Play a pre-recorded emotion"
    parameters_schema = {
        "type": "object",
        "properties": {
            "emotion": {
                "type": "string",
                "description": (
                    "Name of the emotion to play from the Reachy Mini emotions library, for example welcoming1."
                ),
            },
        },
        "required": ["emotion"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Play a pre-recorded emotion."""
        recorded_moves = _get_recorded_moves()
        if recorded_moves is None:
            detail = f": {_EMOTION_IMPORT_ERROR}" if _EMOTION_IMPORT_ERROR else ""
            return {"error": f"Emotion system not available{detail}"}

        try:
            from reachy_mini_conversation_app.dance_emotion_moves import EmotionQueueMove
        except ImportError as e:
            logger.warning("Emotion queue move not available: %s", e)
            return {"error": f"Emotion system not available: {e}"}

        emotion_name = kwargs.get("emotion")
        if not emotion_name:
            return {"error": "Emotion name is required"}

        logger.info("Tool call: play_emotion emotion=%s", emotion_name)

        # Check if emotion exists
        try:
            emotion_names = recorded_moves.list_moves()
            if emotion_name not in emotion_names:
                return {"error": f"Unknown emotion '{emotion_name}'. Available: {emotion_names}"}

            # Add emotion to queue
            movement_manager = deps.require_movement_manager()
            emotion_move = EmotionQueueMove(emotion_name, recorded_moves)
            movement_manager.queue_move(emotion_move)

            return {"status": "queued", "emotion": emotion_name}

        except Exception as e:
            logger.exception("Failed to play emotion")
            return {"error": f"Failed to play emotion: {e!s}"}
