from typing import Any, cast

import pytest

import reachy_mini_conversation_app.tools.play_emotion as play_emotion_mod


def test_play_emotion_schema_does_not_load_emotion_library(monkeypatch: Any) -> None:
    """Building tool specs should not touch the Hugging Face emotion library."""

    def fail_if_called() -> None:
        raise AssertionError("emotion library should not load while building the tool schema")

    monkeypatch.setattr(play_emotion_mod, "_get_recorded_moves", fail_if_called)

    spec = play_emotion_mod.PlayEmotion().spec()

    emotion_description = spec["parameters"]["properties"]["emotion"]["description"]
    assert spec["name"] == "play_emotion"
    assert "Reachy Mini emotions library" in emotion_description


@pytest.mark.asyncio
async def test_play_emotion_reports_lazy_load_failure(monkeypatch: Any) -> None:
    """Emotion load failures should become tool errors, not startup failures."""
    monkeypatch.setattr(play_emotion_mod, "_EMOTION_IMPORT_ERROR", "dataset unavailable")
    monkeypatch.setattr(play_emotion_mod, "_get_recorded_moves", lambda: None)

    result = await play_emotion_mod.PlayEmotion()(cast(Any, object()), emotion="welcoming1")

    assert result == {"error": "Emotion system not available: dataset unavailable"}
