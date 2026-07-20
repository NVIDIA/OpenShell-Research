"""Tests for camera media passed through the Chat Completions tool loop."""

import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

import reachy_mini_conversation_app.chat_completions as chat_mod
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.media_result_processor import ProcessedToolResult


@pytest.mark.asyncio
async def test_scene_scan_images_are_sent_as_vision_content(monkeypatch: Any) -> None:
    """Local-STT Chat Completions should analyze frames without embedding them in tool JSON."""
    create_calls: list[dict[str, Any]] = []

    class FakeFunction:
        name = "scan_scene"
        arguments = '{"question":"What did you see?"}'

    class FakeToolCall:
        id = "call_scan"
        type = "function"
        function = FakeFunction()

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeToolMessage:
        content = ""
        tool_calls = [FakeToolCall()]

    class FakeFinalMessage:
        content = "I saw a desk and a person."
        tool_calls: list[Any] = []

    class FakeChoice:
        def __init__(self, message: Any) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: Any) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(FakeToolMessage())
            return FakeCompletion(FakeFinalMessage())

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    async def fake_dispatch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "scene_scan_complete",
            "question": "What did you see?",
            "frame_timestamps_seconds": [0.5, 7.0],
            "b64_images": ["first-jpeg", "second-jpeg"],
        }

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch)
    runner = chat_mod.ChatCompletionRunner(
        client=FakeClient(),
        deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        tool_manager=MagicMock(),
        model_name="vision-chat-model",
        base_url="https://example.test/v1",
    )

    chatbot_messages = await runner.send_text_message("Scan the room and tell me what you saw.")

    followup_messages = create_calls[1]["messages"]
    tool_message = next(message for message in followup_messages if message["role"] == "tool")
    vision_message = next(
        message for message in followup_messages if message["role"] == "user" and isinstance(message["content"], list)
    )

    assert "b64_images" not in json.loads(tool_message["content"])
    assert vision_message["content"][0]["type"] == "text"
    assert "chronological" in vision_message["content"][0]["text"]
    assert vision_message["content"][1:] == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,first-jpeg"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,second-jpeg"},
        },
    ]
    assert "first-jpeg" not in chatbot_messages[1]["content"]
    assert chatbot_messages[-1]["content"] == "I saw a desk and a person."


@pytest.mark.asyncio
async def test_media_processor_keeps_raw_camera_image_out_of_chat_completions(monkeypatch: Any) -> None:
    """Local STT should use routed vision text instead of sending the image to chat."""
    create_calls: list[dict[str, Any]] = []

    class FakeFunction:
        name = "camera"
        arguments = '{"question":"What am I doing?"}'

    class FakeToolCall:
        id = "call_camera"
        type = "function"
        function = FakeFunction()

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "function": {
                    "name": self.function.name,
                    "arguments": self.function.arguments,
                },
            }

    class FakeMessage:
        def __init__(self, content: str, tool_calls: list[Any]) -> None:
            self.content = content
            self.tool_calls = tool_calls

    class FakeChoice:
        def __init__(self, message: FakeMessage) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: FakeMessage) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            create_calls.append(kwargs)
            if len(create_calls) == 1:
                return FakeCompletion(FakeMessage("", [FakeToolCall()]))
            return FakeCompletion(FakeMessage("You are waving.", []))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    class FakeProcessor:
        async def process(self, _tool_name: str, result: dict[str, Any]) -> ProcessedToolResult:
            assert result["b64_im"] == "private-image"
            return ProcessedToolResult(
                model_payload={
                    "status": "image_analyzed",
                    "question": result["question"],
                    "image_description": "The person is waving.",
                    "selected_model": "approved-vision-model",
                }
            )

    async def fake_dispatch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "question": "What am I doing?",
            "b64_im": "private-image",
        }

    monkeypatch.setattr(chat_mod, "dispatch_tool_call_with_manager", fake_dispatch)
    runner = chat_mod.ChatCompletionRunner(
        client=FakeClient(),
        deps=ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        tool_manager=MagicMock(),
        model_name="chat-model",
        base_url="https://example.test/v1",
        media_result_processor=cast(Any, FakeProcessor()),
    )

    chatbot_messages = await runner.send_text_message("Use the camera.")

    followup_messages = create_calls[1]["messages"]
    serialized_messages = json.dumps(followup_messages)
    assert "private-image" not in serialized_messages
    assert "data:image" not in serialized_messages
    assert "The person is waving." in serialized_messages
    assert chatbot_messages[-1]["content"] == "You are waving."
