from __future__ import annotations
import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from reachy_mini_conversation_app.mcp_server import (
    CaptureStore,
    ReachyMcpService,
    McpServerSettings,
    create_app,
    _validate_physical_runtime,
)


class _MovementManager:
    def __init__(self) -> None:
        self.clear_calls = 0
        self.delivery_error: str | None = None
        self.wait_error: str | None = None
        self.delivered = True

    def clear_move_queue(self) -> None:
        self.clear_calls += 1

    def connection_healthy(self) -> bool:
        return self.delivery_error is None

    def delivery_checkpoint(self) -> int:
        return 0

    def wait_for_delivery(self, checkpoint: int, timeout: float) -> tuple[bool, str | None]:
        del checkpoint, timeout
        return self.delivered, self.wait_error


class _Runtime:
    def __init__(self, *, camera: bool = True, simulation: bool = False) -> None:
        self.robot = object()
        self.camera_worker = object() if camera else None
        self.movement_manager = _MovementManager()
        self.dependencies = SimpleNamespace(vision_router=None)
        self.vision_router = None
        self.vision_manager = None
        self.is_simulation = simulation
        self._stopped = False
        self.stop_calls = 0
        self.start_calls = 0

    @property
    def is_connected(self) -> bool:
        return not self._stopped and self.movement_manager.connection_healthy()

    @property
    def connection_error(self) -> str | None:
        return self.movement_manager.delivery_error

    def start(self) -> None:
        self.start_calls += 1
        self._stopped = False

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped = True


class _ResultTool:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
        del dependencies
        self.calls.append(kwargs)
        return dict(self.result)


class _SensorRobot:
    def __init__(self) -> None:
        self.head_pose = np.eye(4)
        self.body_yaw = 0.0

    def get_current_head_pose(self) -> np.ndarray[Any, Any]:
        return self.head_pose.copy()

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        return [self.body_yaw, *([0.0] * 6)], [0.0, 0.0]


def _scan_result(video: Path) -> dict[str, Any]:
    video.write_bytes(b"fake-mp4")
    return {
        "status": "scene_scan_complete",
        "scan_status": "scene_scan_complete",
        "video_path": str(video),
        "duration_seconds": 8.5,
        "frames_recorded": 120,
        "frames_selected": 2,
        "frame_timestamps_seconds": [0.4, 1.3],
        "b64_images": ["image-one", "image-two"],
    }


def _settings(tmp_path: Path, **overrides: Any) -> McpServerSettings:
    values: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": 8766,
        "token": "test-secret",
        "capture_directory": tmp_path,
        "allowed_emotions": frozenset({"welcoming1"}),
        "allowed_dances": frozenset({"groovy_sway_and_roll"}),
        "daemon_host": "reachy-mini.local",
        "daemon_port": 8000,
        "movement_frequency_hz": 50.0,
        "delivery_timeout_seconds": 0.1,
    }
    values.update(overrides)
    return McpServerSettings(**values)


def _mcp_headers(token: str = "test-secret") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _initialize(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers=_mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "reachy-test", "version": "1"},
            },
        },
    )
    assert response.status_code == 200


def _call_tool(client: TestClient, name: str, arguments: dict[str, Any], *, request_id: int = 2) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        headers=_mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def test_settings_load_expected_environment_and_require_token(tmp_path: Path) -> None:
    """Environment parsing should preserve allowlists and fail closed without auth."""
    settings = McpServerSettings.from_environment(
        {
            "REACHY_MCP_HOST": "127.0.0.1",
            "REACHY_MCP_PORT": "9000",
            "REACHY_MCP_TOKEN": "secret",
            "REACHY_HOST_CAPTURE_DIR": str(tmp_path),
            "REACHY_ALLOWED_EMOTIONS": "welcoming1, curious",
            "REACHY_ALLOWED_DANCES": "groovy_sway_and_roll",
            "DAEMON_HOST": "192.168.0.107",
        }
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.capture_directory == tmp_path.resolve()
    assert settings.allowed_emotions == {"welcoming1", "curious"}
    assert settings.daemon_host == "192.168.0.107"
    assert settings.public_base_url == "http://host.openshell.internal:9000"

    with pytest.raises(ValueError, match="REACHY_MCP_TOKEN"):
        McpServerSettings.from_environment({})


def test_http_routes_require_bearer_token_and_report_physical_runtime(tmp_path: Path) -> None:
    """Protected routes should reject bad credentials and expose safe health state."""
    app = create_app(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]

    with TestClient(app) as client:
        missing = client.get("/healthz")
        wrong = client.get("/healthz", headers={"Authorization": "Bearer wrong"})
        mcp_missing = client.post("/mcp", json={})
        health = client.get("/healthz", headers=_mcp_headers())

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert mcp_missing.status_code == 401
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "robot_connected": True,
        "camera_available": True,
        "simulation_enabled": False,
        "connection_error": None,
    }


def test_mcp_advertises_only_six_strict_tool_schemas(tmp_path: Path) -> None:
    """Tool discovery should expose exactly the narrow interface from the tutorial."""
    app = create_app(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]

    with TestClient(app) as client:
        _initialize(client)
        response = client.post(
            "/mcp",
            headers=_mcp_headers(),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert set(tools) == {"move_head", "play_emotion", "camera", "scan_scene", "stop_motion", "dance"}
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools.values())
    assert tools["camera"]["inputSchema"]["properties"] == {
        "question": {
            "maxLength": 1000,
            "minLength": 1,
            "title": "Question",
            "type": "string",
        }
    }
    directions = tools["move_head"]["inputSchema"]["properties"]["directions"]
    assert directions["minItems"] == 1
    assert directions["maxItems"] == 8
    assert directions["items"]["enum"] == ["left", "right", "up", "down", "front"]


def test_camera_returns_raw_image_and_rejects_undeclared_model_argument(tmp_path: Path) -> None:
    """Camera should return JPEG bytes and provide no model-selection escape hatch."""
    app = create_app(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]
    camera = _ResultTool({"b64_im": "jpeg-base64", "question": "ignored"})
    app.state.reachy_service._camera = camera

    with TestClient(app) as client:
        _initialize(client)
        result = _call_tool(client, "camera", {"question": " What am I doing? "})
        rejected = _call_tool(
            client,
            "camera",
            {"question": "What am I doing?", "requested_model": "gpt-4o"},
            request_id=3,
        )

    assert result["isError"] is False
    assert result["structuredContent"] == {
        "status": "image_captured",
        "question": "What am I doing?",
        "b64_im": "jpeg-base64",
    }
    assert camera.calls == [{"question": "What am I doing?"}]
    assert rejected["isError"] is True
    assert "Extra inputs are not permitted" in rejected["content"][0]["text"]


def test_scan_registers_and_serves_only_known_capture_ids(tmp_path: Path) -> None:
    """Scene scans should replace host paths with authenticated download references."""
    video = tmp_path / "temporary-scan.mp4"
    video.write_bytes(b"fake-mp4")
    app = create_app(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]
    app.state.reachy_service._scan_scene = _ResultTool(
        {
            "status": "scene_scan_complete",
            "video_path": str(video),
            "duration_seconds": 8.5,
            "frames_recorded": 120,
            "frames_selected": 2,
            "frame_timestamps_seconds": [0.4, 1.3],
            "b64_images": ["image-one", "image-two"],
        }
    )

    with TestClient(app) as client:
        _initialize(client)
        result = _call_tool(client, "scan_scene", {"question": "List what you see"})
        payload = result["structuredContent"]
        capture_id = payload["capture_id"]
        download = client.get(f"/captures/{capture_id}.mp4", headers=_mcp_headers())
        missing = client.get("/captures/not-known.mp4", headers=_mcp_headers())
        traversal = client.get("/captures/%2e%2e%2fsecret.mp4", headers=_mcp_headers())
        unauthenticated = client.get(f"/captures/{capture_id}.mp4")

    assert result["isError"] is False
    assert "video_path" not in payload
    assert payload["video_url"] == (f"http://host.openshell.internal:8766/captures/{capture_id}.mp4")
    assert payload["b64_images"] == ["image-one", "image-two"]
    assert download.status_code == 200
    assert download.headers["content-type"] == "video/mp4"
    assert download.content == b"fake-mp4"
    assert missing.status_code == 404
    assert traversal.status_code == 404
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_scan_reports_complete_only_after_front_pose_is_verified(tmp_path: Path) -> None:
    """A successful sweep must finish at absolute front before reporting completion."""
    runtime = _Runtime()
    sensor_robot = _SensorRobot()
    runtime.robot = sensor_robot
    service = ReachyMcpService(runtime, _settings(tmp_path))  # type: ignore[arg-type]

    class SuccessfulScan(_ResultTool):
        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            sensor_robot.body_yaw = 0.4
            await asyncio.sleep(0.01)
            sensor_robot.body_yaw = 0.0
            return await super().__call__(dependencies, **kwargs)

    service._scan_scene = SuccessfulScan(_scan_result(tmp_path / "successful-scan.mp4"))

    result = await service.scan_scene("What did you see?")

    assert result["status"] == "scene_scan_complete"
    assert result["scan_status"] == "scene_scan_complete"
    assert result["returned_to_front"] is True
    assert result["front_verified"] is True
    assert result["front_recovery_commanded"] is False
    assert result["runtime_reconnected"] is False
    assert result["motion_observed"] is True
    assert "scan_warning" not in result


@pytest.mark.asyncio
async def test_interrupted_scan_reconnects_and_returns_front_inside_same_tool_call(tmp_path: Path) -> None:
    """A dropped sweep should preserve media and perform a bounded internal front recovery."""
    disconnected = _Runtime()
    disconnected_sensor = _SensorRobot()
    disconnected.robot = disconnected_sensor
    replacement = _Runtime()
    replacement_sensor = _SensorRobot()
    replacement_sensor.body_yaw = -0.7
    replacement.robot = replacement_sensor

    builds = 0

    def build_runtime() -> _Runtime:
        nonlocal builds
        builds += 1
        return replacement

    service = ReachyMcpService(
        disconnected,  # type: ignore[arg-type]
        _settings(tmp_path),
        build_runtime,  # type: ignore[arg-type]
    )

    class InterruptedScan(_ResultTool):
        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            disconnected_sensor.body_yaw = -0.7
            disconnected.movement_manager.delivery_error = "Lost connection with the server"
            result = await super().__call__(dependencies, **kwargs)
            result["status"] = "scene_scan_incomplete"
            result["scan_status"] = "scene_scan_incomplete"
            result["scan_warning"] = "Reachy lost its control connection before returning to front"
            return result

    class FrontRecovery(_ResultTool):
        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            replacement_sensor.body_yaw = 0.0
            replacement_sensor.head_pose = np.eye(4)
            return await super().__call__(dependencies, **kwargs)

    service._scan_scene = InterruptedScan(_scan_result(tmp_path / "interrupted-scan.mp4"))
    front_recovery = FrontRecovery({"status": "queued"})
    service._move_head = front_recovery

    result = await service.scan_scene("What did you see?")

    assert result["status"] == "scene_scan_incomplete"
    assert result["scan_status"] == "scene_scan_incomplete"
    assert result["returned_to_front"] is True
    assert result["front_verified"] is True
    assert result["front_recovery_commanded"] is True
    assert result["runtime_reconnected"] is True
    assert result["capture_id"]
    assert "video_path" not in result
    assert "Lost connection" in result["scan_warning"]
    assert front_recovery.calls == [{"directions": ["front"]}]
    assert builds == 1
    assert disconnected.stop_calls == 1
    assert replacement.start_calls == 1


@pytest.mark.asyncio
async def test_interrupted_scan_preserves_recording_when_front_recovery_fails(tmp_path: Path) -> None:
    """A failed reconnect should return partial media with an explicit unsafe final-pose state."""
    disconnected = _Runtime()
    disconnected.robot = _SensorRobot()
    service = ReachyMcpService(disconnected, _settings(tmp_path))  # type: ignore[arg-type]

    class InterruptedScan(_ResultTool):
        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            disconnected.movement_manager.delivery_error = "socket closed"
            return await super().__call__(dependencies, **kwargs)

    service._scan_scene = InterruptedScan(_scan_result(tmp_path / "unrecovered-scan.mp4"))

    result = await service.scan_scene("What did you see?")

    assert result["status"] == "scene_scan_incomplete"
    assert result["returned_to_front"] is False
    assert result["front_verified"] is False
    assert result["runtime_reconnected"] is False
    assert result["front_recovery_commanded"] is False
    assert result["capture_id"]
    assert "socket closed" in result["recovery_error"]
    assert "socket closed" in result["scan_warning"]


def test_capture_store_rejects_paths_outside_capture_directory(tmp_path: Path) -> None:
    """Capture registration and lookup should fail closed on unsafe paths and IDs."""
    capture_directory = tmp_path / "captures"
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not-allowed")
    store = CaptureStore(capture_directory)

    with pytest.raises(ValueError, match="outside"):
        store.register(outside)

    assert store.resolve("../outside") is None
    assert store.resolve("not/valid") is None
    assert store.resolve("unknown") is None


@pytest.mark.asyncio
async def test_allowlists_block_actions_before_existing_tools_are_called(tmp_path: Path) -> None:
    """Server argument policy should run before emotion or dance implementations."""
    service = ReachyMcpService(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]
    emotion = _ResultTool({"status": "queued"})
    dance = _ResultTool({"status": "queued"})
    service._play_emotion = emotion
    service._dance = dance

    with pytest.raises(ToolError, match="not allowed"):
        await service.play_emotion("angry")
    with pytest.raises(ToolError, match="not allowed"):
        await service.dance("dizzy_spin", 1)

    assert emotion.calls == []
    assert dance.calls == []


@pytest.mark.asyncio
async def test_hardware_calls_are_serialized_and_stop_motion_bypasses_lock(tmp_path: Path) -> None:
    """Hardware calls should serialize while emergency queue clearing remains prompt."""
    service = ReachyMcpService(_Runtime(), _settings(tmp_path))  # type: ignore[arg-type]

    class _ConcurrentTool:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            del dependencies, kwargs
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {"status": "queued"}

    tool = _ConcurrentTool()
    service._move_head = tool
    await asyncio.gather(service.move_head(["up"]), service.move_head(["right"]))
    assert tool.max_active == 1

    await service.hardware_lock.acquire()
    try:
        result = await asyncio.wait_for(service.stop_motion(), timeout=0.1)
    finally:
        service.hardware_lock.release()

    assert result == {"status": "motion_cleared"}
    assert service.runtime.movement_manager.clear_calls == 1


@pytest.mark.asyncio
async def test_motion_delivery_failure_is_reported_instead_of_false_success(tmp_path: Path) -> None:
    """A dead control socket should turn a queued motion into an MCP error."""
    runtime = _Runtime()
    runtime.movement_manager.delivered = False
    runtime.movement_manager.wait_error = "Lost connection with the server"
    service = ReachyMcpService(runtime, _settings(tmp_path))  # type: ignore[arg-type]
    service._move_head = _ResultTool({"status": "queued"})

    with pytest.raises(ToolError, match="Lost connection"):
        await service.move_head(["left"])

    assert runtime.movement_manager.clear_calls == 1


@pytest.mark.asyncio
async def test_move_head_requires_observed_physical_state_change(tmp_path: Path) -> None:
    """A socket write alone should not be reported as successful physical motion."""
    runtime = _Runtime()
    sensor_robot = _SensorRobot()
    runtime.robot = sensor_robot
    service = ReachyMcpService(
        runtime,  # type: ignore[arg-type]
        _settings(tmp_path, delivery_timeout_seconds=0.05),
    )
    service._move_head = _ResultTool({"status": "queued"})

    with pytest.raises(ToolError, match="no physical Reachy motion"):
        await service.move_head(["left"])

    class _MovingTool(_ResultTool):
        async def __call__(self, dependencies: Any, **kwargs: Any) -> dict[str, Any]:
            sensor_robot.head_pose[0, 3] += 0.01
            return await super().__call__(dependencies, **kwargs)

    service._move_head = _MovingTool({"status": "queued"})
    result = await service.move_head(["left"])

    assert result["delivery_confirmed"] is True
    assert result["motion_observed"] is True


@pytest.mark.asyncio
async def test_next_request_rebuilds_a_disconnected_runtime_once(tmp_path: Path) -> None:
    """Reconnect before a new command but never retry a command after it was queued."""
    disconnected = _Runtime()
    disconnected.movement_manager.delivery_error = "socket closed"
    replacement = _Runtime()
    builds = 0

    def build_runtime() -> _Runtime:
        nonlocal builds
        builds += 1
        return replacement

    service = ReachyMcpService(
        disconnected,  # type: ignore[arg-type]
        _settings(tmp_path),
        build_runtime,  # type: ignore[arg-type]
    )
    service._move_head = _ResultTool({"status": "queued"})

    result = await service.move_head(["left"])

    assert result == {
        "status": "queued",
        "delivery_confirmed": True,
        "motion_observed": None,
    }
    assert builds == 1
    assert disconnected.stop_calls == 1
    assert replacement.start_calls == 1
    assert service.runtime is replacement


def test_disconnected_health_is_degraded(tmp_path: Path) -> None:
    """The HTTP health route should reflect the actual movement connection."""
    runtime = _Runtime()
    runtime.movement_manager.delivery_error = "socket closed"
    app = create_app(runtime, _settings(tmp_path))  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/healthz", headers=_mcp_headers())

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "robot_connected": False,
        "camera_available": False,
        "simulation_enabled": False,
        "connection_error": "socket closed",
    }


def test_physical_runtime_validation_rejects_simulation_camera_and_model_access(tmp_path: Path) -> None:
    """Startup should fail closed unless the runtime is physical and model-free."""
    with pytest.raises(RuntimeError, match="physical robot"):
        _validate_physical_runtime(_Runtime(simulation=True))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="camera"):
        _validate_physical_runtime(_Runtime(camera=False))  # type: ignore[arg-type]

    routed = _Runtime()
    routed.vision_router = object()
    with pytest.raises(RuntimeError, match="without direct model access"):
        _validate_physical_runtime(routed)  # type: ignore[arg-type]

    local_vision = _Runtime()
    local_vision.vision_manager = object()
    with pytest.raises(ValueError, match="direct vision model access"):
        ReachyMcpService(local_vision, _settings(tmp_path))  # type: ignore[arg-type]
