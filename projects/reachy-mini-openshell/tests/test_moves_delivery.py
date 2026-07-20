from __future__ import annotations
from typing import Any

import numpy as np

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.moves import MovementManager


class _Client:
    def __init__(self) -> None:
        self._is_alive = True


class _Robot:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.client = _Client()
        self.failure = failure
        self.targets: list[dict[str, Any]] = []

    def set_target(self, **kwargs: Any) -> None:
        self.targets.append(kwargs)
        if self.failure is not None:
            raise self.failure


class _ObservedRobot(_Robot):
    def __init__(self) -> None:
        super().__init__()
        self.head_pose = create_head_pose(0, 0, 0, 0, 0, 10, degrees=True)

    def get_current_head_pose(self) -> np.ndarray[Any, Any]:
        return self.head_pose.copy()

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        return [0.1] + [0.0] * 6, [0.2, -0.2]


def _target() -> tuple[np.ndarray[Any, Any], tuple[float, float], float]:
    return create_head_pose(0, 0, 0, 0, 0, 20, degrees=True), (0.0, 0.0), 0.0


def test_successful_target_send_advances_delivery_checkpoint() -> None:
    """A successful socket write should release delivery waiters."""
    robot = _Robot()
    manager = MovementManager(robot)  # type: ignore[arg-type]
    checkpoint = manager.delivery_checkpoint()

    manager._issue_control_command(*_target())

    assert manager.wait_for_delivery(checkpoint, timeout=0) == (True, None)
    assert manager.connection_healthy() is True
    assert len(robot.targets) == 1


def test_first_delivery_failure_pauses_output_without_retry_flood() -> None:
    """An uncertain send should be terminal for this runtime and logged only once."""
    robot = _Robot(failure=ConnectionError("socket closed"))
    manager = MovementManager(robot)  # type: ignore[arg-type]
    checkpoint = manager.delivery_checkpoint()

    manager._issue_control_command(*_target())
    manager._issue_control_command(*_target())

    delivered, error = manager.wait_for_delivery(checkpoint, timeout=0)
    assert delivered is False
    assert error is not None and "socket closed" in error
    assert manager.connection_healthy() is False
    assert len(robot.targets) == 1


def test_unchanged_target_is_not_sent_continuously() -> None:
    """Idle control ticks should not flood the wireless daemon with no-op targets."""
    robot = _Robot()
    manager = MovementManager(robot, target_frequency_hz=50, enable_idle_breathing=False)  # type: ignore[arg-type]
    target = _target()

    assert manager._target_changed(*target) is True
    manager._issue_control_command(*target)
    assert manager._target_changed(*target) is False

    changed = (target[0].copy(), target[1], target[2])
    changed[0][0, 3] += 0.001
    assert manager._target_changed(*changed) is True


def test_observed_starting_pose_does_not_command_neutral_on_startup() -> None:
    """Starting the standalone runtime should preserve the robot's current physical pose."""
    robot = _ObservedRobot()
    manager = MovementManager(robot, enable_idle_breathing=False)  # type: ignore[arg-type]
    observed = (robot.head_pose, (0.2, -0.2), 0.1)

    assert manager._target_changed(*observed) is False
    assert robot.targets == []
