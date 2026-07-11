"""Reachy Mini Apps lifecycle integration for the OpenShell agent."""

from __future__ import annotations

import asyncio
import logging
from threading import Event
from typing import Any

from reachy_mini import ReachyMini, ReachyMiniApp

from reachy_mini_openshell_controller.bridge import NativeAudioBridge
from reachy_mini_openshell_controller.camera_adapter import TrustedCameraAdapter
from reachy_mini_openshell_controller.openshell import OpenShellController
from reachy_mini_openshell_controller.settings import ControllerSettings

logger = logging.getLogger(__name__)


class ReachyOpenShellApp(ReachyMiniApp):  # type: ignore[misc]
    """Start the sandbox agent and bridge audio for the lifetime of the Reachy App."""

    custom_app_url = "http://0.0.0.0:8042"
    dont_start_webserver = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the native app and its fixed trusted camera route."""
        super().__init__(*args, **kwargs)
        self._media: Any | None = None
        if self.settings_app is None:
            raise RuntimeError("Reachy camera adapter web server was not initialized")
        self._camera_adapter = TrustedCameraAdapter(lambda: self._media)
        self._camera_adapter.register(self.settings_app)

    def run(self, reachy_mini: ReachyMini, stop_event: Event) -> None:
        """Start the sandbox agent and bridge audio until stopped."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        )
        settings = ControllerSettings.from_environment()
        controller = OpenShellController(settings)
        self._media = reachy_mini.media
        try:
            controller.start_agent()
            asyncio.run(NativeAudioBridge(reachy_mini.media, settings).run(stop_event))
        finally:
            self._media = None
            controller.stop_agent()


if __name__ == "__main__":
    app = ReachyOpenShellApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
