"""Backend configuration and endpoint smoke checks for the conversation app."""

import asyncio
import argparse
from typing import Any, cast
from pathlib import Path

import numpy as np
from openai import AsyncOpenAI
from numpy.typing import NDArray

from reachy_mini_conversation_app.config import (
    config,
    load_dotenv_file,
    loaded_dotenv_keys,
    loaded_dotenv_path,
)
from reachy_mini_conversation_app.audio.pcm import (
    wav_bytes,
    read_wav_audio,
    wav_duration_seconds,
    prepare_mono_int16_audio,
)
from reachy_mini_conversation_app.backend_runtime import (
    selected_backend,
    backend_config_error,
    describe_selected_backend,
    local_stt_tts_config_error,
    local_stt_stage_config_error,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.local_stt_backend import LocalSTTBackend
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


def _required(value: str | None, name: str) -> str:
    """Return a required string config value or raise a clear error."""
    if value and value.strip():
        return value.strip()
    raise RuntimeError(f"{name} is required")


def _backend_config_hint(error: str) -> str | None:
    """Return an actionable hint for common backend config errors."""
    if not error.startswith("BACKEND_PROVIDER is missing"):
        return None

    keys = loaded_dotenv_keys()
    legacy_keys = {"MODEL_NAME", "OPENAI_BASE_URL"} & keys
    loaded_path = loaded_dotenv_path()
    if legacy_keys:
        legacy_list = ", ".join(sorted(legacy_keys))
        location = f" in {loaded_path}" if loaded_path else ""
        return (
            f"Found older config keys{location}: {legacy_list}. They are no longer used. "
            "Choose one BACKEND_PROVIDER and set the matching new keys: OPENAI_REALTIME_* for "
            "openai_realtime, HF_REALTIME_* for hf_realtime, or CHAT_*/STT_*/TTS_* for local_stt."
        )

    if loaded_path is None:
        return (
            "No .env file was loaded. Copy .env.example to .env, set BACKEND_PROVIDER to "
            "openai_realtime, hf_realtime, or local_stt, or pass --env-file."
        )

    return (
        f"Loaded config from {loaded_path}. Set BACKEND_PROVIDER there to "
        "openai_realtime, hf_realtime, or local_stt, or pass --env-file."
    )


def _wav_duration_seconds(wav_payload: bytes) -> float:
    """Return the duration of a WAV payload."""
    return wav_duration_seconds(wav_payload)


def _audio_duration_seconds(sample_rate: int, audio_frame: NDArray[np.int16]) -> float:
    """Return the duration of a mono/channel-first int16 frame."""
    if sample_rate <= 0:
        return 0.0
    sample_count = audio_frame.shape[-1] if audio_frame.ndim > 1 else len(audio_frame)
    return sample_count / float(sample_rate)


def _read_wav_audio(wav_payload: bytes) -> tuple[int, NDArray[np.int16]]:
    """Decode a WAV payload into mono int16 audio."""
    return read_wav_audio(wav_payload)


def _audio_chunks(
    audio_frame: NDArray[np.int16], sample_rate: int, *, chunk_ms: float = 100.0
) -> list[NDArray[np.int16]]:
    """Split audio into chunks that resemble browser microphone frames."""
    chunk_size = max(1, int(sample_rate * chunk_ms / 1000.0))
    return [audio_frame[start : start + chunk_size] for start in range(0, len(audio_frame), chunk_size)]


def _silence_frame(sample_rate: int, silence_ms: float) -> NDArray[np.int16]:
    """Return a mono silence frame long enough to end the mic phrase."""
    sample_count = max(1, int(sample_rate * silence_ms / 1000.0))
    return np.zeros(sample_count, dtype=np.int16)


def _tone_wav_bytes() -> bytes:
    """Return a tiny placeholder WAV when an endpoint needs valid audio bytes."""
    sample_rate = 16_000
    t = np.linspace(0, 0.25, int(sample_rate * 0.25), endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 2000).astype(np.int16)
    return wav_bytes(samples, sample_rate)


def _speech_endpoint_error(component: str, context: str, endpoint_path: str, exc: Exception) -> str:
    """Return a concise endpoint failure message without exposing secrets."""
    return (
        f"{component} endpoint request failed ({context}). Check the base URL and that the service exposes "
        f"{endpoint_path}. {type(exc).__name__}: {exc}"
    )


def _model_ids(models_response: Any) -> list[str]:
    """Extract model IDs from common OpenAI-compatible /models response shapes."""
    data = (
        models_response.get("data", []) if isinstance(models_response, dict) else getattr(models_response, "data", [])
    )
    ids: list[str] = []
    for model in data or []:
        model_id = model.get("id") if isinstance(model, dict) else getattr(model, "id", None)
        if isinstance(model_id, str) and model_id.strip():
            ids.append(model_id.strip())
    return sorted(set(ids))


def _format_model_ids(model_ids: list[str], *, limit: int = 5) -> str:
    """Return a compact non-secret model list for diagnostics."""
    if not model_ids:
        return "<none>"
    visible = model_ids[:limit]
    suffix = f", ... (+{len(model_ids) - limit} more)" if len(model_ids) > limit else ""
    return ", ".join(visible) + suffix


async def _stt_models_diagnostic_lines() -> list[str]:
    """Return diagnostic lines for the STT /models endpoint when available."""
    _required(config.STT_MODEL_NAME, "STT_MODEL_NAME")
    client = AsyncOpenAI(
        api_key=(config.STT_API_KEY or "not-needed").strip() or "not-needed",
        base_url=config.STT_BASE_URL,
    )
    try:
        models_response = await client.models.list()
    except Exception as exc:
        return [f"stt_models_endpoint=unavailable ({type(exc).__name__}: {exc})"]

    ids = _model_ids(models_response)
    model_listed = config.STT_MODEL_NAME in ids
    return [
        "stt_models_endpoint=reachable",
        f"stt_model_listed={'yes' if model_listed else 'no'}",
        f"stt_available_models={_format_model_ids(ids)}",
    ]


def _build_fake_local_stt_backend() -> tuple[LocalSTTBackend, BackgroundToolManager, Any]:
    """Build local-STT backend dependencies for CLI smoke checks."""
    movement_manager = _FakeMovementManager()
    deps = ToolDependencies(
        reachy_mini=cast(Any, _FakeReachyMini()),
        movement_manager=movement_manager,
    )
    tool_manager = BackgroundToolManager()
    return (
        LocalSTTBackend(
            deps=deps,
            tool_manager=tool_manager,
            client_factory=AsyncOpenAI,
        ),
        tool_manager,
        movement_manager,
    )


def _chat_summary_from_messages(messages: list[dict[str, Any]], *, require_tool: bool) -> tuple[str, list[str]]:
    """Return final assistant text and tool result titles from chatbot messages."""
    assistant_text = ""
    tool_titles: list[str] = []

    for message in messages:
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, str):
            continue
        metadata = message.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("title"), str):
            tool_titles.append(metadata["title"])
        elif content.startswith("[error]"):
            raise RuntimeError(content)
        else:
            assistant_text = content

    if not assistant_text:
        raise RuntimeError("Chat endpoint returned no final assistant response")
    if require_tool and not tool_titles:
        raise RuntimeError("Chat check did not execute a Reachy tool")

    return assistant_text, tool_titles


async def live_realtime_session_check() -> list[str]:
    """Exercise the selected realtime backend's session startup path."""
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.conversation_stream import ConversationStreamHandler

    movement_manager = _FakeMovementManager()
    deps = ToolDependencies(
        reachy_mini=cast(Any, _FakeReachyMini()),
        movement_manager=movement_manager,
    )
    handler = ConversationStreamHandler(deps)

    try:
        backend = selected_backend()
        connected = await handler._ensure_realtime_session("backend-check-realtime-session")
        if not connected:
            startup_error = getattr(handler, "_startup_error", None)
            raise RuntimeError(startup_error or "Realtime session did not connect before the startup timeout")

        return [
            "realtime_session=connected",
            f"realtime_model={backend.realtime_model or '<backend default>'}",
            f"realtime_voice={backend.realtime_voice or '<backend default>'}",
        ]
    finally:
        await handler.shutdown()
        startup_task = getattr(handler, "_realtime_startup_task", None)
        if startup_task is not None:
            try:
                await asyncio.wait_for(startup_task, timeout=2.0)
            except asyncio.TimeoutError:
                startup_task.cancel()
                try:
                    await startup_task
                except asyncio.CancelledError:
                    pass


def _stage_name(args: argparse.Namespace) -> str:
    """Return the selected live-check stage, preserving --app-flow as a shortcut."""
    stage = getattr(args, "stage", None)
    if stage:
        return str(stage)
    if getattr(args, "app_flow", False):
        return "app-flow"
    if getattr(args, "live", False) and selected_backend().uses_local_stt:
        return "app-flow"
    return "chain"


def _require_tool_usage_error(args: argparse.Namespace, stage: str) -> str | None:
    """Return an error when --require-tool would otherwise be ignored."""
    if not getattr(args, "require_tool", False):
        return None
    if not getattr(args, "live", False):
        return "--require-tool only applies when --live is set."
    if not selected_backend().uses_local_stt:
        return "--require-tool is only supported for BACKEND_PROVIDER=local_stt --stage chat or --stage app-flow."
    if stage not in {"chat", "app-flow"}:
        return "--require-tool only applies to --stage chat or --stage app-flow."
    return None


def _real_reachy_usage_error(args: argparse.Namespace, stage: str) -> str | None:
    """Return an error when --real-reachy would otherwise be ignored."""
    if not getattr(args, "real_reachy", False):
        return None
    if not getattr(args, "live", False) or not selected_backend().uses_local_stt or stage != "app-flow":
        return "--real-reachy only applies to BACKEND_PROVIDER=local_stt with --live --stage app-flow."
    return None


async def _synthesize_input_audio(seed_text: str) -> bytes:
    """Use the configured TTS endpoint to generate a speech sample for STT."""
    _required(config.TTS_MODEL_NAME, "TTS_MODEL_NAME")
    _required(config.TTS_VOICE, "TTS_VOICE")
    backend, tool_manager, _movement_manager = _build_fake_local_stt_backend()
    try:
        try:
            return await backend.synthesize_speech_wav_bytes(seed_text)
        except Exception as exc:
            raise RuntimeError(_speech_endpoint_error("TTS", backend.tts_context, "POST /audio/speech", exc)) from exc
    finally:
        await tool_manager.shutdown()


async def live_local_stt_probe_check() -> list[str]:
    """Probe the local_stt STT endpoint with synthetic audio without requiring recognized text."""
    _required(config.STT_MODEL_NAME, "STT_MODEL_NAME")
    model_lines = await _stt_models_diagnostic_lines()
    if "stt_model_listed=no" in model_lines:
        raise RuntimeError("; ".join(model_lines))

    input_audio = _tone_wav_bytes()
    backend, tool_manager, _movement_manager = _build_fake_local_stt_backend()
    try:
        try:
            transcript = await backend.transcribe_wav_bytes(
                input_audio,
                filename="backend-check-probe.wav",
            )
        except Exception as exc:
            raise RuntimeError(
                "; ".join(
                    [
                        *model_lines,
                        _speech_endpoint_error("STT", backend.stt_context, "POST /audio/transcriptions", exc),
                    ]
                )
            ) from exc
    finally:
        await tool_manager.shutdown()

    return [
        *model_lines,
        "input_audio=synthetic probe tone",
        f"input_audio_duration={_wav_duration_seconds(input_audio):.2f}s",
        "stt_endpoint=reachable",
        f"transcript={transcript or '<empty>'}",
    ]


async def live_local_stt_transcription_check(seed_text: str, audio_file: Path | None) -> list[str]:
    """Exercise only the local_stt STT endpoint."""
    if audio_file is not None:
        input_audio = audio_file.read_bytes()
        input_label = str(audio_file)
    else:
        tts_error = local_stt_tts_config_error()
        if tts_error:
            raise RuntimeError("--stage stt requires --audio-file unless TTS is configured to synthesize seed audio")
        input_audio = await _synthesize_input_audio(seed_text)
        input_label = "configured TTS seed"

    _required(config.STT_MODEL_NAME, "STT_MODEL_NAME")
    backend, tool_manager, _movement_manager = _build_fake_local_stt_backend()
    try:
        try:
            transcript = await backend.transcribe_wav_bytes(
                input_audio or _tone_wav_bytes(),
                filename="backend-check.wav",
            )
        except Exception as exc:
            raise RuntimeError(
                _speech_endpoint_error("STT", backend.stt_context, "POST /audio/transcriptions", exc)
            ) from exc
    finally:
        await tool_manager.shutdown()
    if not transcript:
        raise RuntimeError("STT endpoint returned an empty transcript")

    return [
        f"input_audio={input_label}",
        f"input_audio_duration={_wav_duration_seconds(input_audio):.2f}s",
        f"transcript={transcript}",
    ]


async def live_local_stt_chat_check(seed_text: str, *, require_tool: bool) -> list[str]:
    """Exercise only the local_stt Chat Completions and Reachy tool path."""
    backend, tool_manager, movement_manager = _build_fake_local_stt_backend()
    try:
        _required(config.CHAT_MODEL_NAME, "CHAT_MODEL_NAME")
        messages = await backend.send_text_message(seed_text)
        assistant_text, tool_titles = _chat_summary_from_messages(messages, require_tool=require_tool)
    finally:
        await tool_manager.shutdown()

    return [
        f"seed_text={seed_text}",
        f"assistant_text={assistant_text}",
        f"tool_results={', '.join(tool_titles) if tool_titles else '<none>'}",
        f"queued_moves={len(movement_manager.queued_moves)}",
    ]


async def live_local_stt_tts_check(seed_text: str) -> list[str]:
    """Exercise only the local_stt TTS endpoint."""
    output_audio = await _synthesize_input_audio(seed_text)
    return [
        f"seed_text={seed_text}",
        f"output_audio_duration={_wav_duration_seconds(output_audio):.2f}s",
    ]


async def live_local_stt_check(seed_text: str, audio_file: Path | None) -> list[str]:
    """Exercise local_stt endpoints without connecting to a Reachy daemon."""
    if audio_file is not None:
        input_audio = audio_file.read_bytes()
        input_label = str(audio_file)
    else:
        input_audio = await _synthesize_input_audio(seed_text)
        input_label = "configured TTS seed"

    _required(config.STT_MODEL_NAME, "STT_MODEL_NAME")
    backend, tool_manager, _movement_manager = _build_fake_local_stt_backend()
    try:
        try:
            transcript = await backend.transcribe_wav_bytes(
                input_audio or _tone_wav_bytes(),
                filename="backend-check.wav",
            )
        except Exception as exc:
            raise RuntimeError(
                _speech_endpoint_error("STT", backend.stt_context, "POST /audio/transcriptions", exc)
            ) from exc
        if not transcript:
            raise RuntimeError("STT endpoint returned an empty transcript")

        _required(config.CHAT_MODEL_NAME, "CHAT_MODEL_NAME")
        messages = await backend.send_text_message(transcript)
        assistant_text, _tool_titles = _chat_summary_from_messages(messages, require_tool=False)
        output_audio = await backend.synthesize_speech_wav_bytes(assistant_text)
    finally:
        await tool_manager.shutdown()

    return [
        f"input_audio={input_label}",
        f"input_audio_duration={_wav_duration_seconds(input_audio):.2f}s",
        f"transcript={transcript}",
        f"assistant_text={assistant_text}",
        f"output_audio_duration={_wav_duration_seconds(output_audio):.2f}s",
    ]


class _FakeReachyMini:
    """Small Reachy stand-in for app-flow backend checks."""

    def get_current_head_pose(self) -> NDArray[np.float64]:
        return np.eye(4, dtype=np.float64)

    def get_current_joint_positions(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return np.array([0.0], dtype=np.float64), np.array([0.0, 0.0], dtype=np.float64)


class _FakeMovementManager:
    """Movement manager stand-in that records queued Reachy moves."""

    def __init__(self) -> None:
        self.queued_moves: list[Any] = []
        self.clear_count = 0
        self.moving_state_durations: list[float] = []

    def clear_move_queue(self) -> None:
        self.clear_count += 1
        self.queued_moves.clear()

    def queue_move(self, move: Any) -> None:
        self.queued_moves.append(move)

    def set_moving_state(self, duration: float) -> None:
        self.moving_state_durations.append(duration)

    def is_idle(self) -> bool:
        return True


class _AppFlowDeps:
    """Dependencies and cleanup hooks for app-flow checks."""

    def __init__(self, reachy_mini: Any, movement_manager: Any, *, real_reachy: bool = False):
        self.reachy_mini = reachy_mini
        self.movement_manager = movement_manager
        self.real_reachy = real_reachy

    def start(self) -> None:
        if self.real_reachy:
            self.movement_manager.start()

    def movement_summary_lines(self) -> list[str]:
        if not self.real_reachy:
            return [f"queued_moves={len(self.movement_manager.queued_moves)}"]

        status = self.movement_manager.get_status()
        loop_frequency = status.get("loop_frequency", {}) if isinstance(status, dict) else {}
        return [
            "movement_manager=real",
            f"movement_queue_size={status.get('queue_size', '<unknown>') if isinstance(status, dict) else '<unknown>'}",
            f"movement_loop_samples={loop_frequency.get('samples', '<unknown>')}",
        ]

    def stop(self) -> None:
        if self.real_reachy:
            self.movement_manager.stop()
            try:
                self.reachy_mini.client.disconnect()
            except Exception:
                pass


def _build_app_flow_deps(*, real_reachy: bool, robot_name: str | None) -> _AppFlowDeps:
    """Build fake or real Reachy dependencies for app-flow checks."""
    if not real_reachy:
        movement_manager = _FakeMovementManager()
        return _AppFlowDeps(
            reachy_mini=cast(Any, _FakeReachyMini()),
            movement_manager=movement_manager,
            real_reachy=False,
        )

    from reachy_mini import ReachyMini
    from reachy_mini_conversation_app.moves import MovementManager

    robot_kwargs = {"robot_name": robot_name} if robot_name else {}
    reachy_mini = ReachyMini(**robot_kwargs)
    movement_manager = MovementManager(current_robot=reachy_mini)
    return _AppFlowDeps(reachy_mini=reachy_mini, movement_manager=movement_manager, real_reachy=True)


async def live_local_stt_app_flow_check(
    seed_text: str,
    audio_file: Path | None,
    *,
    require_tool: bool,
    real_reachy: bool = False,
    robot_name: str | None = None,
) -> list[str]:
    """Exercise the app handler's mic -> local_stt STT -> chat/tools -> TTS flow."""
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.conversation_stream import (
        MIC_TRANSCRIPTION_SAMPLE_RATE,
        ConversationStreamHandler,
    )

    if audio_file is not None:
        input_audio = audio_file.read_bytes()
        input_label = str(audio_file)
    else:
        input_audio = await _synthesize_input_audio(seed_text)
        input_label = "configured TTS seed"

    sample_rate, input_frame = _read_wav_audio(input_audio or _tone_wav_bytes())
    prepared_frame = prepare_mono_int16_audio(
        (sample_rate, input_frame),
        MIC_TRANSCRIPTION_SAMPLE_RATE,
    )

    app_flow_deps = _build_app_flow_deps(real_reachy=real_reachy, robot_name=robot_name)
    app_flow_deps.start()
    deps = ToolDependencies(reachy_mini=app_flow_deps.reachy_mini, movement_manager=app_flow_deps.movement_manager)
    handler = ConversationStreamHandler(deps)

    transcript = ""
    assistant_text = ""
    tool_titles: list[str] = []
    error_messages: list[str] = []
    output_audio_duration = 0.0
    try:
        for chunk in _audio_chunks(prepared_frame, MIC_TRANSCRIPTION_SAMPLE_RATE):
            await handler.receive((MIC_TRANSCRIPTION_SAMPLE_RATE, chunk))

        silence_ms = max(config.MIC_TRANSCRIPTION_SILENCE_MS + 50.0, 1.0)
        await handler.receive(
            (MIC_TRANSCRIPTION_SAMPLE_RATE, _silence_frame(MIC_TRANSCRIPTION_SAMPLE_RATE, silence_ms))
        )

        await _wait_for_app_flow_mic_tasks(handler)

        while not handler.output_queue.empty():
            output = handler.output_queue.get_nowait()
            if isinstance(output, tuple):
                output_sample_rate, audio_frame = output
                if isinstance(output_sample_rate, int) and isinstance(audio_frame, np.ndarray):
                    output_audio_duration += _audio_duration_seconds(
                        output_sample_rate,
                        cast(NDArray[np.int16], np.asarray(audio_frame, dtype=np.int16)),
                    )
                continue

            for message in getattr(output, "args", ()):
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if message.get("role") == "user" and isinstance(content, str):
                    transcript = content
                elif message.get("role") == "assistant" and isinstance(content, str):
                    metadata = message.get("metadata")
                    if isinstance(metadata, dict) and isinstance(metadata.get("title"), str):
                        tool_titles.append(metadata["title"])
                        if real_reachy:
                            await asyncio.sleep(0.2)
                    elif content.startswith("[error]"):
                        error_messages.append(content)
                    elif not content.startswith("[error]"):
                        assistant_text = content
    finally:
        await handler.shutdown()
        app_flow_deps.stop()

    if error_messages:
        raise RuntimeError("; ".join(error_messages))
    if not transcript:
        raise RuntimeError("App-flow check did not produce a transcript")
    if not assistant_text:
        raise RuntimeError("App-flow check did not produce a final assistant response")
    if output_audio_duration <= 0:
        raise RuntimeError("App-flow check did not produce synthesized audio")
    if require_tool and not tool_titles:
        raise RuntimeError("App-flow check did not execute a Reachy tool")

    return [
        f"input_audio={input_label}",
        f"input_audio_duration={_wav_duration_seconds(input_audio):.2f}s",
        f"transcript={transcript}",
        f"assistant_text={assistant_text}",
        f"tool_results={', '.join(tool_titles) if tool_titles else '<none>'}",
        *app_flow_deps.movement_summary_lines(),
        f"output_audio_duration={output_audio_duration:.2f}s",
    ]


async def _wait_for_app_flow_mic_tasks(handler: Any) -> None:
    """Wait for the handler's microphone transcription task to finish."""
    for _ in range(50):
        tasks = list(getattr(handler, "_mic_transcription_tasks", ()))
        if tasks:
            await asyncio.gather(*tasks)
            return
        if not handler.output_queue.empty():
            return
        await asyncio.sleep(0.05)

    raise RuntimeError("App-flow check did not flush microphone audio through the receive path")


async def run_check(args: argparse.Namespace) -> int:
    """Run the selected backend check."""
    env_file = getattr(args, "env_file", None)
    if env_file is not None and not load_dotenv_file(env_file):
        print(f"[error] Could not load env file: {env_file}")
        return 2

    stage = _stage_name(args)

    for line in describe_selected_backend():
        print(line)

    error = (
        local_stt_stage_config_error(stage)
        if getattr(args, "live", False) and selected_backend().uses_local_stt
        else backend_config_error()
    )
    if error:
        print(f"[error] {error}")
        hint = _backend_config_hint(error)
        if hint:
            print(f"[hint] {hint}")
        return 2

    require_tool_error = _require_tool_usage_error(args, stage)
    if require_tool_error:
        print(f"[error] {require_tool_error}")
        return 2

    real_reachy_error = _real_reachy_usage_error(args, stage)
    if real_reachy_error:
        print(f"[error] {real_reachy_error}")
        return 2

    print(
        f"[ok] local_stt {stage} config is valid"
        if getattr(args, "live", False) and selected_backend().uses_local_stt
        else "[ok] backend config is valid"
    )

    if not args.live:
        return 0

    backend = selected_backend()
    if not backend.uses_local_stt:
        if not backend.uses_realtime:
            print("[error] --live supports realtime backends and BACKEND_PROVIDER=local_stt")
            return 2

        try:
            results = await live_realtime_session_check()
        except Exception as e:
            print(f"[error] realtime live session check failed: {type(e).__name__}: {e}")
            return 1

        for line in results:
            print(line)
        print("[ok] realtime live session check completed")
        return 0

    try:
        if stage == "stt-probe":
            results = await live_local_stt_probe_check()
        elif stage == "stt":
            results = await live_local_stt_transcription_check(args.seed_text, args.audio_file)
        elif stage == "chat":
            results = await live_local_stt_chat_check(
                args.seed_text,
                require_tool=getattr(args, "require_tool", False),
            )
        elif stage == "tts":
            results = await live_local_stt_tts_check(args.seed_text)
        elif stage == "app-flow":
            results = await live_local_stt_app_flow_check(
                args.seed_text,
                args.audio_file,
                require_tool=getattr(args, "require_tool", False),
                real_reachy=getattr(args, "real_reachy", False),
                robot_name=getattr(args, "robot_name", None),
            )
        else:
            results = await live_local_stt_check(args.seed_text, args.audio_file)
    except Exception as e:
        print(f"[error] local_stt live {stage} check failed: {type(e).__name__}: {e}")
        return 1

    for line in results:
        print(line)
    print(f"[ok] local_stt live {stage} check completed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the backend check argument parser."""
    parser = argparse.ArgumentParser(description="Validate Reachy conversation backend configuration.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="call configured endpoints. Realtime backends validate session startup; local_stt can validate each stage.",
    )
    parser.add_argument(
        "--seed-text",
        default="Hi Reachy, please introduce yourself in one short sentence.",
        help="text to synthesize as the input speech sample when --live is used without --audio-file.",
    )
    parser.add_argument(
        "--audio-file",
        type=Path,
        help="WAV or provider-supported audio file to send to STT instead of synthesizing seed text.",
    )
    parser.add_argument(
        "--stage",
        choices=["stt-probe", "stt", "chat", "tts", "chain", "app-flow"],
        default=None,
        help=(
            "with --live and BACKEND_PROVIDER=local_stt, check one stage. "
            "Defaults to app-flow for local_stt live checks and is ignored by realtime backends."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="load this dotenv file for backend validation instead of the default .env search.",
    )
    parser.add_argument(
        "--app-flow",
        action="store_true",
        help="shortcut for --stage app-flow.",
    )
    parser.add_argument(
        "--require-tool",
        action="store_true",
        help="with --live --stage chat or --live --stage app-flow, fail unless the model executes a Reachy tool.",
    )
    parser.add_argument(
        "--real-reachy",
        action="store_true",
        help="with --live --stage app-flow, use the real Reachy SDK/daemon instead of fake tool dependencies.",
    )
    parser.add_argument(
        "--robot-name",
        help="optional Reachy daemon robot name/prefix, used with --real-reachy.",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_check(args)))


if __name__ == "__main__":
    main()
