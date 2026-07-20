"""Dance and emotion moves for the movement queue system.

This module implements dance moves and emotions as Move objects that can be queued
and executed sequentially by the MovementManager.
"""

from __future__ import annotations
import logging

import numpy as np
from numpy.typing import NDArray

from reachy_mini.motion.move import Move
from reachy_mini.motion.recorded_move import RecordedMoves
from reachy_mini_dances_library.dance_move import DanceMove


logger = logging.getLogger(__name__)


class DanceQueueMove(Move):
    """Wrapper for dance moves to work with the movement queue system."""

    def __init__(self, move_name: str):
        """Initialize a DanceQueueMove."""
        self.dance_move = DanceMove(move_name)
        self.move_name = move_name

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return float(self.dance_move.duration)

    def evaluate(self, t: float) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate dance move at time t."""
        try:
            # Get the pose from the dance move
            head_pose, antennas, body_yaw = self.dance_move.evaluate(t)

            # Convert to numpy array if antennas is tuple and return in official Move format
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]])

            return (head_pose, antennas, body_yaw)

        except Exception as e:
            logger.error(f"Error evaluating dance move '{self.move_name}' at t={t}: {e}")
            # Return neutral pose on error
            from reachy_mini.utils import create_head_pose

            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            return (neutral_head_pose, np.array([0.0, 0.0], dtype=np.float64), 0.0)


class EmotionQueueMove(Move):
    """Wrapper for emotion moves to work with the movement queue system."""

    def __init__(self, emotion_name: str, recorded_moves: RecordedMoves):
        """Initialize an EmotionQueueMove."""
        self.emotion_move = recorded_moves.get(emotion_name)
        self.emotion_name = emotion_name

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return float(self.emotion_move.duration)

    def evaluate(self, t: float) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate emotion move at time t."""
        try:
            # Get the pose from the emotion move
            head_pose, antennas, body_yaw = self.emotion_move.evaluate(t)

            # Convert to numpy array if antennas is tuple and return in official Move format
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]])

            return (head_pose, antennas, body_yaw)

        except Exception as e:
            logger.error(f"Error evaluating emotion '{self.emotion_name}' at t={t}: {e}")
            # Return neutral pose on error
            from reachy_mini.utils import create_head_pose

            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            return (neutral_head_pose, np.array([0.0, 0.0], dtype=np.float64), 0.0)
