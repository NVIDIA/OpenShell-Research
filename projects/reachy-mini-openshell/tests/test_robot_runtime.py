from __future__ import annotations
from typing import Any
from pathlib import Path

import pytest

import reachy_mini_conversation_app.robot_runtime as runtime_mod


class _Component:
    def __init__(self, name: str, events: list[str], *, fail_start: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start

    def start(self) -> None:
        self.events.append(f"{self.name}.start")
        if self.fail_start:
            raise RuntimeError(f"{self.name} failed")

    def stop(self) -> None:
        self.events.append(f"{self.name}.stop")


class _MovementManager(_Component):
    def set_speech_offsets(self, offsets: Any) -> None:
        del offsets

    def connection_healthy(self) -> bool:
        return True


class _Client:
    def __init__(self, events: list[str], status: Any) -> None:
        self.events = events
        self.status = status

    def get_status(self) -> Any:
        return self.status

    def disconnect(self) -> None:
        self.events.append("client.disconnect")


class _Media:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("media.close")


class _Robot:
    def __init__(self, events: list[str], status: Any) -> None:
        self.client = _Client(events, status)
        self.media = _Media(events)


def _patch_runtime_factories(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    camera_worker: Any | None,
    head_tracker: Any | None = None,
    vision_manager: Any | None = None,
    vision_router: Any | None = None,
) -> tuple[_MovementManager, _Component]:
    movement_manager = _MovementManager("movement", events)
    head_wobbler = _Component("wobbler", events)

    monkeypatch.setattr(
        runtime_mod,
        "handle_vision_stuff",
        lambda args, robot: (camera_worker, head_tracker, vision_manager),
    )
    monkeypatch.setattr(runtime_mod, "build_vision_router", lambda: vision_router)
    monkeypatch.setattr(runtime_mod, "MovementManager", lambda **kwargs: movement_manager)
    monkeypatch.setattr(runtime_mod, "HeadWobbler", lambda **kwargs: head_wobbler)
    return movement_manager, head_wobbler


def test_connect_builds_workers_dependencies_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime construction should wire every robot-dependent service once."""
    events: list[str] = []
    camera_worker = _Component("camera", events)
    head_tracker = object()
    vision_router = object()
    movement_manager, head_wobbler = _patch_runtime_factories(
        monkeypatch,
        events,
        camera_worker=camera_worker,
        head_tracker=head_tracker,
        vision_router=vision_router,
    )
    robot = _Robot(
        events,
        {
            "simulation_enabled": True,
            "mockup_sim_enabled": False,
        },
    )

    runtime = runtime_mod.ReachyRuntime.connect(
        robot=robot,  # type: ignore[arg-type]
        no_camera=False,
        head_tracker="mediapipe",
        capture_directory=Path("~/reachy-captures"),
        shutdown_delay_seconds=0,
    )

    assert runtime.robot is robot
    assert runtime.camera_worker is camera_worker
    assert runtime.head_tracker is head_tracker
    assert runtime.movement_manager is movement_manager
    assert runtime.head_wobbler is head_wobbler
    assert runtime.vision_router is vision_router
    assert runtime.is_simulation is True
    assert runtime.dependencies.reachy_mini is robot
    assert runtime.dependencies.movement_manager is movement_manager
    assert runtime.dependencies.camera_worker is camera_worker
    assert runtime.dependencies.vision_router is vision_router
    assert runtime.dependencies.capture_directory == Path("~/reachy-captures").expanduser()


def test_connect_constructs_robot_with_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone callers should be able to select the Reachy topic prefix."""
    events: list[str] = []
    robot = _Robot(events, {"simulation_enabled": False, "mockup_sim_enabled": False})
    received_kwargs: dict[str, Any] = {}

    def build_robot(**kwargs: Any) -> _Robot:
        received_kwargs.update(kwargs)
        return robot

    monkeypatch.setattr(runtime_mod, "ReachyMini", build_robot)
    _patch_runtime_factories(monkeypatch, events, camera_worker=None)

    runtime = runtime_mod.ReachyRuntime.connect(
        robot_name="test_reachy",
        shutdown_delay_seconds=0,
    )

    assert runtime.robot is robot
    assert received_kwargs == {"robot_name": "test_reachy"}
    assert runtime.is_simulation is False


def test_connect_passes_explicit_network_and_movement_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP runtime should honor the configured robot address and lower send rate."""
    events: list[str] = []
    robot = _Robot(events, {})
    received_robot_kwargs: dict[str, Any] = {}
    received_movement_kwargs: dict[str, Any] = {}

    def build_robot(**kwargs: Any) -> _Robot:
        received_robot_kwargs.update(kwargs)
        return robot

    monkeypatch.setattr(runtime_mod, "ReachyMini", build_robot)
    monkeypatch.setattr(runtime_mod, "handle_vision_stuff", lambda args, robot: (None, None, None))
    monkeypatch.setattr(runtime_mod, "build_vision_router", lambda: None)
    monkeypatch.setattr(
        runtime_mod,
        "MovementManager",
        lambda **kwargs: received_movement_kwargs.update(kwargs) or _MovementManager("movement", events),
    )
    monkeypatch.setattr(runtime_mod, "HeadWobbler", lambda **kwargs: _Component("wobbler", events))

    runtime_mod.ReachyRuntime.connect(
        robot_host="192.168.0.107",
        robot_port=8000,
        connection_mode="network",
        movement_frequency_hz=50,
        enable_idle_breathing=False,
        shutdown_delay_seconds=0,
    )

    assert received_robot_kwargs == {
        "host": "192.168.0.107",
        "port": 8000,
        "connection_mode": "network",
    }
    assert received_movement_kwargs["target_frequency_hz"] == 50
    assert received_movement_kwargs["enable_idle_breathing"] is False


def test_connect_skips_cloud_router_for_local_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit local vision should not also construct the routed cloud client."""
    events: list[str] = []
    camera_worker = _Component("camera", events)
    vision_manager = _Component("vision", events)
    router_builds = 0

    monkeypatch.setattr(
        runtime_mod,
        "handle_vision_stuff",
        lambda args, robot: (camera_worker, None, vision_manager),
    )

    def build_router() -> object:
        nonlocal router_builds
        router_builds += 1
        return object()

    monkeypatch.setattr(runtime_mod, "build_vision_router", build_router)
    monkeypatch.setattr(runtime_mod, "MovementManager", lambda **kwargs: _MovementManager("movement", events))
    monkeypatch.setattr(runtime_mod, "HeadWobbler", lambda **kwargs: _Component("wobbler", events))

    runtime = runtime_mod.ReachyRuntime.connect(
        robot=_Robot(events, {}),  # type: ignore[arg-type]
        local_vision=True,
        shutdown_delay_seconds=0,
    )

    assert runtime.vision_manager is vision_manager
    assert runtime.vision_router is None
    assert router_builds == 0


def test_connect_can_disable_cloud_router_for_camera_only_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trusted MCP runtime should capture images without constructing an OpenAI client."""
    events: list[str] = []
    camera_worker = _Component("camera", events)
    router_builds = 0

    monkeypatch.setattr(
        runtime_mod,
        "handle_vision_stuff",
        lambda args, robot: (camera_worker, None, None),
    )

    def build_router() -> object:
        nonlocal router_builds
        router_builds += 1
        return object()

    monkeypatch.setattr(runtime_mod, "build_vision_router", build_router)
    monkeypatch.setattr(runtime_mod, "MovementManager", lambda **kwargs: _MovementManager("movement", events))
    monkeypatch.setattr(runtime_mod, "HeadWobbler", lambda **kwargs: _Component("wobbler", events))

    runtime = runtime_mod.ReachyRuntime.connect(
        robot=_Robot(events, {}),  # type: ignore[arg-type]
        enable_vision_router=False,
        shutdown_delay_seconds=0,
    )

    assert runtime.camera_worker is camera_worker
    assert runtime.vision_router is None
    assert runtime.dependencies.vision_router is None
    assert router_builds == 0


def test_start_and_stop_manage_the_complete_lifecycle_once() -> None:
    """Lifecycle methods should be ordered and idempotent."""
    events: list[str] = []
    robot = _Robot(events, {})
    movement_manager = _MovementManager("movement", events)
    head_wobbler = _Component("wobbler", events)
    camera_worker = _Component("camera", events)
    vision_manager = _Component("vision", events)

    runtime = runtime_mod.ReachyRuntime(
        robot=robot,  # type: ignore[arg-type]
        camera_worker=camera_worker,
        movement_manager=movement_manager,  # type: ignore[arg-type]
        dependencies=object(),  # type: ignore[arg-type]
        head_wobbler=head_wobbler,  # type: ignore[arg-type]
        vision_manager=vision_manager,
        shutdown_delay_seconds=0,
    )

    runtime.start()
    runtime.start()
    runtime.stop()
    runtime.stop()

    assert events == [
        "movement.start",
        "wobbler.start",
        "camera.start",
        "vision.start",
        "movement.stop",
        "wobbler.stop",
        "camera.stop",
        "vision.stop",
        "media.close",
        "client.disconnect",
    ]


def test_start_cleans_up_already_started_components_on_failure() -> None:
    """A partial start should stop components that were already running."""
    events: list[str] = []
    runtime = runtime_mod.ReachyRuntime(
        robot=_Robot(events, {}),  # type: ignore[arg-type]
        camera_worker=_Component("camera", events),
        movement_manager=_MovementManager("movement", events),  # type: ignore[arg-type]
        dependencies=object(),  # type: ignore[arg-type]
        head_wobbler=_Component("wobbler", events, fail_start=True),  # type: ignore[arg-type]
        shutdown_delay_seconds=0,
    )

    with pytest.raises(RuntimeError, match="wobbler failed"):
        runtime.start()

    assert events == [
        "movement.start",
        "wobbler.start",
        "movement.stop",
    ]


def test_stopped_runtime_cannot_restart_disconnected_robot() -> None:
    """Restart should fail after the SDK connection has been closed."""
    events: list[str] = []
    runtime = runtime_mod.ReachyRuntime(
        robot=_Robot(events, {}),  # type: ignore[arg-type]
        camera_worker=None,
        movement_manager=_MovementManager("movement", events),  # type: ignore[arg-type]
        dependencies=object(),  # type: ignore[arg-type]
        head_wobbler=_Component("wobbler", events),  # type: ignore[arg-type]
        shutdown_delay_seconds=0,
    )

    runtime.start()
    runtime.stop()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        runtime.start()
