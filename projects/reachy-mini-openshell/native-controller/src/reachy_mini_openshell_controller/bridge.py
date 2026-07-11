"""Bidirectional audio bridge between Reachy hardware and the sandbox agent."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from threading import Event
from typing import Any

from websockets.asyncio.client import connect

from reachy_mini_openshell_controller.audio import WIRE_SAMPLE_RATE, decode_agent_audio, encode_robot_audio
from reachy_mini_openshell_controller.settings import ControllerSettings

logger = logging.getLogger(__name__)


class NativeAudioBridge:
    """Own Reachy's microphone/speaker and reconnect to the local sandbox service."""

    def __init__(self, media: Any, settings: ControllerSettings) -> None:
        """Initialize the bridge around Reachy's media manager."""
        self.media = media
        self.settings = settings
        self._mute_microphone_until = 0.0

    async def run(self, stop_event: Event) -> None:
        """Run audio until the Reachy App lifecycle requests a stop."""
        self.media.start_recording()
        self.media.start_playing()
        await asyncio.sleep(1.0)
        input_rate = int(self.media.get_input_audio_samplerate())
        output_rate = int(self.media.get_output_audio_samplerate())
        reconnect_delay = self.settings.reconnect_initial_seconds
        try:
            while not stop_event.is_set():
                try:
                    async with connect(
                        self.settings.audio_websocket_url,
                        host=self.settings.gateway_connect_host,
                        port=self.settings.gateway_port,
                        proxy=None,
                        max_size=4 * 1024 * 1024,
                        ping_interval=20,
                        ping_timeout=20,
                    ) as websocket:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "hello",
                                    "format": "pcm_s16le",
                                    "sample_rate": WIRE_SAMPLE_RATE,
                                    "channels": 1,
                                }
                            )
                        )
                        ready = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                        if not isinstance(ready, str) or json.loads(ready).get("type") != "ready":
                            raise RuntimeError("sandbox audio service did not return ready")
                        logger.info("Connected Reachy audio to %s", self.settings.audio_websocket_url)
                        reconnect_delay = self.settings.reconnect_initial_seconds
                        await self._run_session(websocket, stop_event, input_rate, output_rate)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if stop_event.is_set():
                        break
                    logger.warning("Audio bridge disconnected (%s); reconnecting in %.1fs", exc, reconnect_delay)
                    await self._wait_or_stop(stop_event, reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, self.settings.reconnect_max_seconds)
        finally:
            self._safe_media_call("stop_recording")
            self._safe_media_call("stop_playing")

    async def _run_session(self, websocket: Any, stop_event: Event, input_rate: int, output_rate: int) -> None:
        record_task = asyncio.create_task(
            self._record_loop(websocket, stop_event, input_rate),
            name="reachy-microphone",
        )
        play_task = asyncio.create_task(
            self._play_loop(websocket, stop_event, output_rate),
            name="reachy-speaker",
        )
        stop_task = asyncio.create_task(self._wait_or_stop(stop_event, None), name="reachy-stop-event")
        done, pending = await asyncio.wait(
            {record_task, play_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task is not stop_task:
                task.result()

    async def _record_loop(self, websocket: Any, stop_event: Event, input_rate: int) -> None:
        while not stop_event.is_set():
            frame = self.media.get_audio_sample()
            if frame is None:
                await asyncio.sleep(0.005)
                continue
            if time.monotonic() < self._mute_microphone_until:
                await asyncio.sleep(0)
                continue
            payload = encode_robot_audio(frame, input_rate)
            if payload:
                await websocket.send(payload)
            await asyncio.sleep(0)

    async def _play_loop(self, websocket: Any, stop_event: Event, output_rate: int) -> None:
        while not stop_event.is_set():
            message = await websocket.recv()
            if isinstance(message, bytes):
                audio = decode_agent_audio(message, output_rate)
                if audio.size:
                    duration = audio.size / output_rate
                    self._mute_microphone_until = max(
                        self._mute_microphone_until,
                        time.monotonic() + duration + 0.15,
                    )
                    self.media.push_audio_sample(audio)
                continue
            try:
                event = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                logger.debug("Ignoring malformed sandbox control message")
                continue
            if event.get("type") == "messages":
                for item in event.get("messages", []):
                    if isinstance(item, dict):
                        logger.info("agent role=%s content=%s", item.get("role"), item.get("content"))
            elif event.get("type") == "error":
                logger.error("Sandbox audio error: %s", event.get("message"))

    @staticmethod
    async def _wait_or_stop(stop_event: Event, timeout: float | None) -> None:
        started = time.monotonic()
        while not stop_event.is_set():
            if timeout is not None and time.monotonic() - started >= timeout:
                return
            await asyncio.sleep(0.1)

    def _safe_media_call(self, method_name: str) -> None:
        try:
            getattr(self.media, method_name)()
        except Exception as exc:
            logger.debug("Ignoring %s failure during shutdown: %s", method_name, exc)
