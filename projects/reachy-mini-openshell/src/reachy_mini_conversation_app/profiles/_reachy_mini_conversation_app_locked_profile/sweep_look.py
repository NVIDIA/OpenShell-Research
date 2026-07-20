import logging
from typing import Any, Dict

import numpy as np

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.goto_queue_move import GotoQueueMove
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SweepLook(Tool):
    """Sweep head from left to right and back to center, pausing at each position."""

    name = "sweep_look"
    description = (
        "Sweep head from left to right while rotating the body, pausing at each extreme, then return to center"
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Execute sweep look: left -> hold -> right -> hold -> center."""
        logger.info("Tool call: sweep_look")

        deps.movement_manager.clear_move_queue()

        current_head_pose = deps.reachy_mini.get_current_head_pose()
        head_joints, antenna_joints = deps.reachy_mini.get_current_joint_positions()

        current_body_yaw = head_joints[0]
        current_antenna1 = antenna_joints[0]
        current_antenna2 = antenna_joints[1]

        max_angle = 0.9 * np.pi
        transition_duration = 3.0
        hold_duration = 1.0

        left_head_pose = create_head_pose(0, 0, 0, 0, 0, max_angle, degrees=False)
        move_to_left = GotoQueueMove(
            target_head_pose=left_head_pose,
            start_head_pose=current_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw + max_angle,
            start_body_yaw=current_body_yaw,
            duration=transition_duration,
        )

        hold_left = GotoQueueMove(
            target_head_pose=left_head_pose,
            start_head_pose=left_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw + max_angle,
            start_body_yaw=current_body_yaw + max_angle,
            duration=hold_duration,
        )

        center_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=False)
        return_to_center_from_left = GotoQueueMove(
            target_head_pose=center_head_pose,
            start_head_pose=left_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw,
            start_body_yaw=current_body_yaw + max_angle,
            duration=transition_duration,
        )

        right_head_pose = create_head_pose(0, 0, 0, 0, 0, -max_angle, degrees=False)
        move_to_right = GotoQueueMove(
            target_head_pose=right_head_pose,
            start_head_pose=center_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw - max_angle,
            start_body_yaw=current_body_yaw,
            duration=transition_duration,
        )

        hold_right = GotoQueueMove(
            target_head_pose=right_head_pose,
            start_head_pose=right_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw - max_angle,
            start_body_yaw=current_body_yaw - max_angle,
            duration=hold_duration,
        )

        return_to_center_final = GotoQueueMove(
            target_head_pose=center_head_pose,
            start_head_pose=right_head_pose,
            target_antennas=(current_antenna1, current_antenna2),
            start_antennas=(current_antenna1, current_antenna2),
            target_body_yaw=current_body_yaw,
            start_body_yaw=current_body_yaw - max_angle,
            duration=transition_duration,
        )

        deps.movement_manager.queue_move(move_to_left)
        deps.movement_manager.queue_move(hold_left)
        deps.movement_manager.queue_move(return_to_center_from_left)
        deps.movement_manager.queue_move(move_to_right)
        deps.movement_manager.queue_move(hold_right)
        deps.movement_manager.queue_move(return_to_center_final)

        total_duration = transition_duration * 4 + hold_duration * 2
        deps.movement_manager.set_moving_state(total_duration)

        return {"status": f"sweeping look left-right-center, total {total_duration:.1f}s"}
