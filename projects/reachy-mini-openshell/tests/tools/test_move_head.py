"""Tests for ordered Reachy Mini head movements."""

from unittest.mock import MagicMock

import numpy as np
import pytest

import reachy_mini_conversation_app.tools.core_tools as core_tools
from reachy_mini_conversation_app.tools.move_head import MoveHead


def _dependencies() -> tuple[core_tools.ToolDependencies, MagicMock]:
    reachy = MagicMock()
    reachy.get_current_head_pose.return_value = np.eye(4)
    reachy.get_current_joint_positions.return_value = ([0.25], [0.1, -0.1])
    movement_manager = MagicMock()
    deps = core_tools.ToolDependencies(
        reachy_mini=reachy,
        movement_manager=movement_manager,
        motion_duration_s=0.5,
    )
    return deps, movement_manager


def test_move_head_schema_requests_an_ordered_direction_list() -> None:
    """The model should receive a schema capable of representing a sequence."""
    schema = MoveHead.parameters_schema

    assert schema["required"] == ["directions"]
    assert schema["properties"]["directions"]["type"] == "array"
    assert schema["properties"]["directions"]["items"]["enum"] == [
        "left",
        "right",
        "up",
        "down",
        "front",
    ]


@pytest.mark.asyncio
async def test_move_head_queues_multiple_directions_in_order() -> None:
    """A compound request should become one deterministic queued sequence."""
    deps, movement_manager = _dependencies()

    result = await MoveHead()(deps, directions=["up", "right"])

    queued_moves = [call.args[0] for call in movement_manager.queue_move.call_args_list]
    assert len(queued_moves) == 2
    assert np.array_equal(queued_moves[0].start_head_pose, np.eye(4))
    assert np.array_equal(queued_moves[1].start_head_pose, queued_moves[0].target_head_pose)
    assert not np.array_equal(queued_moves[0].target_head_pose, queued_moves[1].target_head_pose)
    movement_manager.set_moving_state.assert_called_once_with(1.0)
    assert result == {
        "status": "queued",
        "directions": ["up", "right"],
        "total_duration_seconds": 1.0,
    }


@pytest.mark.asyncio
async def test_move_head_accepts_legacy_single_direction() -> None:
    """Existing direct callers using direction= remain compatible."""
    deps, movement_manager = _dependencies()

    result = await MoveHead()(deps, direction="left")

    assert movement_manager.queue_move.call_count == 1
    assert result["directions"] == ["left"]


@pytest.mark.asyncio
async def test_move_head_rejects_invalid_sequence() -> None:
    """Invalid directions should not enqueue partial motion."""
    deps, movement_manager = _dependencies()

    result = await MoveHead()(deps, directions=["up", "backwards"])

    assert "error" in result
    movement_manager.queue_move.assert_not_called()
