"""Tests for the fixed Reachy daemon REST transport."""

from __future__ import annotations
import json
import math
import base64
from typing import Any

import httpx
import pytest

from reachy_mini_conversation_app.rest_tool_transport import RestToolTransport, RestTransportSettings


MOVE_1 = "11111111-1111-4111-8111-111111111111"
MOVE_2 = "22222222-2222-4222-8222-222222222222"


def _settings(**overrides: Any) -> RestTransportSettings:
    values = {
        "base_url": "http://reachy.test:8000",
        "request_timeout_seconds": 1.0,
        "motion_duration_seconds": 1.0,
        "poll_interval_seconds": 0.001,
        "completion_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return RestTransportSettings(**values)


def _json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


@pytest.mark.asyncio
async def test_rest_transport_advertises_only_fixed_motion_tools() -> None:
    """REST discovery should expose only immutable motion and stop schemas."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)))
    transport = RestToolTransport(_settings(), client=client)

    first = await transport.list_tools()
    first[0]["parameters"]["properties"]["directions"]["items"]["enum"].append("raw_pose")
    second = await transport.list_tools()

    assert [tool["name"] for tool in second] == ["move_head", "stop_motion"]
    assert second[0]["parameters"]["additionalProperties"] is False
    assert second[0]["parameters"]["properties"]["directions"]["items"]["enum"] == [
        "left",
        "right",
        "up",
        "down",
        "front",
    ]
    assert second[1]["parameters"] == {"type": "object", "properties": {}, "additionalProperties": False}
    await client.aclose()


@pytest.mark.asyncio
async def test_configured_camera_tool_captures_one_bounded_jpeg() -> None:
    """Camera discovery and capture should use only the fixed adapter endpoint."""
    jpeg = b"\xff\xd8camera-frame\xff\xd9"
    camera_requests: list[httpx.Request] = []

    def camera_handler(request: httpx.Request) -> httpx.Response:
        camera_requests.append(request)
        return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"}, request=request)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))) as client,
        httpx.AsyncClient(transport=httpx.MockTransport(camera_handler)) as camera_client,
    ):
        transport = RestToolTransport(
            _settings(camera_base_url="http://camera.test:8042"),
            client=client,
            camera_client=camera_client,
        )
        tools = await transport.list_tools()
        result = await transport.call_tool("camera", {"question": "What is in front of me?"})

    assert [tool["name"] for tool in tools] == ["move_head", "stop_motion", "camera"]
    assert [(request.method, request.url.path, request.content) for request in camera_requests] == [
        ("POST", "/camera/capture", b""),
    ]
    assert result == {
        "status": "captured",
        "tool": "camera",
        "question": "What is in front of me?",
        "b64_im": base64.b64encode(jpeg).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_camera_policy_denial_and_invalid_arguments_are_model_visible() -> None:
    """Camera policy denial should be explicit and invalid arguments must not reach the adapter."""
    request_count = 0

    def camera_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(403, json={"error": "denied"}, request=request)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))) as client,
        httpx.AsyncClient(transport=httpx.MockTransport(camera_handler)) as camera_client,
    ):
        transport = RestToolTransport(
            _settings(camera_base_url="http://camera.test:8042"),
            client=client,
            camera_client=camera_client,
        )
        denied = await transport.call_tool("camera", {"question": "What do you see?"})
        empty = await transport.call_tool("camera", {"question": " "})
        extra = await transport.call_tool("camera", {"question": "What?", "filename": "/tmp/x"})

    assert denied == {
        "status": "policy_denied",
        "tool": "camera",
        "error": "Blocked by OpenShell policy: POST /camera/capture",
    }
    assert empty["status"] == "invalid_arguments"
    assert extra["status"] == "invalid_arguments"
    assert request_count == 1


@pytest.mark.asyncio
async def test_move_head_maps_directions_to_fixed_rest_poses_in_order() -> None:
    """Directions should become fixed head-only poses sent sequentially."""
    requests: list[httpx.Request] = []
    goto_ids = iter((MOVE_1, MOVE_2))
    current_move: list[str | None] = [None]
    poll_counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/move/goto":
            move_id = next(goto_ids)
            current_move[0] = move_id
            return httpx.Response(200, json={"uuid": move_id}, request=request)
        if request.method == "GET" and request.url.path == "/api/move/running":
            move_id = current_move[0]
            assert move_id is not None
            poll_counts[move_id] = poll_counts.get(move_id, 0) + 1
            if poll_counts[move_id] == 1:
                return httpx.Response(200, json=[{"uuid": move_id}], request=request)
            current_move[0] = None
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = RestToolTransport(_settings(), client=client)
        result = await transport.call_tool("move_head", {"directions": ["up", "right"]})

    goto_requests = [request for request in requests if request.url.path == "/api/move/goto"]
    assert result == {
        "status": "completed",
        "tool": "move_head",
        "directions": ["up", "right"],
        "total_duration_seconds": 2.0,
    }
    assert len(goto_requests) == 2
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/move/goto"),
        ("GET", "/api/move/running"),
        ("GET", "/api/move/running"),
        ("POST", "/api/move/goto"),
        ("GET", "/api/move/running"),
        ("GET", "/api/move/running"),
    ]
    up = _json_body(goto_requests[0])
    right = _json_body(goto_requests[1])
    assert up == {
        "head_pose": {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "roll": 0.0,
            "pitch": pytest.approx(math.radians(-30.0)),
            "yaw": 0.0,
        },
        "duration": 1.0,
        "interpolation": "minjerk",
    }
    assert right["head_pose"]["pitch"] == 0.0
    assert right["head_pose"]["yaw"] == pytest.approx(math.radians(-40.0))
    assert "antennas" not in up
    assert "body_yaw" not in up


@pytest.mark.asyncio
async def test_move_head_rejects_raw_or_invalid_arguments_without_network_access() -> None:
    """Raw pose fields and invalid direction lists must fail before networking."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = RestToolTransport(_settings(), client=client)
        raw_pose = await transport.call_tool("move_head", {"directions": ["up"], "pitch": -2.0})
        invalid_direction = await transport.call_tool("move_head", {"directions": ["backward"]})
        too_many = await transport.call_tool("move_head", {"directions": ["up"] * 9})

    assert raw_pose["status"] == "invalid_arguments"
    assert invalid_direction["status"] == "invalid_arguments"
    assert too_many["status"] == "invalid_arguments"
    assert request_count == 0


@pytest.mark.asyncio
async def test_open_shell_forbidden_response_becomes_policy_denial() -> None:
    """An OpenShell HTTP 403 should remain visible to the conversation model."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "denied"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = RestToolTransport(_settings(), client=client)
        result = await transport.call_tool("move_head", {"directions": ["up"]})

    assert result == {
        "status": "policy_denied",
        "tool": "move_head",
        "error": "Blocked by OpenShell policy: POST /api/move/goto",
        "directions": ["up"],
        "completed_directions": [],
    }


@pytest.mark.asyncio
async def test_motion_post_timeout_is_uncertain_and_is_not_retried() -> None:
    """A timed-out motion POST must not be repeated after uncertain delivery."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("timeout after send", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = RestToolTransport(_settings(), client=client)
        result = await transport.call_tool("move_head", {"directions": ["left"]})

    assert result["status"] == "unknown_delivery"
    assert result["completed_directions"] == []
    assert request_count == 1


@pytest.mark.asyncio
async def test_stop_motion_lists_and_stops_every_running_move() -> None:
    """The stop tool should stop all daemon-reported move identifiers."""
    stopped: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/move/running":
            return httpx.Response(200, json=[{"uuid": MOVE_2}, {"uuid": MOVE_1}], request=request)
        if request.method == "POST" and request.url.path == "/api/move/stop":
            stopped.append(_json_body(request)["uuid"])
            return httpx.Response(200, json={"status": "stopped"}, request=request)
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = RestToolTransport(_settings(), client=client)
        result = await transport.call_tool("stop_motion", {})

    assert result == {"status": "stopped", "tool": "stop_motion", "stopped_move_ids": [MOVE_1, MOVE_2]}
    assert stopped == [MOVE_1, MOVE_2]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_url": "reachy.test:8000"}, "absolute HTTP"),
        ({"camera_base_url": "http://camera.test:8042/capture"}, "REACHY_CAMERA_BASE_URL"),
        ({"request_timeout_seconds": 0.0}, "REACHY_REST_TIMEOUT_SECONDS"),
        ({"motion_duration_seconds": float("nan")}, "REACHY_MOTION_DURATION_SECONDS"),
        ({"poll_interval_seconds": -1.0}, "REACHY_MOTION_POLL_INTERVAL_SECONDS"),
        ({"completion_timeout_seconds": 0.0}, "REACHY_MOTION_COMPLETION_TIMEOUT_SECONDS"),
    ],
)
def test_rest_settings_reject_invalid_values(overrides: dict[str, Any], message: str) -> None:
    """Invalid URLs and timing values should fail before transport creation."""
    with pytest.raises(ValueError, match=message):
        _settings(**overrides)
