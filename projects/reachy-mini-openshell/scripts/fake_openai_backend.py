#!/usr/bin/env python3
"""Tiny OpenAI-compatible backend for local Reachy conversation smoke tests."""

import io
import math
import time
import wave
import argparse
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response


def _wav_bytes(text: str, *, sample_rate: int = 24_000, duration_seconds: float = 0.7) -> bytes:
    """Return a small mono WAV payload."""
    frequency = 523.25 if "swept" in text.lower() else 440.0
    frame_count = max(1, int(sample_rate * duration_seconds))
    frames = bytearray()
    for index in range(frame_count):
        sample = int(math.sin(2 * math.pi * frequency * index / sample_rate) * 6000)
        frames.extend(sample.to_bytes(2, byteorder="little", signed=True))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))
    return buffer.getvalue()


def _message_has_tool_result(messages: list[Any]) -> bool:
    """Return whether a Chat Completions request includes a tool result."""
    return any(isinstance(message, dict) and message.get("role") == "tool" for message in messages)


def build_app(
    *,
    transcript: str,
    assistant_text: str,
    tool_name: str,
    call_tool: bool,
    audio_duration_seconds: float,
) -> FastAPI:
    """Build the fake OpenAI-compatible FastAPI app."""
    app = FastAPI(title="Reachy fake OpenAI-compatible backend")

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": "fake-whisper", "object": "model"},
                {"id": "fake-chat", "object": "model"},
                {"id": "fake-tts", "object": "model"},
            ],
        }

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> dict[str, str]:
        await request.body()
        return {"text": transcript}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        input_text = str(payload.get("input", "hello"))
        return Response(
            content=_wav_bytes(input_text, duration_seconds=audio_duration_seconds),
            media_type="audio/wav",
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> dict[str, Any]:
        payload = await request.json()
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        if call_tool and not _message_has_tool_result(messages):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_fake_reachy_tool",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": "{}"},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": assistant_text}
            finish_reason = "stop"

        return {
            "id": "chatcmpl-fake-reachy",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model", "fake-chat"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        }

    return app


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description="Run a fake OpenAI-compatible backend for Reachy smoke tests.")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind.")
    parser.add_argument("--port", type=int, default=8766, help="port to bind.")
    parser.add_argument(
        "--transcript",
        default="Reachy, use the sweep_look tool, then tell me what you did.",
        help="transcription text returned by POST /v1/audio/transcriptions.",
    )
    parser.add_argument(
        "--assistant-text",
        default="I swept my gaze and returned to center.",
        help="final assistant text returned after a tool result.",
    )
    parser.add_argument("--tool-name", default="sweep_look", help="tool name to request from Chat Completions.")
    parser.add_argument(
        "--no-tool", action="store_true", help="return assistant text immediately without a tool call."
    )
    parser.add_argument("--audio-duration", type=float, default=0.7, help="duration in seconds for fake TTS WAVs.")
    return parser


def main() -> None:
    """Run the fake backend."""
    args = build_parser().parse_args()
    app = build_app(
        transcript=args.transcript,
        assistant_text=args.assistant_text,
        tool_name=args.tool_name,
        call_tool=not args.no_tool,
        audio_duration_seconds=args.audio_duration,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
