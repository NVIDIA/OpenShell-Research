"""Direct, policy-aware Reachy REST tool transport."""

from __future__ import annotations
import math
import base64
import asyncio
from copy import deepcopy
from uuid import UUID
from typing import Any, Final, cast
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


POLICY_DENIED_ERROR: Final = "Blocked by OpenShell policy"
MAX_CAMERA_QUESTION_CHARACTERS: Final = 500
MAX_CAMERA_JPEG_BYTES: Final = 2 * 1024 * 1024

_MOVE_HEAD_SPEC: Final[dict[str, Any]] = {
    "type": "function",
    "name": "move_head",
    "description": (
        "Move Reachy's head through one or more fixed directions in order. "
        "Valid directions are left, right, up, down, and front."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "directions": {
                "type": "array",
                "description": "Ordered directions to perform.",
                "items": {
                    "type": "string",
                    "enum": ["left", "right", "up", "down", "front"],
                },
                "minItems": 1,
                "maxItems": 8,
            }
        },
        "required": ["directions"],
        "additionalProperties": False,
    },
}

_STOP_MOTION_SPEC: Final[dict[str, Any]] = {
    "type": "function",
    "name": "stop_motion",
    "description": "Stop every currently running Reachy movement.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

_CAMERA_SPEC: Final[dict[str, Any]] = {
    "type": "function",
    "name": "camera",
    "description": (
        "Capture one still image from Reachy's camera and answer a question about what is visibly present. "
        "The capture is a separate OpenShell policy-controlled action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "A concise question to answer using the captured image.",
                "minLength": 1,
                "maxLength": MAX_CAMERA_QUESTION_CHARACTERS,
            }
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

_MOTION_TOOL_SPECS: Final[list[dict[str, Any]]] = [_MOVE_HEAD_SPEC, _STOP_MOTION_SPEC]

# REST XYZRPYPose values are expressed in radians. These absolute poses match
# the original local MoveHead tool's fixed direction mapping.
_DIRECTION_POSES: Final[dict[str, tuple[float, float]]] = {
    "left": (0.0, math.radians(40.0)),
    "right": (0.0, math.radians(-40.0)),
    "up": (math.radians(-30.0), 0.0),
    "down": (math.radians(30.0), 0.0),
    "front": (0.0, 0.0),
}


@dataclass(frozen=True)
class RestTransportSettings:
    """Validated settings for direct Reachy REST calls."""

    base_url: str = "http://127.0.0.1:8000"
    camera_base_url: str | None = None
    request_timeout_seconds: float = 5.0
    motion_duration_seconds: float = 1.0
    poll_interval_seconds: float = 0.1
    completion_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        """Reject invalid network and timing settings before tool discovery."""
        self._validate_base_url("REACHY_REST_BASE_URL", self.base_url)
        if self.camera_base_url is not None:
            self._validate_base_url("REACHY_CAMERA_BASE_URL", self.camera_base_url)

        for name, value in (
            ("REACHY_REST_TIMEOUT_SECONDS", self.request_timeout_seconds),
            ("REACHY_MOTION_DURATION_SECONDS", self.motion_duration_seconds),
            ("REACHY_MOTION_POLL_INTERVAL_SECONDS", self.poll_interval_seconds),
            ("REACHY_MOTION_COMPLETION_TIMEOUT_SECONDS", self.completion_timeout_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

    @staticmethod
    def _validate_base_url(name: str, value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{name} must be an absolute HTTP(S) URL")
        if parsed.path not in {"", "/"}:
            raise ValueError(f"{name} must not contain a path")
        if parsed.query or parsed.fragment:
            raise ValueError(f"{name} must not contain a query string or fragment")


class RestToolTransport:
    """Expose a deliberately small Reachy tool set through its daemon REST API."""

    def __init__(
        self,
        settings: RestTransportSettings,
        *,
        client: httpx.AsyncClient | None = None,
        camera_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the transport without probing or moving the robot."""
        self.settings = settings
        self._base_url = settings.base_url.rstrip("/")
        self._camera_base_url = settings.camera_base_url.rstrip("/") if settings.camera_base_url else None
        if camera_client is not None and self._camera_base_url is None:
            raise ValueError("camera_client requires REACHY_CAMERA_BASE_URL")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{settings.base_url.rstrip('/')}/",
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )
        self._owns_camera_client = camera_client is None and self._camera_base_url is not None
        self._camera_client = camera_client
        if self._camera_client is None and self._camera_base_url is not None:
            self._camera_client = httpx.AsyncClient(
                base_url=f"{self._camera_base_url}/",
                timeout=httpx.Timeout(settings.request_timeout_seconds),
                follow_redirects=False,
            )
        self._active_move_ids: set[str] = set()
        self._closed = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return fixed motion schemas plus camera only when its adapter is configured."""
        schemas = [*_MOTION_TOOL_SPECS]
        if self._camera_client is not None:
            schemas.append(_CAMERA_SPEC)
        return deepcopy(schemas)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate model arguments and invoke the corresponding REST operation."""
        if self._closed:
            return {"status": "robot_unavailable", "tool": name, "error": "Reachy REST transport is closed"}
        if name == "move_head":
            return await self._move_head(arguments)
        if name == "stop_motion":
            if arguments:
                return self._invalid_arguments(name, "stop_motion does not accept arguments")
            return await self._stop_motion()
        if name == "camera" and self._camera_client is not None:
            return await self._capture_image(arguments)
        return {"status": "unknown_tool", "tool": name, "error": f"Unknown REST tool: {name}"}

    async def close(self) -> None:
        """Close the owned HTTP client once."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()
        if self._owns_camera_client and self._camera_client is not None and self._camera_client is not self._client:
            await self._camera_client.aclose()

    async def _capture_image(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"question"}:
            return self._invalid_arguments("camera", "camera accepts only the question field")
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            return self._invalid_arguments("camera", "question must be a non-empty string")
        question = question.strip()
        if len(question) > MAX_CAMERA_QUESTION_CHARACTERS:
            return self._invalid_arguments(
                "camera",
                f"question must contain at most {MAX_CAMERA_QUESTION_CHARACTERS} characters",
            )

        assert self._camera_client is not None
        assert self._camera_base_url is not None
        path = "/camera/capture"
        try:
            response = await self._camera_client.post(f"{self._camera_base_url}{path}")
        except httpx.TimeoutException:
            return {
                "status": "unknown_delivery",
                "tool": "camera",
                "error": "Camera request timed out; a picture may have been captured and the request was not retried",
            }
        except httpx.RequestError as exc:
            return {
                "status": "camera_unavailable",
                "tool": "camera",
                "error": f"Reachy camera adapter is unavailable: {type(exc).__name__}",
            }

        error = self._response_error(response, "camera", path)
        if error is not None:
            return error

        jpeg = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "image/jpeg" or not jpeg.startswith(b"\xff\xd8"):
            return {
                "status": "camera_rejected",
                "tool": "camera",
                "error": "Reachy camera adapter returned an invalid JPEG",
            }
        if not jpeg or len(jpeg) > MAX_CAMERA_JPEG_BYTES:
            return {
                "status": "camera_rejected",
                "tool": "camera",
                "error": "Reachy camera adapter returned an image outside the allowed size limit",
            }

        return {
            "status": "captured",
            "tool": "camera",
            "question": question,
            "b64_im": base64.b64encode(jpeg).decode("ascii"),
        }

    async def _move_head(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"directions"}:
            return self._invalid_arguments("move_head", "move_head accepts only the directions field")

        raw_directions = arguments.get("directions")
        if not isinstance(raw_directions, list) or not 1 <= len(raw_directions) <= 8:
            return self._invalid_arguments("move_head", "directions must contain between 1 and 8 values")
        if not all(isinstance(direction, str) and direction in _DIRECTION_POSES for direction in raw_directions):
            return self._invalid_arguments(
                "move_head",
                f"directions must contain only {list(_DIRECTION_POSES)}",
            )

        directions = cast(list[str], list(raw_directions))
        completed: list[str] = []
        try:
            for direction in directions:
                result = await self._goto_and_wait(direction)
                if result.get("status") != "completed":
                    result["directions"] = directions
                    result["completed_directions"] = completed
                    return result
                completed.append(direction)
        except asyncio.CancelledError:
            with suppress(Exception):
                await asyncio.shield(self._stop_motion())
            raise

        return {
            "status": "completed",
            "tool": "move_head",
            "directions": directions,
            "total_duration_seconds": self.settings.motion_duration_seconds * len(directions),
        }

    async def _goto_and_wait(self, direction: str) -> dict[str, Any]:
        pitch, yaw = _DIRECTION_POSES[direction]
        payload = {
            "head_pose": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "roll": 0.0,
                "pitch": pitch,
                "yaw": yaw,
            },
            "duration": self.settings.motion_duration_seconds,
            "interpolation": "minjerk",
        }

        try:
            response = await self._client.post(self._url("/api/move/goto"), json=payload)
        except httpx.TimeoutException:
            return {
                "status": "unknown_delivery",
                "tool": "move_head",
                "direction": direction,
                "error": "Reachy motion request timed out; it was not retried",
            }
        except httpx.RequestError as exc:
            return self._request_error("move_head", exc)

        error = self._response_error(response, "move_head", "/api/move/goto")
        if error is not None:
            return error

        move_id = self._move_id_from_response(response)
        if move_id is None:
            return {
                "status": "robot_rejected",
                "tool": "move_head",
                "direction": direction,
                "error": "Reachy returned an invalid move identifier",
            }

        self._active_move_ids.add(move_id)
        deadline = asyncio.get_running_loop().time() + self.settings.completion_timeout_seconds
        while True:
            running = await self._running_moves("move_head")
            if isinstance(running, dict):
                running["direction"] = direction
                return running
            if move_id not in running:
                self._active_move_ids.discard(move_id)
                return {"status": "completed", "tool": "move_head", "direction": direction, "move_id": move_id}
            if asyncio.get_running_loop().time() >= deadline:
                await self._stop_move_best_effort(move_id)
                return {
                    "status": "motion_timeout",
                    "tool": "move_head",
                    "direction": direction,
                    "move_id": move_id,
                    "error": "Reachy motion did not complete before the safety timeout",
                }
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _stop_motion(self) -> dict[str, Any]:
        running = await self._running_moves("stop_motion")
        if isinstance(running, dict):
            return running

        move_ids = sorted(running | self._active_move_ids)
        stopped: list[str] = []
        for move_id in move_ids:
            try:
                response = await self._client.post(self._url("/api/move/stop"), json={"uuid": move_id})
            except httpx.TimeoutException:
                return {
                    "status": "unknown_delivery",
                    "tool": "stop_motion",
                    "stopped_move_ids": stopped,
                    "error": "Reachy stop request timed out; it was not retried",
                }
            except httpx.RequestError as exc:
                result = self._request_error("stop_motion", exc)
                result["stopped_move_ids"] = stopped
                return result

            error = self._response_error(response, "stop_motion", "/api/move/stop")
            if error is not None:
                error["stopped_move_ids"] = stopped
                return error
            stopped.append(move_id)
            self._active_move_ids.discard(move_id)

        return {"status": "stopped", "tool": "stop_motion", "stopped_move_ids": stopped}

    async def _running_moves(self, tool: str) -> set[str] | dict[str, Any]:
        try:
            response = await self._client.get(self._url("/api/move/running"))
        except httpx.RequestError as exc:
            return self._request_error(tool, exc)

        error = self._response_error(response, tool, "/api/move/running")
        if error is not None:
            return error
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, list):
            return {
                "status": "robot_rejected",
                "tool": tool,
                "error": "Reachy returned an invalid running-moves response",
            }

        move_ids: set[str] = set()
        for item in payload:
            move_id = self._validated_move_id(item.get("uuid") if isinstance(item, dict) else None)
            if move_id is None:
                return {
                    "status": "robot_rejected",
                    "tool": tool,
                    "error": "Reachy returned an invalid running move identifier",
                }
            move_ids.add(move_id)
        return move_ids

    async def _stop_move_best_effort(self, move_id: str) -> None:
        try:
            response = await self._client.post(self._url("/api/move/stop"), json={"uuid": move_id})
            if response.is_success:
                self._active_move_ids.discard(move_id)
        except httpx.RequestError:
            return

    @classmethod
    def _move_id_from_response(cls, response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return cls._validated_move_id(payload.get("uuid") if isinstance(payload, dict) else None)

    @staticmethod
    def _validated_move_id(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return str(UUID(value))
        except ValueError:
            return None

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    @staticmethod
    def _invalid_arguments(tool: str, message: str) -> dict[str, Any]:
        return {"status": "invalid_arguments", "tool": tool, "error": message}

    @staticmethod
    def _request_error(tool: str, exc: httpx.RequestError) -> dict[str, Any]:
        return {
            "status": "robot_unavailable",
            "tool": tool,
            "error": f"Reachy REST API is unavailable: {type(exc).__name__}",
        }

    @staticmethod
    def _response_error(response: httpx.Response, tool: str, path: str) -> dict[str, Any] | None:
        if response.status_code == httpx.codes.FORBIDDEN:
            return {
                "status": "policy_denied",
                "tool": tool,
                "error": f"{POLICY_DENIED_ERROR}: {response.request.method} {path}",
            }
        if response.is_error:
            return {
                "status": "robot_rejected",
                "tool": tool,
                "http_status": response.status_code,
                "error": f"Reachy rejected {response.request.method} {path}",
            }
        return None
