import logging
from typing import Any, Dict, Tuple, Literal

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.goto_queue_move import GotoQueueMove
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

Direction = Literal["left", "right", "up", "down", "front"]


class MoveHead(Tool):
    """Move the head through one or more ordered directions."""

    name = "move_head"
    description = (
        "Move your head through an ordered list of directions. Include every direction the user requests, "
        "in the same order. Valid directions are left, right, up, down, and front."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "directions": {
                "type": "array",
                "description": ("Ordered directions to perform. For 'look up and then right', use ['up', 'right']."),
                "items": {
                    "type": "string",
                    "enum": ["left", "right", "up", "down", "front"],
                },
                "minItems": 1,
                "maxItems": 8,
            },
        },
        "required": ["directions"],
    }

    # mapping: direction -> args for create_head_pose
    DELTAS: Dict[str, Tuple[int, int, int, int, int, int]] = {
        "left": (0, 0, 0, 0, 0, 40),
        "right": (0, 0, 0, 0, 0, -40),
        "up": (0, 0, 0, 0, -30, 0),
        "down": (0, 0, 0, 0, 30, 0),
        "front": (0, 0, 0, 0, 0, 0),
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Queue each requested head direction in order."""
        directions_raw = kwargs.get("directions")

        # Preserve compatibility with older callers that send {"direction": "up"}.
        if directions_raw is None and "direction" in kwargs:
            directions_raw = [kwargs.get("direction")]

        if not isinstance(directions_raw, list) or not directions_raw:
            return {"error": "directions must be a non-empty list"}
        if len(directions_raw) > 8:
            return {"error": "directions supports at most 8 movements"}

        directions: list[Direction] = []
        for direction_raw in directions_raw:
            if not isinstance(direction_raw, str) or direction_raw not in self.DELTAS:
                return {"error": (f"invalid direction {direction_raw!r}; expected one of {list(self.DELTAS.keys())}")}
            directions.append(direction_raw)  # type: ignore[arg-type]

        logger.info("Tool call: move_head directions=%s", directions)

        # Use new movement manager
        try:
            movement_manager = deps.require_movement_manager()
            reachy_mini = deps.require_reachy_mini()

            # Get current state for interpolation
            current_head_pose = reachy_mini.get_current_head_pose()
            head_joints, current_antennas = reachy_mini.get_current_joint_positions()

            start_head_pose = current_head_pose
            start_antennas = (current_antennas[0], current_antennas[1])
            start_body_yaw = head_joints[0]

            for direction in directions:
                target_head_pose = create_head_pose(*self.DELTAS[direction], degrees=True)
                movement_manager.queue_move(
                    GotoQueueMove(
                        target_head_pose=target_head_pose,
                        start_head_pose=start_head_pose,
                        target_antennas=(0, 0),
                        start_antennas=start_antennas,
                        target_body_yaw=0,
                        start_body_yaw=start_body_yaw,
                        duration=deps.motion_duration_s,
                    )
                )
                start_head_pose = target_head_pose
                start_antennas = (0, 0)
                start_body_yaw = 0

            movement_manager.set_moving_state(deps.motion_duration_s * len(directions))

            return {
                "status": "queued",
                "directions": directions,
                "total_duration_seconds": deps.motion_duration_s * len(directions),
            }

        except Exception as e:
            logger.error("move_head failed")
            return {"error": f"move_head failed: {type(e).__name__}: {e}"}
