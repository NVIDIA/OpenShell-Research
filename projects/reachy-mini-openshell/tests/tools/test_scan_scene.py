"""Tests for synchronized Reachy scene scanning and recording."""

import base64
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from numpy.typing import NDArray

import reachy_mini_conversation_app.tools.core_tools as core_tools
import reachy_mini_conversation_app.profiles._reachy_mini_conversation_app_locked_profile.scan_scene as scan_mod
import reachy_mini_conversation_app.profiles._reachy_mini_conversation_app_locked_profile.sweep_look as sweep_mod


ToolDependencies = core_tools.ToolDependencies


class _FakeCameraWorker:
    def __init__(self, frame: NDArray[np.uint8] | None) -> None:
        self.frame = frame
        self.is_head_tracking_enabled = True
        self.tracking_changes: list[bool] = []
        self.offsets_cleared = False

    def get_latest_frame(self) -> NDArray[np.uint8] | None:
        return None if self.frame is None else self.frame.copy()

    def set_head_tracking_enabled(self, enabled: bool) -> None:
        self.tracking_changes.append(enabled)
        self.is_head_tracking_enabled = enabled

    def clear_face_tracking_offsets(self) -> None:
        self.offsets_cleared = True


class _FakeVideoWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.touch()
        self.frames: list[NDArray[np.uint8]] = []
        self.released = False

    def isOpened(self) -> bool:
        return True

    def write(self, frame: NDArray[np.uint8]) -> None:
        self.frames.append(frame.copy())

    def release(self) -> None:
        self.released = True


def test_scan_scene_schema_requires_an_analysis_question() -> None:
    """The model should state what it wants determined from the complete scan."""
    schema = scan_mod.ScanScene.parameters_schema

    assert schema["required"] == ["question"]
    assert schema["properties"]["question"]["type"] == "string"


@pytest.mark.asyncio
async def test_sweep_look_uses_absolute_left_right_and_front_body_targets() -> None:
    """A scan that begins off-center must still end at absolute front."""
    movement_manager = MagicMock()
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = ([0.6, *([0.0] * 6)], [0.1, -0.1])
    deps = ToolDependencies(reachy_mini=robot, movement_manager=movement_manager)

    await sweep_mod.SweepLook()(deps)

    queued_moves = [call.args[0] for call in movement_manager.queue_move.call_args_list]
    max_angle = sweep_mod.SWEEP_MAX_ANGLE_RADIANS
    assert [move.target_body_yaw for move in queued_moves] == [
        max_angle,
        max_angle,
        0,
        -max_angle,
        -max_angle,
        0,
    ]
    assert queued_moves[0].start_body_yaw == 0.6
    assert queued_moves[-1].target_body_yaw == 0


@pytest.mark.asyncio
async def test_scan_scene_records_video_and_returns_chronological_frames(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """One tool call should coordinate motion, recording, and model-ready frames."""
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, 16:32] = 255
    camera_worker = _FakeCameraWorker(frame)
    movement_manager = MagicMock()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        capture_directory=tmp_path,
    )

    writer_holder: list[_FakeVideoWriter] = []

    def fake_open_writer(path: Path, _frame: NDArray[np.uint8]) -> _FakeVideoWriter:
        writer = _FakeVideoWriter(path)
        writer_holder.append(writer)
        return writer

    sweep_calls: list[ToolDependencies] = []

    async def fake_sweep(_self: Any, sweep_deps: ToolDependencies, **_kwargs: Any) -> dict[str, str]:
        sweep_calls.append(sweep_deps)
        return {"status": "queued"}

    monkeypatch.setattr(scan_mod, "_open_video_writer", fake_open_writer)
    monkeypatch.setattr(scan_mod.SweepLook, "__call__", fake_sweep)
    monkeypatch.setattr(scan_mod, "SWEEP_TOTAL_DURATION_SECONDS", 0.12)
    monkeypatch.setattr(scan_mod, "SWEEP_RECORDING_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(scan_mod, "CAPTURE_FPS", 60.0)
    monkeypatch.setattr(scan_mod, "MAX_ANALYSIS_FRAMES", 3)

    result = await scan_mod.ScanScene()(deps, question="What people and objects did you see?")

    assert result["status"] == "scene_scan_complete"
    assert result["question"] == "What people and objects did you see?"
    assert result["frames_recorded"] >= 3
    assert result["frames_selected"] == 3
    assert len(result["frame_timestamps_seconds"]) == 3
    assert len(result["b64_images"]) == 3
    assert all(base64.b64decode(image) for image in result["b64_images"])
    assert Path(result["video_path"]).is_file()
    assert sweep_calls == [deps]
    assert writer_holder[0].released is True
    assert len(writer_holder[0].frames) == result["frames_recorded"]
    assert camera_worker.offsets_cleared is True
    assert camera_worker.tracking_changes == [False, True]


@pytest.mark.asyncio
async def test_scan_scene_fails_before_moving_when_no_camera_frame(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """A missing camera feed must not start physical movement."""
    camera_worker = _FakeCameraWorker(None)
    movement_manager = MagicMock()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        capture_directory=tmp_path,
    )
    monkeypatch.setattr(scan_mod, "FRAME_WAIT_TIMEOUT_SECONDS", 0.01)

    result = await scan_mod.ScanScene()(deps, question="What do you see?")

    assert result == {"error": "No frame available from camera worker"}
    movement_manager.queue_move.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_scan_scene_stops_motion_and_removes_partial_video_on_recording_failure(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Recording failures should restore tracking and stop the in-progress sweep."""
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    camera_worker = _FakeCameraWorker(frame)
    movement_manager = MagicMock()
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        capture_directory=tmp_path,
    )

    class FailingWriter(_FakeVideoWriter):
        def write(self, frame: NDArray[np.uint8]) -> None:
            raise RuntimeError("disk full")

    writer_holder: list[FailingWriter] = []

    def fake_open_writer(path: Path, _frame: NDArray[np.uint8]) -> FailingWriter:
        writer = FailingWriter(path)
        writer_holder.append(writer)
        return writer

    async def fake_sweep(_self: Any, _deps: ToolDependencies, **_kwargs: Any) -> dict[str, str]:
        return {"status": "queued"}

    monkeypatch.setattr(scan_mod, "_open_video_writer", fake_open_writer)
    monkeypatch.setattr(scan_mod.SweepLook, "__call__", fake_sweep)
    monkeypatch.setattr(scan_mod, "SWEEP_TOTAL_DURATION_SECONDS", 0.1)
    monkeypatch.setattr(scan_mod, "SWEEP_RECORDING_SETTLE_SECONDS", 0.0)

    with pytest.raises(RuntimeError, match="disk full"):
        await scan_mod.ScanScene()(deps, question="What do you see?")

    movement_manager.clear_move_queue.assert_called_once_with()
    assert writer_holder[0].released is True
    assert not writer_holder[0].path.exists()
    assert camera_worker.tracking_changes == [False, True]
