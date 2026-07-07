"""Reusable lifecycle for a Reachy Mini robot and its local workers."""

from __future__ import annotations
import time
import logging
from types import SimpleNamespace
from typing import Any, Callable
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini import ReachyMini
from reachy_mini_conversation_app.moves import MovementManager
from reachy_mini_conversation_app.utils import handle_vision_stuff
from reachy_mini_conversation_app.vision_router import build_vision_router
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.audio.head_wobbler import HeadWobbler


logger = logging.getLogger(__name__)


def shutdown_step(log: Any, name: str, callback: Callable[[], Any]) -> None:
    """Run one shutdown callback without interrupting later cleanup steps."""
    try:
        callback()
    except KeyboardInterrupt:
        log.warning("Shutdown interrupted while stopping %s; continuing cleanup.", name)
    except Exception as exc:
        log.debug("Error while stopping %s: %s", name, exc)


def _status_flag(status: Any, name: str) -> bool:
    """Read a boolean daemon-status field from a mapping or SDK object."""
    if isinstance(status, dict):
        return bool(status.get(name, False))
    return bool(getattr(status, name, False))


@dataclass
class ReachyRuntime:
    """Own the connected robot, its workers, and their coordinated lifecycle."""

    robot: ReachyMini
    camera_worker: Any | None
    movement_manager: MovementManager
    dependencies: ToolDependencies
    head_wobbler: HeadWobbler
    head_tracker: Any | None = None
    vision_manager: Any | None = None
    vision_router: Any | None = None
    simulation_enabled: bool = False
    mockup_sim_enabled: bool = False
    log: Any = logger
    shutdown_delay_seconds: float = 1.0
    _started: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)

    @property
    def is_simulation(self) -> bool:
        """Return whether either supported simulation backend is active."""
        return self.simulation_enabled or self.mockup_sim_enabled

    @classmethod
    def connect(
        cls,
        *,
        robot_name: str | None = None,
        robot_host: str | None = None,
        robot_port: int | None = None,
        connection_mode: str | None = None,
        media_backend: str | None = None,
        robot: ReachyMini | None = None,
        no_camera: bool = False,
        head_tracker: str | None = None,
        local_vision: bool = False,
        enable_vision_router: bool = True,
        movement_frequency_hz: float = 100.0,
        enable_idle_breathing: bool = True,
        capture_directory: Path | None = None,
        log: Any = logger,
        shutdown_delay_seconds: float = 1.0,
    ) -> "ReachyRuntime":
        """Connect to Reachy and construct every worker that depends on it.

        Passing an existing ``robot`` preserves the Reachy Mini Apps integration,
        while callers such as a standalone MCP server can let the runtime create
        its own SDK connection.
        """
        current_robot = robot
        if current_robot is None:
            robot_kwargs: dict[str, Any] = {}
            if robot_name is not None:
                robot_kwargs["robot_name"] = robot_name
            if robot_host is not None:
                robot_kwargs["host"] = robot_host
            if robot_port is not None:
                robot_kwargs["port"] = robot_port
            if connection_mode is not None:
                robot_kwargs["connection_mode"] = connection_mode
            if media_backend is not None:
                robot_kwargs["media_backend"] = media_backend

            log.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            current_robot = ReachyMini(**robot_kwargs)

        status = current_robot.client.get_status()
        simulation_enabled = _status_flag(status, "simulation_enabled")
        mockup_sim_enabled = _status_flag(status, "mockup_sim_enabled")

        vision_args = SimpleNamespace(
            no_camera=no_camera,
            head_tracker=head_tracker,
            local_vision=local_vision,
        )
        camera_worker, initialized_head_tracker, vision_manager = handle_vision_stuff(
            vision_args,
            current_robot,
        )

        vision_router = None
        if enable_vision_router and camera_worker is not None and vision_manager is None:
            vision_router = build_vision_router()

        movement_manager = MovementManager(
            current_robot=current_robot,
            camera_worker=camera_worker,
            target_frequency_hz=movement_frequency_hz,
            enable_idle_breathing=enable_idle_breathing,
        )
        head_wobbler = HeadWobbler(set_speech_offsets=movement_manager.set_speech_offsets)
        dependencies = ToolDependencies(
            reachy_mini=current_robot,
            movement_manager=movement_manager,
            camera_worker=camera_worker,
            vision_manager=vision_manager,
            vision_router=vision_router,
            head_wobbler=head_wobbler,
            capture_directory=(capture_directory or Path("captures")).expanduser(),
        )

        return cls(
            robot=current_robot,
            camera_worker=camera_worker,
            movement_manager=movement_manager,
            dependencies=dependencies,
            head_wobbler=head_wobbler,
            head_tracker=initialized_head_tracker,
            vision_manager=vision_manager,
            vision_router=vision_router,
            simulation_enabled=simulation_enabled,
            mockup_sim_enabled=mockup_sim_enabled,
            log=log,
            shutdown_delay_seconds=shutdown_delay_seconds,
        )

    def start(self) -> None:
        """Start each robot worker once, preserving the app's existing order."""
        if self._started:
            self.log.debug("Reachy runtime already started; start() ignored")
            return
        if self._stopped:
            raise RuntimeError("A stopped Reachy runtime cannot be restarted; create a new runtime")

        started_components: list[tuple[str, Any]] = []
        components = [
            ("movement manager", self.movement_manager),
            ("head wobbler", self.head_wobbler),
            ("camera worker", self.camera_worker),
            ("vision manager", self.vision_manager),
        ]
        try:
            for name, component in components:
                if component is None:
                    continue
                component.start()
                started_components.append((name, component))
        except BaseException:
            for name, component in reversed(started_components):
                shutdown_step(self.log, name, component.stop)
            raise

        self._started = True
        self._stopped = False
        self.log.debug("Reachy runtime started")

    @property
    def is_connected(self) -> bool:
        """Return the live SDK/movement connection state used by hardware tools."""
        if self._stopped:
            return False
        checker = getattr(self.movement_manager, "connection_healthy", None)
        if callable(checker):
            return bool(checker())
        client_alive = getattr(self.robot.client, "_is_alive", None)
        return True if client_alive is None else bool(client_alive)

    @property
    def connection_error(self) -> str | None:
        """Return the movement transport's terminal error when available."""
        return getattr(self.movement_manager, "delivery_error", None)

    def stop(self) -> None:
        """Stop all workers and disconnect the SDK without skipping cleanup."""
        if self._stopped:
            self.log.debug("Reachy runtime already stopped; stop() ignored")
            return

        shutdown_step(self.log, "movement manager", self.movement_manager.stop)
        shutdown_step(self.log, "head wobbler", self.head_wobbler.stop)
        if self.camera_worker is not None:
            shutdown_step(self.log, "camera worker", self.camera_worker.stop)
        if self.vision_manager is not None:
            shutdown_step(self.log, "vision manager", self.vision_manager.stop)

        shutdown_step(self.log, "media", self.robot.media.close)
        shutdown_step(self.log, "robot client", self.robot.client.disconnect)

        if self.shutdown_delay_seconds > 0:
            time.sleep(self.shutdown_delay_seconds)

        self._started = False
        self._stopped = True
        self.log.info("Shutdown complete.")
