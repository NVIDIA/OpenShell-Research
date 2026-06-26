"""Bidirectional local audio stream for Reachy Mini audio."""

import time
import asyncio
import logging
from typing import Any, List, Optional
from pathlib import Path

from fastrtc import AdditionalOutputs, audio_to_float32
from scipy.signal import resample

from reachy_mini import ReachyMini
from reachy_mini.media.media_manager import MediaBackend
from reachy_mini_conversation_app.config import (
    BACKEND_OPENAI_REALTIME,
    load_dotenv_file,
)
from reachy_mini_conversation_app.backend_runtime import selected_backend, backend_config_error
from reachy_mini_conversation_app.conversation_stream import ConversationStreamHandler


logger = logging.getLogger(__name__)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: ConversationStreamHandler,
        robot: ReachyMini,
        *,
        settings_app: Optional[Any] = None,
        instance_path: Optional[str] = None,
    ):
        """Initialize the stream with a conversation handler and pipelines.

        - ``settings_app``: reserved for the Reachy Mini Apps runtime.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        """
        self.handler = handler
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        # Allow the handler to flush the player queue when appropriate.
        self.handler._clear_queue = self.clear_audio_queue
        self._settings_app: Optional[Any] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._asyncio_loop = None

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        Provider credentials are loaded from `.env` or exported process
        variables. If required backend config is missing, startup stops before
        media is opened.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first for Reachy Mini app-shell runs.
        if self._instance_path:
            try:
                load_dotenv_file(Path(self._instance_path) / ".env")
            except Exception as exc:
                logger.debug("Instance .env loading skipped: %s", exc)

        missing_reason = self._missing_backend_config()
        if missing_reason:
            if selected_backend().provider == BACKEND_OPENAI_REALTIME and "OPENAI_API_KEY" in missing_reason:
                logger.error(
                    "%s Export OPENAI_API_KEY in the shell that starts the app, or set "
                    "OPENAI_REALTIME_API_KEY in .env only if this app needs a different OpenAI key. "
                    "Then restart the conversation app.",
                    missing_reason,
                )
            else:
                logger.error("%s Add the missing value to .env and restart the conversation app.", missing_reason)
            return

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()
        time.sleep(1)  # give some time to the pipelines to start

        async def runner() -> None:
            self._asyncio_loop = asyncio.get_running_loop()  # type: ignore[assignment]
            self._tasks = [
                asyncio.create_task(self.handler.start_up(), name="conversation-handler"),
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self.handler.shutdown()

        asyncio.run(runner())

    def _missing_backend_config(self) -> str | None:
        """Return a startup-blocking config error for the selected backend, if any."""
        return backend_config_error()

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (conversation-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug(f"Error stopping recording (may already be stopped): {e}")

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug(f"Error stopping playback (may already be stopped): {e}")

        def stop_async_tasks() -> None:
            self._stop_event.set()
            for task in self._tasks:
                if not task.done():
                    task.cancel()

        if self._asyncio_loop is not None and self._asyncio_loop.is_running():
            self._asyncio_loop.call_soon_threadsafe(stop_async_tasks)
        else:
            stop_async_tasks()

    def clear_audio_queue(self) -> None:
        """Flush the player's appsrc to drop any queued audio immediately."""
        logger.info("User intervention: flushing player queue")
        audio = self._robot.media.audio
        if self._robot.media.backend == MediaBackend.GSTREAMER:
            # Directly flush gstreamer audio pipe
            clear_player = getattr(audio, "clear_player", None)
            if callable(clear_player):
                clear_player()
        elif (
            self._robot.media.backend == MediaBackend.DEFAULT
            or self._robot.media.backend == MediaBackend.DEFAULT_NO_VIDEO
        ):
            clear_output_buffer = getattr(audio, "clear_output_buffer", None)
            if callable(clear_output_buffer):
                clear_output_buffer()
        self.handler.output_queue = asyncio.Queue()

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is not None:
                await self.handler.receive((input_sample_rate, audio_frame))
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            handler_output = await self.handler.emit()

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        logger.info(
                            "role=%s content=%s",
                            msg.get("role"),
                            content if len(content) < 500 else content[:500] + "…",
                        )

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output
                output_sample_rate = self._robot.media.get_output_audio_samplerate()

                # Reshape if needed
                if audio_data.ndim == 2:
                    # Scipy channels last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                # Resample if needed
                if input_sample_rate != output_sample_rate:
                    audio_frame = resample(
                        audio_frame,
                        int(len(audio_frame) * output_sample_rate / input_sample_rate),
                    )

                self._robot.media.push_audio_sample(audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
