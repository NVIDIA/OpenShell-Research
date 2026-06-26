"""Queued goto move for direct head, antenna, and body-yaw targets."""

from __future__ import annotations
import logging
from typing import Tuple

import numpy as np
from numpy.typing import NDArray

from reachy_mini.motion.move import Move


logger = logging.getLogger(__name__)


class GotoQueueMove(Move):  # type: ignore[misc]
    """Interpolate from a current pose to a target pose."""

    def __init__(
        self,
        target_head_pose: NDArray[np.float64],
        start_head_pose: NDArray[np.float64] | None = None,
        target_antennas: Tuple[float, float] = (0, 0),
        start_antennas: Tuple[float, float] | None = None,
        target_body_yaw: float = 0,
        start_body_yaw: float | None = None,
        duration: float = 1.0,
    ):
        """Initialize a queued goto move."""
        self._duration = duration
        self.target_head_pose = target_head_pose
        self.start_head_pose = start_head_pose
        self.target_antennas = target_antennas
        self.start_antennas = start_antennas or (0, 0)
        self.target_body_yaw = target_body_yaw
        self.start_body_yaw = start_body_yaw or 0

    @property
    def duration(self) -> float:
        """Duration required by the Move interface."""
        return self._duration

    def evaluate(self, t: float) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate the move at elapsed time t."""
        try:
            from reachy_mini.utils import create_head_pose
            from reachy_mini.utils.interpolation import linear_pose_interpolation

            t_clamped = max(0, min(1, t / self.duration))
            start_pose = self.start_head_pose
            if start_pose is None:
                start_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)

            head_pose = linear_pose_interpolation(start_pose, self.target_head_pose, t_clamped)
            antennas = np.array(
                [
                    self.start_antennas[0] + (self.target_antennas[0] - self.start_antennas[0]) * t_clamped,
                    self.start_antennas[1] + (self.target_antennas[1] - self.start_antennas[1]) * t_clamped,
                ],
                dtype=np.float64,
            )
            body_yaw = self.start_body_yaw + (self.target_body_yaw - self.start_body_yaw) * t_clamped
            return (head_pose, antennas, body_yaw)
        except Exception as e:
            logger.error("Error evaluating goto move at t=%s: %s", t, e)
            target_head_pose_f64 = self.target_head_pose.astype(np.float64)
            target_antennas_array = np.array([self.target_antennas[0], self.target_antennas[1]], dtype=np.float64)
            return (target_head_pose_f64, target_antennas_array, self.target_body_yaw)
