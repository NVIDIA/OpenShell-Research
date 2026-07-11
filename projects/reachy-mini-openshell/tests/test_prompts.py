"""Tests for locked Realtime session instructions."""

from reachy_mini_conversation_app.prompts import get_session_instructions


def test_camera_instructions_require_explicit_request_and_tool_use() -> None:
    """The prompt should use camera on request without enabling automatic capture."""
    instructions = get_session_instructions()

    assert "explicitly asks Reachy to take a picture or photo" in instructions
    assert "call camera" in instructions
    assert "Do not take pictures unless the human explicitly requests one" in instructions
    assert "Do not offer camera" not in instructions
