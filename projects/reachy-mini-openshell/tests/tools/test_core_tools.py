"""Tests for profile tool registration and dependency-aware exposure."""

from typing import Any, cast

from reachy_mini_conversation_app.tools.core_tools import (
    ToolDependencies,
    get_tool_specs,
    get_tool_specs_for_dependencies,
)


def _tool_names(specs: list[dict[str, object]]) -> set[object]:
    return {spec.get("name") for spec in specs}


def test_requested_profile_tools_are_registered() -> None:
    """The locked profile exposes the newly enabled tools."""
    names = _tool_names(cast(list[dict[str, object]], get_tool_specs()))

    assert {"camera", "scan_scene", "move_head", "do_nothing"} <= names


def test_camera_tool_requires_a_camera_worker() -> None:
    """Models should not see the camera tool when the app disabled camera support."""
    without_camera = ToolDependencies(
        reachy_mini=cast(Any, None),
        movement_manager=object(),
        camera_worker=None,
    )
    with_camera = ToolDependencies(
        reachy_mini=cast(Any, None),
        movement_manager=object(),
        camera_worker=object(),
    )

    without_names = _tool_names(cast(list[dict[str, object]], get_tool_specs_for_dependencies(without_camera)))
    with_names = _tool_names(cast(list[dict[str, object]], get_tool_specs_for_dependencies(with_camera)))

    assert "camera" not in without_names
    assert "scan_scene" not in without_names
    assert {"move_head", "do_nothing"} <= without_names
    assert "camera" in with_names
    assert "scan_scene" in with_names
