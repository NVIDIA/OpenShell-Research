import os
from typing import Any
from argparse import Namespace

import numpy as np
import pytest

import reachy_mini_conversation_app.config as config_mod
import reachy_mini_conversation_app.backend_check as backend_check
import reachy_mini_conversation_app.backend_runtime as runtime_mod
import reachy_mini_conversation_app.conversation_stream as stream_mod
from reachy_mini_conversation_app.audio.pcm import wav_bytes


def _set_local_stt_config(monkeypatch: Any) -> None:
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_LOCAL_STT)
    monkeypatch.setattr(config_mod.config, "CHAT_API_KEY", "chat-key")
    monkeypatch.setattr(config_mod.config, "CHAT_BASE_URL", "https://chat.test/v1")
    monkeypatch.setattr(config_mod.config, "CHAT_MODEL_NAME", "test-chat-model")
    monkeypatch.setattr(config_mod.config, "STT_API_KEY", "not-needed")
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "https://stt.test/v1")
    monkeypatch.setattr(config_mod.config, "STT_MODEL_NAME", "whisper-large-v3")
    monkeypatch.setattr(config_mod.config, "TTS_API_KEY", "tts-key")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "https://tts.test/v1")
    monkeypatch.setattr(config_mod.config, "TTS_MODEL_NAME", "test-tts-model")
    monkeypatch.setattr(config_mod.config, "TTS_VOICE", "cedar")


def test_describe_backend_masks_secret_values(monkeypatch: Any) -> None:
    """Backend summaries should be useful without printing credentials."""
    _set_local_stt_config(monkeypatch)

    summary = runtime_mod.describe_selected_backend()

    assert "backend=local_stt" in summary
    assert "chat.api_key=configured" in summary
    assert all("chat-key" not in line for line in summary)
    assert "stt.base_url=https://stt.test/v1" in summary


def test_describe_backend_reports_global_openai_key_as_configured(monkeypatch: Any) -> None:
    """OpenAI Realtime summaries should include the standard OpenAI key fallback."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_VOICE", "cedar")
    monkeypatch.setitem(config_mod._ORIGINAL_PROCESS_ENV, "OPENAI_API_KEY", "global-openai-key")

    summary = runtime_mod.describe_selected_backend()

    assert "openai_realtime.api_key=configured" in summary
    assert all("global-openai-key" not in line for line in summary)


@pytest.mark.asyncio
async def test_run_check_reports_valid_dry_run(monkeypatch: Any, capsys: Any) -> None:
    """Dry-run mode validates config without calling external endpoints."""
    _set_local_stt_config(monkeypatch)

    exit_code = await backend_check.run_check(
        Namespace(live=False, seed_text="hello", audio_file=None, app_flow=False, require_tool=False),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] backend config is valid" in output
    assert "backend=local_stt" in output


@pytest.mark.asyncio
async def test_run_check_reports_config_error(monkeypatch: Any, capsys: Any) -> None:
    """Dry-run mode returns a nonzero status when required config is missing."""
    _set_local_stt_config(monkeypatch)
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")

    exit_code = await backend_check.run_check(
        Namespace(live=False, seed_text="hello", audio_file=None, app_flow=False, require_tool=False),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[error] TTS_BASE_URL is missing for BACKEND_PROVIDER=local_stt." in output


@pytest.mark.asyncio
async def test_run_check_rejects_ignored_require_tool(monkeypatch: Any, capsys: Any) -> None:
    """--require-tool should fail clearly when the selected check cannot prove tool usage."""
    _set_local_stt_config(monkeypatch)

    exit_code = await backend_check.run_check(
        Namespace(live=True, stage="tts", seed_text="hello", audio_file=None, app_flow=False, require_tool=True),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[error] --require-tool only applies to --stage chat or --stage app-flow." in output


@pytest.mark.asyncio
async def test_run_check_rejects_ignored_real_reachy(monkeypatch: Any, capsys: Any) -> None:
    """--real-reachy should fail clearly unless it can exercise the app-flow path."""
    _set_local_stt_config(monkeypatch)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            stage="tts",
            seed_text="hello",
            audio_file=None,
            app_flow=False,
            require_tool=False,
            real_reachy=True,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[error] --real-reachy only applies to BACKEND_PROVIDER=local_stt with --live --stage app-flow." in output


def test_app_flow_deps_start_stop_and_summarize_real_reachy() -> None:
    """Real app-flow deps should start/stop the movement worker and disconnect the SDK client."""

    class FakeMovementManager:
        started = False
        stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def get_status(self) -> dict[str, Any]:
            return {"queue_size": 2, "loop_frequency": {"samples": 7}}

    class FakeClient:
        disconnected = False

        def disconnect(self) -> None:
            self.disconnected = True

    class FakeReachy:
        client = FakeClient()

    movement_manager = FakeMovementManager()
    reachy = FakeReachy()
    deps = backend_check._AppFlowDeps(reachy, movement_manager, real_reachy=True)

    deps.start()
    assert movement_manager.started is True
    assert deps.movement_summary_lines() == [
        "movement_manager=real",
        "movement_queue_size=2",
        "movement_loop_samples=7",
    ]

    deps.stop()
    assert movement_manager.stopped is True
    assert reachy.client.disconnected is True


@pytest.mark.asyncio
async def test_run_check_live_app_flow_can_use_real_reachy_dependencies(monkeypatch: Any, capsys: Any) -> None:
    """--real-reachy should be passed through to the app-flow live check."""
    _set_local_stt_config(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_live_local_stt_app_flow_check(
        seed_text: str,
        audio_file: Any,
        *,
        require_tool: bool,
        real_reachy: bool = False,
        robot_name: str | None = None,
    ) -> list[str]:
        captured.update(
            {
                "seed_text": seed_text,
                "audio_file": audio_file,
                "require_tool": require_tool,
                "real_reachy": real_reachy,
                "robot_name": robot_name,
            }
        )
        return ["movement_manager=real"]

    monkeypatch.setattr(backend_check, "live_local_stt_app_flow_check", fake_live_local_stt_app_flow_check)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="app-flow",
            require_tool=True,
            real_reachy=True,
            robot_name="reachy_mini",
            seed_text="look around",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured == {
        "seed_text": "look around",
        "audio_file": None,
        "require_tool": True,
        "real_reachy": True,
        "robot_name": "reachy_mini",
    }
    assert "movement_manager=real" in output
    assert "[ok] local_stt live app-flow check completed" in output


@pytest.mark.asyncio
async def test_run_check_live_local_stt_defaults_to_app_flow(monkeypatch: Any, capsys: Any) -> None:
    """The default local-STT live check should exercise the app handler path."""
    _set_local_stt_config(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_live_local_stt_app_flow_check(
        seed_text: str,
        audio_file: Any,
        *,
        require_tool: bool,
        real_reachy: bool = False,
        robot_name: str | None = None,
    ) -> list[str]:
        captured.update(
            {
                "seed_text": seed_text,
                "audio_file": audio_file,
                "require_tool": require_tool,
                "real_reachy": real_reachy,
                "robot_name": robot_name,
            }
        )
        return ["transcript=hello", "assistant_text=hi", "output_audio_duration=0.10s"]

    monkeypatch.setattr(backend_check, "live_local_stt_app_flow_check", fake_live_local_stt_app_flow_check)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage=None,
            require_tool=False,
            real_reachy=False,
            robot_name=None,
            seed_text="hello",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured == {
        "seed_text": "hello",
        "audio_file": None,
        "require_tool": False,
        "real_reachy": False,
        "robot_name": None,
    }
    assert "[ok] local_stt app-flow config is valid" in output
    assert "[ok] local_stt live app-flow check completed" in output


@pytest.mark.asyncio
async def test_run_check_requires_backend_provider(monkeypatch: Any, capsys: Any) -> None:
    """Backend preflight should fail when the single backend selector is absent."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "")
    monkeypatch.setattr(config_mod, "_dotenv_path", "")

    exit_code = await backend_check.run_check(
        Namespace(live=False, seed_text="hello", audio_file=None, app_flow=False, require_tool=False),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "backend=" in output
    assert "[error] BACKEND_PROVIDER is missing; set it to one of hf_realtime, local_stt, openai_realtime." in output
    assert "[hint] No .env file was loaded." in output


@pytest.mark.asyncio
async def test_run_check_requires_backend_provider_points_at_loaded_env(monkeypatch: Any, capsys: Any) -> None:
    """Backend preflight should point at the loaded config file when the selector is absent."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "")
    monkeypatch.setattr(config_mod, "_dotenv_path", "/tmp/reachy-test.env")
    monkeypatch.setattr(config_mod, "_dotenv_values", {})

    exit_code = await backend_check.run_check(
        Namespace(live=False, seed_text="hello", audio_file=None, app_flow=False, require_tool=False),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[hint] Loaded config from /tmp/reachy-test.env. Set BACKEND_PROVIDER there" in output


@pytest.mark.asyncio
async def test_run_check_detects_older_config_keys(monkeypatch: Any, capsys: Any) -> None:
    """Backend preflight should identify stale config files without honoring old keys."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "")
    monkeypatch.setattr(config_mod, "_dotenv_path", "/tmp/reachy-old.env")
    monkeypatch.setattr(
        config_mod,
        "_dotenv_values",
        {
            "OPENAI_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://inference-api.example/v1",
            "MODEL_NAME": "example-model",
        },
    )

    exit_code = await backend_check.run_check(
        Namespace(live=False, seed_text="hello", audio_file=None, app_flow=False, require_tool=False),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[hint] Found older config keys in /tmp/reachy-old.env: MODEL_NAME, OPENAI_BASE_URL." in output
    assert "secret" not in output


@pytest.mark.asyncio
async def test_run_check_loads_explicit_env_file(tmp_path: Any, monkeypatch: Any, capsys: Any) -> None:
    """Preflight can validate a candidate dotenv file without editing .env."""
    previous_dotenv_path = config_mod._dotenv_path
    previous_dotenv_values = dict(config_mod._dotenv_values)
    tracked_attrs = {
        "BACKEND_PROVIDER": config_mod.config.BACKEND_PROVIDER,
        "CHAT_API_KEY": config_mod.config.CHAT_API_KEY,
        "CHAT_BASE_URL": config_mod.config.CHAT_BASE_URL,
        "CHAT_MODEL_NAME": config_mod.config.CHAT_MODEL_NAME,
        "STT_API_KEY": config_mod.config.STT_API_KEY,
        "STT_BASE_URL": config_mod.config.STT_BASE_URL,
        "STT_MODEL_NAME": config_mod.config.STT_MODEL_NAME,
        "TTS_API_KEY": config_mod.config.TTS_API_KEY,
        "TTS_BASE_URL": config_mod.config.TTS_BASE_URL,
        "TTS_MODEL_NAME": config_mod.config.TTS_MODEL_NAME,
        "TTS_VOICE": config_mod.config.TTS_VOICE,
    }
    tracked_env = {name: os.environ.get(name) for name in tracked_attrs}
    monkeypatch.setattr(config_mod, "_skip_dotenv", False)
    env_path = tmp_path / ".env.local-stt"
    env_path.write_text(
        "\n".join(
            [
                "BACKEND_PROVIDER=local_stt",
                "CHAT_API_KEY=chat-key",
                "CHAT_BASE_URL=https://chat.test/v1",
                "CHAT_MODEL_NAME=test-chat-model",
                "STT_API_KEY=not-needed",
                "STT_BASE_URL=http://stt.test/v1",
                "STT_MODEL_NAME=whisper-1",
                "TTS_API_KEY=tts-key",
                "TTS_BASE_URL=https://tts.test/v1",
                "TTS_MODEL_NAME=test-tts-model",
                "TTS_VOICE=cedar",
            ]
        )
    )

    try:
        exit_code = await backend_check.run_check(
            Namespace(
                live=False,
                app_flow=False,
                require_tool=False,
                seed_text="hello",
                audio_file=None,
                env_file=env_path,
            ),
        )
    finally:
        for name, value in tracked_attrs.items():
            setattr(config_mod.config, name, value)
        for name, value in tracked_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        config_mod._dotenv_path = previous_dotenv_path
        config_mod._dotenv_values = previous_dotenv_values

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "backend=local_stt" in output
    assert "chat.base_url=https://chat.test/v1" in output
    assert "[ok] backend config is valid" in output


@pytest.mark.asyncio
async def test_run_check_defaults_stage_after_explicit_env_file_load(monkeypatch: Any, capsys: Any) -> None:
    """A candidate env file should select the local-STT default live stage."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", "")
    captured: dict[str, Any] = {}

    def fake_load_dotenv_file(_env_file: Any) -> bool:
        _set_local_stt_config(monkeypatch)
        return True

    async def fake_live_local_stt_app_flow_check(
        seed_text: str,
        audio_file: Any,
        *,
        require_tool: bool,
        real_reachy: bool = False,
        robot_name: str | None = None,
    ) -> list[str]:
        captured.update(
            {
                "seed_text": seed_text,
                "audio_file": audio_file,
                "require_tool": require_tool,
                "real_reachy": real_reachy,
                "robot_name": robot_name,
            }
        )
        return ["transcript=hello", "assistant_text=hi", "output_audio_duration=0.10s"]

    monkeypatch.setattr(backend_check, "load_dotenv_file", fake_load_dotenv_file)
    monkeypatch.setattr(backend_check, "live_local_stt_app_flow_check", fake_live_local_stt_app_flow_check)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage=None,
            require_tool=True,
            real_reachy=False,
            robot_name=None,
            seed_text="hello",
            audio_file=None,
            env_file=".env.local-stt",
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["require_tool"] is True
    assert "[ok] local_stt live app-flow check completed" in output


@pytest.mark.asyncio
async def test_run_check_reports_missing_explicit_env_file(capsys: Any) -> None:
    """Missing explicit env files should fail clearly before validation."""
    exit_code = await backend_check.run_check(
        Namespace(
            live=False,
            app_flow=False,
            require_tool=False,
            seed_text="hello",
            audio_file=None,
            env_file="/tmp/does-not-exist-reachy.env",
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "[error] Could not load env file: /tmp/does-not-exist-reachy.env" in output


@pytest.mark.asyncio
async def test_run_check_live_app_flow_runs_handler_tool_path(monkeypatch: Any, capsys: Any) -> None:
    """App-flow live mode uses the conversation handler and reports tool/audio output."""
    _set_local_stt_config(monkeypatch)

    async def fake_synthesize_input_audio(seed_text: str) -> bytes:
        assert seed_text == "look around"
        return wav_bytes(np.array([0, 2000, 0], dtype=np.int16), 16000)

    monkeypatch.setattr(backend_check, "_synthesize_input_audio", fake_synthesize_input_audio)
    speech_calls: list[dict[str, Any]] = []
    receive_calls: list[tuple[int, tuple[int, ...]]] = []
    original_receive = stream_mod.ConversationStreamHandler.receive

    async def spy_receive(self: Any, frame: Any) -> None:
        sample_rate, audio_frame = frame
        receive_calls.append((sample_rate, tuple(np.asarray(audio_frame).shape)))
        await original_receive(self, frame)

    monkeypatch.setattr(stream_mod.ConversationStreamHandler, "receive", spy_receive)

    class FakeTranscription:
        text = "Reachy, look around."

    class FakeTranscriptions:
        async def create(self, **_kwargs: Any) -> FakeTranscription:
            return FakeTranscription()

    class FakeSttAudio:
        transcriptions = FakeTranscriptions()

    class FakeSpeech:
        async def create(self, **kwargs: Any) -> Any:
            speech_calls.append(kwargs)

            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1200, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeTtsAudio:
        speech = FakeSpeech()

    class FakeFunction:
        name = "sweep_look"
        arguments = "{}"

    class FakeToolCall:
        id = "call_sweep"
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
        content = "I swept my gaze and returned to center."
        tool_calls: list[Any] = []

    class FakeChoice:
        def __init__(self, message: Any) -> None:
            self.message = message

    class FakeCompletion:
        def __init__(self, message: Any) -> None:
            self.choices = [FakeChoice(message)]

    class FakeCompletions:
        call_count = 0

        async def create(self, **_kwargs: Any) -> FakeCompletion:
            self.call_count += 1
            if self.call_count == 1:
                return FakeCompletion(FakeToolMessage())
            return FakeCompletion(FakeFinalMessage())

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            if kwargs["base_url"] == config_mod.config.STT_BASE_URL:
                self.audio = FakeSttAudio()
            elif kwargs["base_url"] == config_mod.config.TTS_BASE_URL:
                self.audio = FakeTtsAudio()
            else:
                self.chat = FakeChat()

    monkeypatch.setattr(stream_mod, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=True,
            require_tool=True,
            seed_text="look around",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] local_stt live app-flow check completed" in output
    assert "transcript=Reachy, look around." in output
    assert "assistant_text=I swept my gaze and returned to center." in output
    assert "tool_results=Used tool sweep_look" in output
    assert "queued_moves=6" in output
    assert speech_calls[0]["input"] == "I swept my gaze and returned to center."
    assert len(receive_calls) >= 2
    assert all(sample_rate == stream_mod.MIC_TRANSCRIPTION_SAMPLE_RATE for sample_rate, _shape in receive_calls)


@pytest.mark.asyncio
async def test_run_check_live_stt_stage_does_not_require_tts(
    tmp_path: Any,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """STT-only live checks can validate Whisper before TTS is configured."""
    _set_local_stt_config(monkeypatch)
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(wav_bytes(np.array([0, 2000, 0], dtype=np.int16), 16000))

    class FakeTranscription:
        text = "hello reachy"

    class FakeTranscriptions:
        async def create(self, **kwargs: Any) -> FakeTranscription:
            assert kwargs["model"] == config_mod.config.STT_MODEL_NAME
            assert kwargs["file"][0] == "backend-check.wav"
            return FakeTranscription()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeModels:
        async def list(self) -> dict[str, Any]:
            return {"data": [{"id": config_mod.config.STT_MODEL_NAME}]}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["base_url"] == config_mod.config.STT_BASE_URL
            self.audio = FakeAudio()
            self.models = FakeModels()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="stt",
            require_tool=False,
            seed_text="unused",
            audio_file=audio_path,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] local_stt stt config is valid" in output
    assert "transcript=hello reachy" in output
    assert "[ok] local_stt live stt check completed" in output


@pytest.mark.asyncio
async def test_run_check_live_stt_probe_allows_empty_transcript(monkeypatch: Any, capsys: Any) -> None:
    """STT probe should validate endpoint reachability without requiring speech recognition."""
    _set_local_stt_config(monkeypatch)
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")

    class FakeTranscription:
        text = ""

    class FakeTranscriptions:
        async def create(self, **kwargs: Any) -> FakeTranscription:
            assert kwargs["model"] == config_mod.config.STT_MODEL_NAME
            assert kwargs["file"][0] == "backend-check-probe.wav"
            return FakeTranscription()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeModels:
        async def list(self) -> dict[str, Any]:
            return {"data": [{"id": config_mod.config.STT_MODEL_NAME}]}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["base_url"] == config_mod.config.STT_BASE_URL
            self.audio = FakeAudio()
            self.models = FakeModels()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="stt-probe",
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] local_stt stt-probe config is valid" in output
    assert "stt_models_endpoint=reachable" in output
    assert "stt_model_listed=yes" in output
    assert "stt_endpoint=reachable" in output
    assert "transcript=<empty>" in output
    assert "[ok] local_stt live stt-probe check completed" in output


@pytest.mark.asyncio
async def test_run_check_live_stt_probe_fails_when_model_is_not_listed(monkeypatch: Any, capsys: Any) -> None:
    """STT probe should catch a likely STT_MODEL_NAME mismatch when /models is available."""
    _set_local_stt_config(monkeypatch)

    class FakeModels:
        async def list(self) -> dict[str, Any]:
            return {"data": [{"id": "different-whisper-model"}]}

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="stt-probe",
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "stt_models_endpoint=reachable" in output
    assert "stt_model_listed=no" in output
    assert "stt_available_models=different-whisper-model" in output


@pytest.mark.asyncio
async def test_run_check_live_stt_probe_reports_endpoint_context(monkeypatch: Any, capsys: Any) -> None:
    """STT probe failures should point at STT_BASE_URL and the expected endpoint path."""
    _set_local_stt_config(monkeypatch)

    class FakeTranscriptions:
        async def create(self, **_kwargs: Any) -> Any:
            raise ConnectionError("offline")

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeModels:
        async def list(self) -> dict[str, Any]:
            return {"data": [{"id": config_mod.config.STT_MODEL_NAME}]}

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.audio = FakeAudio()
            self.models = FakeModels()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="stt-probe",
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[error] local_stt live stt-probe check failed: RuntimeError:" in output
    assert "STT endpoint request failed" in output
    assert "stt_models_endpoint=reachable" in output
    assert "stt_model_listed=yes" in output
    assert "base_url='https://stt.test/v1'" in output
    assert "POST /audio/transcriptions" in output
    assert "ConnectionError: offline" in output
    assert "Traceback" not in output


@pytest.mark.asyncio
async def test_run_check_live_stage_reports_endpoint_errors(monkeypatch: Any, capsys: Any) -> None:
    """Live stage failures should be one-line user errors, not Python tracebacks."""
    _set_local_stt_config(monkeypatch)

    async def fake_live_local_stt_transcription_check(_seed_text: str, _audio_file: Any) -> list[str]:
        raise RuntimeError("endpoint offline")

    monkeypatch.setattr(
        backend_check,
        "live_local_stt_transcription_check",
        fake_live_local_stt_transcription_check,
    )

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="stt",
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[error] local_stt live stt check failed: RuntimeError: endpoint offline" in output
    assert "Traceback" not in output


@pytest.mark.asyncio
async def test_run_check_live_chain_accepts_dict_shaped_chat_response(monkeypatch: Any, capsys: Any) -> None:
    """The simple local_stt chain preflight should accept OpenAI-compatible JSON responses."""
    _set_local_stt_config(monkeypatch)

    class FakeTranscription:
        text = "Hello Reachy"

    class FakeTranscriptions:
        async def create(self, **_kwargs: Any) -> FakeTranscription:
            return FakeTranscription()

    class FakeSpeech:
        async def create(self, **_kwargs: Any) -> Any:
            class FakeSpeechResponse:
                content = wav_bytes(np.array([0, 1200, 0], dtype=np.int16), 24000)

            return FakeSpeechResponse()

    class FakeAudio:
        transcriptions = FakeTranscriptions()
        speech = FakeSpeech()

    class FakeCompletions:
        async def create(self, **_kwargs: Any) -> Any:
            class FakeCompletion:
                choices = [{"message": {"content": "Hello from chat."}}]

            return FakeCompletion()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            if kwargs["base_url"] == config_mod.config.CHAT_BASE_URL:
                self.chat = FakeChat()
            else:
                self.audio = FakeAudio()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="chain",
            require_tool=False,
            seed_text="hello",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "transcript=Hello Reachy" in output
    assert "assistant_text=Hello from chat." in output
    assert "[ok] local_stt live chain check completed" in output


@pytest.mark.asyncio
async def test_run_check_live_chat_stage_does_not_require_stt_or_tts(monkeypatch: Any, capsys: Any) -> None:
    """Chat-only live checks can validate the model/tool path before STT/TTS are configured."""
    _set_local_stt_config(monkeypatch)
    monkeypatch.setattr(config_mod.config, "STT_BASE_URL", "")
    monkeypatch.setattr(config_mod.config, "TTS_BASE_URL", "")

    class FakeMessage:
        content = "Hello from chat."
        tool_calls: list[Any] = []

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeCompletion:
            assert kwargs["model"] == config_mod.config.CHAT_MODEL_NAME
            assert kwargs["tools"]
            return FakeCompletion()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["base_url"] == config_mod.config.CHAT_BASE_URL
            self.chat = FakeChat()

    monkeypatch.setattr(backend_check, "AsyncOpenAI", FakeClient)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage="chat",
            require_tool=False,
            seed_text="Say hello.",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[ok] local_stt chat config is valid" in output
    assert "assistant_text=Hello from chat." in output
    assert "tool_results=<none>" in output
    assert "[ok] local_stt live chat check completed" in output


@pytest.mark.asyncio
async def test_live_realtime_session_check_uses_conversation_handler(monkeypatch: Any) -> None:
    """Realtime live checks should exercise handler startup instead of stopping at config validation."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_VOICE", "cedar")
    calls: list[str] = []

    class FakeHandler:
        _realtime_startup_task = None
        _startup_error = None

        def __init__(self, _deps: Any) -> None:
            calls.append("created")

        async def _ensure_realtime_session(self, task_name: str) -> bool:
            calls.append(task_name)
            return True

        async def shutdown(self) -> None:
            calls.append("shutdown")

    monkeypatch.setattr(stream_mod, "ConversationStreamHandler", FakeHandler)

    result = await backend_check.live_realtime_session_check()

    assert calls == ["created", "backend-check-realtime-session", "shutdown"]
    assert result == [
        "realtime_session=connected",
        "realtime_model=gpt-realtime",
        "realtime_voice=cedar",
    ]


@pytest.mark.asyncio
async def test_run_check_live_realtime_backend(monkeypatch: Any, capsys: Any) -> None:
    """--live should validate realtime session startup for realtime backends."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_OPENAI_REALTIME)
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config_mod.config, "OPENAI_REALTIME_MODEL", "gpt-realtime")

    async def fake_live_realtime_session_check() -> list[str]:
        return ["realtime_session=connected"]

    monkeypatch.setattr(backend_check, "live_realtime_session_check", fake_live_realtime_session_check)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage=None,
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "backend=openai_realtime" in output
    assert "realtime_session=connected" in output
    assert "[ok] realtime live session check completed" in output


@pytest.mark.asyncio
async def test_run_check_live_realtime_backend_reports_session_failure(monkeypatch: Any, capsys: Any) -> None:
    """Realtime live failures should be visible without a traceback."""
    monkeypatch.setattr(config_mod.config, "BACKEND_PROVIDER", config_mod.BACKEND_HF_REALTIME)
    monkeypatch.setattr(config_mod.config, "HF_REALTIME_CONNECTION_MODE", config_mod.HF_REALTIME_CONNECTION_DEPLOYED)

    async def fake_live_realtime_session_check() -> list[str]:
        raise RuntimeError("session broker unavailable")

    monkeypatch.setattr(backend_check, "live_realtime_session_check", fake_live_realtime_session_check)

    exit_code = await backend_check.run_check(
        Namespace(
            live=True,
            app_flow=False,
            stage=None,
            require_tool=False,
            seed_text="unused",
            audio_file=None,
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[error] realtime live session check failed: RuntimeError: session broker unavailable" in output
    assert "Traceback" not in output
