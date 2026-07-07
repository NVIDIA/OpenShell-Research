"""Local-STT backend adapter for STT -> Chat Completions/tools -> TTS."""

from typing import Any
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tool_transport import ToolTransport
from reachy_mini_conversation_app.chat_completions import ChatCompletionRunner
from reachy_mini_conversation_app.speech_endpoints import SpeechEndpointClient
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.background_tool_manager import BackgroundToolManager


class LocalSTTBackend:
    """Run the configured local-STT conversation cascade."""

    def __init__(
        self,
        *,
        deps: ToolDependencies,
        tool_manager: BackgroundToolManager,
        client_factory: Callable[..., Any],
        tool_transport: ToolTransport | None = None,
    ) -> None:
        """Initialize the local-STT backend adapter."""
        self.deps = deps
        self.tool_manager = tool_manager
        self.tool_transport = tool_transport
        self.client_factory = client_factory
        self._speech_endpoint_client: SpeechEndpointClient | None = None
        self._chat_client: Any = None

    @property
    def stt_context(self) -> str:
        """Return non-secret STT context for user-facing errors."""
        return f"model={config.STT_MODEL_NAME!r}, base_url={config.STT_BASE_URL!r}"

    @property
    def tts_context(self) -> str:
        """Return non-secret TTS context for user-facing errors."""
        return f"model={config.TTS_MODEL_NAME!r}, base_url={config.TTS_BASE_URL!r}"

    def _get_speech_endpoint_client(self) -> SpeechEndpointClient:
        """Return the OpenAI-compatible STT/TTS endpoint wrapper."""
        if self._speech_endpoint_client is None:
            self._speech_endpoint_client = SpeechEndpointClient(
                stt_api_key=config.STT_API_KEY,
                stt_base_url=config.STT_BASE_URL,
                stt_model=config.STT_MODEL_NAME,
                tts_api_key=config.TTS_API_KEY,
                tts_base_url=config.TTS_BASE_URL,
                tts_model=config.TTS_MODEL_NAME,
                tts_voice=config.TTS_VOICE,
                client_factory=self.client_factory,
            )
        return self._speech_endpoint_client

    def _get_chat_client(self) -> Any:
        """Return the OpenAI-compatible Chat Completions client."""
        if self._chat_client is None:
            self._chat_client = self.client_factory(
                api_key=(config.CHAT_API_KEY or "").strip(),
                base_url=config.CHAT_BASE_URL,
            )
        return self._chat_client

    async def transcribe_audio(
        self,
        audio_frame: NDArray[np.int16],
        sample_rate: int,
        *,
        filename: str = "microphone.wav",
    ) -> str:
        """Transcribe mono int16 audio through the configured STT endpoint."""
        return await self._get_speech_endpoint_client().transcribe_audio(
            audio_frame,
            sample_rate,
            filename=filename,
        )

    async def transcribe_wav_bytes(self, wav_payload: bytes, *, filename: str = "microphone.wav") -> str:
        """Transcribe a WAV payload through the configured STT endpoint."""
        return await self._get_speech_endpoint_client().transcribe_wav_bytes(wav_payload, filename=filename)

    async def synthesize_speech(self, text: str) -> tuple[int, NDArray[np.int16]]:
        """Synthesize assistant text through the configured TTS endpoint."""
        return await self._get_speech_endpoint_client().synthesize_speech(text)

    async def synthesize_speech_wav_bytes(self, text: str) -> bytes:
        """Synthesize assistant text through the configured TTS endpoint as WAV bytes."""
        return await self._get_speech_endpoint_client().synthesize_speech_wav_bytes(text)

    async def send_text_message(self, text: str) -> list[dict[str, Any]]:
        """Send a text turn through Chat Completions and Reachy tools."""
        return await ChatCompletionRunner(
            client=self._get_chat_client(),
            deps=self.deps,
            tool_manager=self.tool_manager,
            tool_transport=self.tool_transport,
            model_name=config.CHAT_MODEL_NAME or "",
            base_url=config.CHAT_BASE_URL,
        ).send_text_message(text)
