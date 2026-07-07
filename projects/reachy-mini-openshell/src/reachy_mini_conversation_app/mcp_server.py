"""Authenticated MCP boundary for the physical Reachy Mini runtime."""

from __future__ import annotations
import os
import re
import hmac
import math
import asyncio
import logging
import secrets
from typing import Any, Literal, Mapping, Callable, Annotated
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass

import numpy as np
import uvicorn
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.applications import Starlette
from starlette.datastructures import Headers
from mcp.server.fastmcp.exceptions import ToolError

from reachy_mini_conversation_app.tools.dance import Dance
from reachy_mini_conversation_app.tools.camera import Camera
from reachy_mini_conversation_app.robot_runtime import ReachyRuntime
from reachy_mini_conversation_app.tools.move_head import MoveHead
from reachy_mini_conversation_app.tools.play_emotion import PlayEmotion
from reachy_mini_conversation_app.profiles._reachy_mini_conversation_app_locked_profile.scan_scene import (
    MAX_ANALYSIS_FRAMES,
    ScanScene,
)


logger = logging.getLogger(__name__)

Direction = Literal["left", "right", "up", "down", "front"]
Directions = Annotated[list[Direction], Field(min_length=1, max_length=8)]
Question = Annotated[str, Field(min_length=1, max_length=1000)]
RepeatCount = Annotated[int, Field(ge=1, le=2)]

_CAPTURE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _parse_allowlist(raw_value: str) -> frozenset[str]:
    """Parse a comma-separated environment allowlist."""
    return frozenset(value.strip() for value in raw_value.split(",") if value.strip())


@dataclass(frozen=True)
class McpServerSettings:
    """Configuration for the trusted host-side Reachy MCP server."""

    host: str
    port: int
    token: str
    capture_directory: Path
    allowed_emotions: frozenset[str]
    allowed_dances: frozenset[str]
    daemon_host: str = "reachy-mini.local"
    daemon_port: int = 8000
    movement_frequency_hz: float = 50.0
    delivery_timeout_seconds: float = 1.5

    def __post_init__(self) -> None:
        """Reject unsafe or unusable settings before opening the robot connection."""
        if not self.token:
            raise ValueError("REACHY_MCP_TOKEN must be set")
        if not 1 <= self.port <= 65535:
            raise ValueError("REACHY_MCP_PORT must be between 1 and 65535")
        if not 1 <= self.daemon_port <= 65535:
            raise ValueError("REACHY_DAEMON_PORT must be between 1 and 65535")
        if not self.daemon_host:
            raise ValueError("REACHY_DAEMON_HOST must be set")
        if (
            not math.isfinite(self.movement_frequency_hz)
            or self.movement_frequency_hz <= 0
            or self.movement_frequency_hz > 50
        ):
            raise ValueError("REACHY_MCP_MOVEMENT_HZ must be greater than zero and at most 50")
        if not math.isfinite(self.delivery_timeout_seconds) or self.delivery_timeout_seconds <= 0:
            raise ValueError("REACHY_MCP_DELIVERY_TIMEOUT must be positive")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "McpServerSettings":
        """Load settings from process environment variables."""
        values = os.environ if environ is None else environ
        raw_port = values.get("REACHY_MCP_PORT", "8766").strip()
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("REACHY_MCP_PORT must be an integer") from exc
        raw_daemon_port = values.get("REACHY_DAEMON_PORT", "8000").strip()
        try:
            daemon_port = int(raw_daemon_port)
        except ValueError as exc:
            raise ValueError("REACHY_DAEMON_PORT must be an integer") from exc
        try:
            movement_frequency_hz = float(values.get("REACHY_MCP_MOVEMENT_HZ", "50"))
            delivery_timeout_seconds = float(values.get("REACHY_MCP_DELIVERY_TIMEOUT", "1.5"))
        except ValueError as exc:
            raise ValueError("Reachy MCP movement frequency and delivery timeout must be numbers") from exc

        return cls(
            host=values.get("REACHY_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=port,
            token=values.get("REACHY_MCP_TOKEN", "").strip(),
            capture_directory=Path(values.get("REACHY_HOST_CAPTURE_DIR", "./captures")).expanduser().resolve(),
            allowed_emotions=_parse_allowlist(values.get("REACHY_ALLOWED_EMOTIONS", "welcoming1")),
            allowed_dances=_parse_allowlist(values.get("REACHY_ALLOWED_DANCES", "groovy_sway_and_roll")),
            daemon_host=(values.get("REACHY_DAEMON_HOST") or values.get("DAEMON_HOST") or "reachy-mini.local").strip(),
            daemon_port=daemon_port,
            movement_frequency_hz=movement_frequency_hz,
            delivery_timeout_seconds=delivery_timeout_seconds,
        )

    @property
    def public_base_url(self) -> str:
        """Return the host URL reachable from an OpenShell sandbox."""
        return f"http://host.openshell.internal:{self.port}"


class BearerTokenMiddleware:
    """Require one fixed bearer token on the MCP, health, and capture routes."""

    def __init__(self, app: Any, *, token: str) -> None:
        """Store the wrapped ASGI application and expected token."""
        self.app = app
        self.token = token

    @staticmethod
    def _requires_auth(path: str) -> bool:
        return path == "/mcp" or path.startswith("/mcp/") or path == "/healthz" or path.startswith("/captures/")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Reject protected HTTP requests that do not carry the expected token."""
        if scope.get("type") == "http" and self._requires_auth(scope.get("path", "")):
            authorization = Headers(scope=scope).get("authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(authorization, expected):
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class CaptureStore:
    """Map server-generated identifiers to MP4s inside the capture directory."""

    def __init__(self, capture_directory: Path) -> None:
        """Create the capture root and an initially empty identifier registry."""
        self.capture_directory = capture_directory.resolve()
        self.capture_directory.mkdir(parents=True, exist_ok=True)
        self._captures: dict[str, Path] = {}

    def register(self, source_path: str | Path) -> str:
        """Move one completed MP4 to a server-generated safe name and remember it."""
        source = Path(source_path).expanduser().resolve(strict=True)
        if source.parent != self.capture_directory or source.suffix.lower() != ".mp4" or source.is_symlink():
            raise ValueError("scan_scene returned a capture outside the configured MP4 directory")

        while True:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            capture_id = f"reachy-scene-scan-{timestamp}-{secrets.token_hex(4)}"
            target = self.capture_directory / f"{capture_id}.mp4"
            if not target.exists():
                break

        source.replace(target)
        self._captures[capture_id] = target
        return capture_id

    def resolve(self, capture_id: str) -> Path | None:
        """Resolve only a known, safe capture identifier to an existing local MP4."""
        if _CAPTURE_ID_PATTERN.fullmatch(capture_id) is None:
            return None

        path = self._captures.get(capture_id)
        if path is None or path.is_symlink() or not path.is_file():
            return None

        resolved = path.resolve()
        if resolved.parent != self.capture_directory or resolved.suffix.lower() != ".mp4":
            return None
        return resolved


class ReachyMcpService:
    """Validate and serialize the deliberately small Reachy hardware API."""

    def __init__(
        self,
        runtime: ReachyRuntime,
        settings: McpServerSettings,
        runtime_factory: Callable[[], ReachyRuntime] | None = None,
    ) -> None:
        """Bind one model-free physical runtime to the configured safety limits."""
        if (
            runtime.vision_manager is not None
            or runtime.vision_router is not None
            or getattr(runtime.dependencies, "vision_manager", None) is not None
            or getattr(runtime.dependencies, "vision_router", None) is not None
        ):
            raise ValueError("The MCP runtime must not have direct vision model access")

        self.runtime = runtime
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.hardware_lock = asyncio.Lock()
        self.reconnect_lock = asyncio.Lock()
        self.captures = CaptureStore(settings.capture_directory)
        self._move_head = MoveHead()
        self._play_emotion = PlayEmotion()
        self._camera = Camera()
        self._scan_scene = ScanScene()
        self._dance = Dance()

    @staticmethod
    def _raise_for_tool_error(result: dict[str, Any]) -> dict[str, Any]:
        """Translate an existing Python tool error into an MCP tool error."""
        error = result.get("error")
        if error:
            raise ToolError(str(error))
        return result

    async def _ensure_connected(self) -> None:
        """Reconnect before a new command, without retrying an uncertain command."""
        if self.runtime.is_connected:
            return
        if self.runtime_factory is None:
            raise ToolError(self.runtime.connection_error or "Reachy control connection is unavailable")

        async with self.reconnect_lock:
            if self.runtime.is_connected:
                return

            previous = self.runtime
            logger.warning("Reachy control connection is unavailable; rebuilding runtime before the next command")
            await asyncio.to_thread(previous.stop)
            replacement: ReachyRuntime | None = None
            try:
                replacement = await asyncio.to_thread(self.runtime_factory)
                _validate_physical_runtime(replacement)
                await asyncio.to_thread(replacement.start)
            except Exception as exc:
                if replacement is not None:
                    await asyncio.to_thread(replacement.stop)
                raise ToolError(f"Could not reconnect to Reachy: {type(exc).__name__}: {exc}") from exc

            self.runtime = replacement
            logger.info("Reachy runtime reconnected")

    def _delivery_checkpoint(self) -> int | None:
        checkpoint = getattr(self.runtime.movement_manager, "delivery_checkpoint", None)
        return checkpoint() if callable(checkpoint) else None

    async def _confirm_motion_delivery(self, checkpoint: int | None) -> None:
        """Require one successful target send after a motion tool was queued."""
        waiter = getattr(self.runtime.movement_manager, "wait_for_delivery", None)
        if checkpoint is None or not callable(waiter):
            if not self.runtime.is_connected:
                raise ToolError(self.runtime.connection_error or "Reachy control connection was lost")
            return

        delivered, error = await asyncio.to_thread(
            waiter,
            checkpoint,
            self.settings.delivery_timeout_seconds,
        )
        if delivered:
            return

        self.runtime.movement_manager.clear_move_queue()
        raise ToolError(error or "Reachy did not accept the movement target")

    def _motion_snapshot(self) -> np.ndarray[Any, np.dtype[np.float64]] | None:
        """Read cached robot state for post-command physical-motion verification."""
        try:
            head_pose = np.asarray(self.runtime.robot.get_current_head_pose(), dtype=np.float64).reshape(-1)
            head_joints, antennas = self.runtime.robot.get_current_joint_positions()
            return np.concatenate(
                (
                    head_pose,
                    np.asarray(head_joints, dtype=np.float64),
                    np.asarray(antennas, dtype=np.float64),
                )
            )
        except (AttributeError, AssertionError, TypeError, ValueError):
            return None

    async def _observe_motion(self, baseline: np.ndarray[Any, np.dtype[np.float64]] | None) -> bool | None:
        """Observe sensor-state change while also detecting a lost SDK socket."""
        if baseline is None:
            return None

        deadline = asyncio.get_running_loop().time() + self.settings.delivery_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if not self.runtime.is_connected:
                raise ToolError(self.runtime.connection_error or "Reachy control connection was lost")
            current = self._motion_snapshot()
            if current is not None and current.shape == baseline.shape:
                if float(np.max(np.abs(current - baseline))) > 1e-4:
                    return True
            await asyncio.sleep(0.05)
        return False

    async def _finish_motion(
        self,
        checkpoint: int | None,
        observer: asyncio.Task[bool | None] | None,
        *,
        require_observed_motion: bool,
    ) -> bool | None:
        """Confirm target transmission and, when meaningful, physical state change."""
        try:
            await self._confirm_motion_delivery(checkpoint)
            observed = await observer if observer is not None else None
        except BaseException:
            if observer is not None and not observer.done():
                observer.cancel()
            raise

        if observed is False and require_observed_motion:
            self.runtime.movement_manager.clear_move_queue()
            raise ToolError("A target was sent, but no physical Reachy motion was observed")
        return observed

    async def move_head(self, directions: list[Direction]) -> dict[str, Any]:
        """Queue one to eight validated head directions in order."""
        async with self.hardware_lock:
            await self._ensure_connected()
            checkpoint = self._delivery_checkpoint()
            baseline = self._motion_snapshot()
            result = await self._move_head(self.runtime.dependencies, directions=directions)
            result = self._raise_for_tool_error(result)
            observer = asyncio.create_task(self._observe_motion(baseline)) if baseline is not None else None
            observed = await self._finish_motion(
                checkpoint,
                observer,
                require_observed_motion=any(direction != "front" for direction in directions),
            )
        return {**result, "delivery_confirmed": True, "motion_observed": observed}

    async def play_emotion(self, emotion: str) -> dict[str, Any]:
        """Queue a server-approved recorded emotion."""
        emotion = emotion.strip()
        if emotion not in self.settings.allowed_emotions:
            raise ToolError(f"Emotion {emotion!r} is not allowed by this Reachy MCP server")
        async with self.hardware_lock:
            await self._ensure_connected()
            checkpoint = self._delivery_checkpoint()
            baseline = self._motion_snapshot()
            result = await self._play_emotion(self.runtime.dependencies, emotion=emotion)
            result = self._raise_for_tool_error(result)
            observer = asyncio.create_task(self._observe_motion(baseline)) if baseline is not None else None
            observed = await self._finish_motion(checkpoint, observer, require_observed_motion=True)
        return {**result, "delivery_confirmed": True, "motion_observed": observed}

    async def camera(self, question: str) -> dict[str, Any]:
        """Capture one JPEG without invoking a vision model."""
        question = question.strip()
        if not question:
            raise ToolError("question must be a non-empty string")
        async with self.hardware_lock:
            await self._ensure_connected()
            result = await self._camera(self.runtime.dependencies, question=question)
        result = self._raise_for_tool_error(result)
        image = result.get("b64_im")
        if not isinstance(image, str) or not image:
            raise ToolError("camera did not return a JPEG")
        return {
            "status": "image_captured",
            "question": question,
            "b64_im": image,
        }

    async def scan_scene(self, question: str) -> dict[str, Any]:
        """Record a sweep and return a safe capture reference plus sampled frames."""
        question = question.strip()
        if not question:
            raise ToolError("question must be a non-empty string")
        async with self.hardware_lock:
            await self._ensure_connected()
            checkpoint = self._delivery_checkpoint()
            baseline = self._motion_snapshot()
            observer = asyncio.create_task(self._observe_motion(baseline)) if baseline is not None else None
            result = await self._scan_scene(self.runtime.dependencies, question=question)
            try:
                result = self._raise_for_tool_error(result)
                observed = await self._finish_motion(checkpoint, observer, require_observed_motion=True)
            except BaseException:
                if observer is not None and not observer.done():
                    observer.cancel()
                raise

        images = result.get("b64_images")
        if not isinstance(images, list) or not 1 <= len(images) <= MAX_ANALYSIS_FRAMES:
            raise ToolError(f"scan_scene must return between 1 and {MAX_ANALYSIS_FRAMES} frames")
        if not all(isinstance(image, str) and image for image in images):
            raise ToolError("scan_scene returned an invalid image frame")

        video_path = result.pop("video_path", None)
        if not isinstance(video_path, str):
            raise ToolError("scan_scene did not return an MP4 path")
        try:
            capture_id = self.captures.register(video_path)
        except (OSError, ValueError) as exc:
            raise ToolError(f"Could not register scene capture: {exc}") from exc

        result.update(
            {
                "status": "scene_scan_complete",
                "question": question,
                "capture_id": capture_id,
                "video_url": f"{self.settings.public_base_url}/captures/{capture_id}.mp4",
                "delivery_confirmed": True,
                "motion_observed": observed,
            }
        )
        return result

    async def stop_motion(self) -> dict[str, Any]:
        """Clear current and queued motion without stopping the control loop."""
        self.runtime.movement_manager.clear_move_queue()
        return {"status": "motion_cleared"}

    async def dance(self, move: str, repeat: int) -> dict[str, Any]:
        """Queue a server-approved dance with a bounded repeat count."""
        move = move.strip()
        if move not in self.settings.allowed_dances:
            raise ToolError(f"Dance {move!r} is not allowed by this Reachy MCP server")
        if not 1 <= repeat <= 2:
            raise ToolError("repeat must be one or two")
        async with self.hardware_lock:
            await self._ensure_connected()
            checkpoint = self._delivery_checkpoint()
            baseline = self._motion_snapshot()
            result = await self._dance(self.runtime.dependencies, move=move, repeat=repeat)
            result = self._raise_for_tool_error(result)
            observer = asyncio.create_task(self._observe_motion(baseline)) if baseline is not None else None
            observed = await self._finish_motion(checkpoint, observer, require_observed_motion=True)
        return {**result, "delivery_confirmed": True, "motion_observed": observed}

    def health(self) -> dict[str, Any]:
        """Return the server's connected physical-runtime state."""
        connected = self.runtime.is_connected
        return {
            "status": "ok" if connected else "degraded",
            "robot_connected": connected,
            "camera_available": connected and self.runtime.camera_worker is not None,
            "simulation_enabled": self.runtime.is_simulation,
            "connection_error": self.runtime.connection_error,
        }


def _make_tool_arguments_strict(mcp: FastMCP[Any]) -> None:
    """Forbid undeclared arguments in both MCP schemas and Pydantic validation."""
    for tool in mcp._tool_manager.list_tools():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config["extra"] = "forbid"
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)


def create_app(
    runtime: ReachyRuntime,
    settings: McpServerSettings,
    runtime_factory: Callable[[], ReachyRuntime] | None = None,
) -> Starlette:
    """Build the authenticated Streamable HTTP MCP application."""
    service = ReachyMcpService(runtime, settings, runtime_factory)
    mcp = FastMCP(
        "Reachy Mini Hardware",
        instructions="A narrow, authenticated interface to one physical Reachy Mini.",
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(name="move_head", description="Move through one to eight ordered head directions.")
    async def move_head(directions: Directions) -> dict[str, Any]:
        return await service.move_head(directions)

    @mcp.tool(name="play_emotion", description="Play one server-approved Reachy emotion.")
    async def play_emotion(emotion: str) -> dict[str, Any]:
        return await service.play_emotion(emotion)

    @mcp.tool(name="camera", description="Capture one JPEG for analysis by the caller's approved vision model.")
    async def camera(question: Question) -> dict[str, Any]:
        return await service.camera(question)

    @mcp.tool(name="scan_scene", description="Sweep, record an MP4, and return chronological JPEG frames.")
    async def scan_scene(question: Question) -> dict[str, Any]:
        return await service.scan_scene(question)

    @mcp.tool(name="stop_motion", description="Immediately clear active and queued Reachy movement.")
    async def stop_motion() -> dict[str, Any]:
        return await service.stop_motion()

    @mcp.tool(name="dance", description="Play one server-approved dance one or two times.")
    async def dance(move: str, repeat: RepeatCount = 1) -> dict[str, Any]:
        return await service.dance(move, repeat)

    _make_tool_arguments_strict(mcp)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        del request
        health = service.health()
        return JSONResponse(health, status_code=200 if health["robot_connected"] else 503)

    @mcp.custom_route("/captures/{capture_id}.mp4", methods=["GET"])
    async def download_capture(request: Request) -> FileResponse | JSONResponse:
        capture_id = request.path_params["capture_id"]
        path = service.captures.resolve(capture_id)
        if path is None:
            return JSONResponse({"error": "capture not found"}, status_code=404)
        return FileResponse(path, media_type="video/mp4", filename=f"{capture_id}.mp4")

    app = mcp.streamable_http_app()
    app.add_middleware(BearerTokenMiddleware, token=settings.token)
    app.state.reachy_service = service
    app.state.mcp_server = mcp
    return app


def _validate_physical_runtime(runtime: ReachyRuntime) -> None:
    """Refuse to start this trusted service with simulation or without a camera."""
    if runtime.is_simulation:
        raise RuntimeError("The Reachy MCP server requires a physical robot; simulation is not allowed")
    if runtime.camera_worker is None:
        raise RuntimeError("The Reachy MCP server requires an available camera")
    if runtime.vision_manager is not None or runtime.vision_router is not None:
        raise RuntimeError("The Reachy MCP server must capture images without direct model access")


def main() -> None:
    """Connect to the physical robot and serve its authenticated MCP interface."""
    logging.basicConfig(level=logging.INFO)
    try:
        settings = McpServerSettings.from_environment()
    except ValueError as exc:
        raise SystemExit(f"Invalid Reachy MCP configuration: {exc}") from exc

    def build_runtime() -> ReachyRuntime:
        return ReachyRuntime.connect(
            robot_host=settings.daemon_host,
            robot_port=settings.daemon_port,
            connection_mode="network",
            no_camera=False,
            local_vision=False,
            enable_vision_router=False,
            movement_frequency_hz=settings.movement_frequency_hz,
            enable_idle_breathing=False,
            capture_directory=settings.capture_directory,
        )

    runtime: ReachyRuntime | None = None
    app: Starlette | None = None
    try:
        runtime = build_runtime()
        _validate_physical_runtime(runtime)
        runtime.start()
        app = create_app(runtime, settings, build_runtime)
        logger.info("Serving authenticated Reachy MCP at http://%s:%d/mcp", settings.host, settings.port)
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    finally:
        active_runtime = app.state.reachy_service.runtime if app is not None else runtime
        if active_runtime is not None:
            active_runtime.stop()
        if runtime is not None and runtime is not active_runtime:
            runtime.stop()


if __name__ == "__main__":
    main()
