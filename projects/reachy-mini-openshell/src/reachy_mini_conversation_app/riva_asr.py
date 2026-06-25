"""Riva streaming ASR bridge used by microphone-to-text mode."""

from __future__ import annotations
import os
import queue
import asyncio
import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlparse
from collections.abc import Callable, Awaitable


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RivaAsrConfig:
    """Configuration for Riva streaming ASR."""

    server_uri: str
    language_code: str
    model: str
    use_ssl: bool
    ssl_cert: str | None
    authorization: str | None
    metadata: tuple[tuple[str, str], ...]
    interim_results: bool
    automatic_punctuation: bool

    @classmethod
    def from_env(cls) -> "RivaAsrConfig":
        """Build Riva ASR configuration from environment variables."""
        server_uri, inferred_use_ssl = _normalize_server_uri(os.getenv("RIVA_SERVER_URI", "localhost:50051"))
        return cls(
            server_uri=server_uri,
            language_code=os.getenv("RIVA_LANGUAGE_CODE", "en-US"),
            model=os.getenv("RIVA_ASR_MODEL", ""),
            use_ssl=_env_flag("RIVA_USE_SSL", default=inferred_use_ssl),
            ssl_cert=os.getenv("RIVA_SSL_CERT") or None,
            authorization=os.getenv("RIVA_AUTHORIZATION") or None,
            metadata=_parse_metadata(os.getenv("RIVA_METADATA", "")),
            interim_results=_env_flag("RIVA_INTERIM_RESULTS", default=True),
            automatic_punctuation=_env_flag("RIVA_AUTOMATIC_PUNCTUATION", default=True),
        )


class RivaStreamingTranscriber:
    """Threaded wrapper around the blocking Riva streaming ASR client."""

    def __init__(
        self,
        *,
        config: RivaAsrConfig,
        on_final_transcript: Callable[[str], Awaitable[None]],
        on_partial_transcript: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the streaming transcriber callbacks and audio queue."""
        self._config = config
        self._on_final_transcript = on_final_transcript
        self._on_partial_transcript = on_partial_transcript
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sample_rate_hertz: int | None = None
        self._last_partial = ""

    async def start(self, sample_rate_hertz: int) -> None:
        """Start the background Riva stream."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._loop = asyncio.get_running_loop()
        self._sample_rate_hertz = sample_rate_hertz
        self._thread = threading.Thread(target=self._run, name="riva-asr-stream", daemon=True)
        self._thread.start()

    async def send_audio(self, audio_bytes: bytes) -> bool:
        """Send one PCM16 audio chunk to the Riva stream."""
        if self._thread is None or not self._thread.is_alive():
            logger.debug("Dropping Riva audio frame because the ASR stream is not running")
            return False

        try:
            self._audio_queue.put_nowait(audio_bytes)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            self._audio_queue.put_nowait(audio_bytes)
        return True

    async def stop(self) -> None:
        """Close the stream and wait briefly for the worker to exit."""
        if self._thread is None:
            return
        self._audio_queue.put(None)
        thread = self._thread
        await asyncio.to_thread(thread.join, 2.0)

    def _audio_chunks(self) -> object:
        while True:
            chunk = self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

    def _run(self) -> None:
        try:
            import riva.client
        except ImportError:
            self._schedule_final(
                "[error] Riva STT mode requires the optional dependency "
                "`nvidia-riva-client`. Install it with `uv sync --extra riva`."
            )
            return

        if self._loop is None or self._sample_rate_hertz is None:
            logger.error("Riva ASR stream started without an event loop or sample rate")
            return

        try:
            metadata_args = list(self._config.metadata)
            if self._config.authorization:
                metadata_args.append(("authorization", self._config.authorization))

            auth = riva.client.Auth(
                uri=self._config.server_uri,
                use_ssl=self._config.use_ssl,
                ssl_cert=self._config.ssl_cert,
                metadata_args=metadata_args,
            )
            asr_service = riva.client.ASRService(auth)
            recognition_config = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                sample_rate_hertz=self._sample_rate_hertz,
                language_code=self._config.language_code,
                max_alternatives=1,
                enable_automatic_punctuation=self._config.automatic_punctuation,
                audio_channel_count=1,
                model=self._config.model,
            )
            streaming_config = riva.client.StreamingRecognitionConfig(
                config=recognition_config,
                interim_results=self._config.interim_results,
            )

            responses = asr_service.streaming_response_generator(self._audio_chunks(), streaming_config)
            for response in responses:
                for result in getattr(response, "results", []):
                    transcript = self._result_transcript(result)
                    if not transcript:
                        continue
                    if getattr(result, "is_final", False):
                        self._schedule_final(transcript)
                    else:
                        self._schedule_partial(transcript)
        except Exception as exc:
            logger.exception("Riva streaming ASR failed")
            self._schedule_final(f"[error] Riva streaming ASR failed: {type(exc).__name__}: {exc}")

    def _result_transcript(self, result: object) -> str:
        alternatives = getattr(result, "alternatives", [])
        if not alternatives:
            return ""
        transcript = getattr(alternatives[0], "transcript", "")
        return transcript.strip() if isinstance(transcript, str) else ""

    def _schedule_partial(self, transcript: str) -> None:
        if transcript == self._last_partial:
            return
        self._last_partial = transcript
        if self._loop is None or self._on_partial_transcript is None:
            return
        asyncio.run_coroutine_threadsafe(self._on_partial_transcript(transcript), self._loop)

    def _schedule_final(self, transcript: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._on_final_transcript(transcript), self._loop)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    if not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_server_uri(raw: str) -> tuple[str, bool]:
    value = raw.strip()
    if "://" not in value:
        return value, False

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        logger.warning("RIVA_SERVER_URI=%r has an unexpected scheme; passing host/path through", raw)

    server_uri = parsed.netloc or parsed.path
    if parsed.path not in {"", "/"} and parsed.netloc:
        logger.warning("Ignoring path in RIVA_SERVER_URI=%r; Riva client expects host:port", raw)

    return server_uri, parsed.scheme == "https"


def _parse_metadata(raw: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            logger.warning("Ignoring invalid RIVA_METADATA item %r; expected key=value", item)
            continue
        pairs.append((key.strip(), value.strip()))
    return tuple(pairs)
