"""Configuration for the trusted native controller."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ControllerSettings:
    """Fixed sandbox and audio bridge settings."""

    sandbox_name: str = "reachy-agent"
    service_name: str = "audio"
    service_port: int = 8765
    gateway_port: int = 17670
    openshell_executable: str | None = None
    command_timeout_seconds: float = 150.0
    reconnect_initial_seconds: float = 0.5
    reconnect_max_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Reject settings that could be interpreted as CLI options or invalid URLs."""
        name_pattern = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
        for label, value in (("sandbox_name", self.sandbox_name), ("service_name", self.service_name)):
            if name_pattern.fullmatch(value) is None:
                raise ValueError(f"{label} must contain only lowercase letters, numbers, and hyphens")
        for label, value in (("service_port", self.service_port), ("gateway_port", self.gateway_port)):
            if not 1 <= value <= 65_535:
                raise ValueError(f"{label} must be between 1 and 65535")
        if self.command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        if self.reconnect_initial_seconds <= 0 or self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("audio reconnect delays are invalid")

    @property
    def audio_websocket_url(self) -> str:
        """Return the loopback OpenShell service URL."""
        return (
            f"ws://{self.sandbox_name}--{self.service_name}.openshell.localhost:"
            f"{self.gateway_port}/audio"
        )

    @property
    def gateway_connect_host(self) -> str:
        """Return the fixed TCP destination for the onboard OpenShell gateway."""
        return "127.0.0.1"

    @classmethod
    def from_environment(cls) -> "ControllerSettings":
        """Load supported controller overrides from the environment."""
        return cls(
            sandbox_name=os.getenv("REACHY_OPENSHELL_SANDBOX", "reachy-agent"),
            service_name=os.getenv("REACHY_OPENSHELL_AUDIO_SERVICE", "audio"),
            service_port=int(os.getenv("REACHY_OPENSHELL_AUDIO_PORT", "8765")),
            gateway_port=int(os.getenv("REACHY_OPENSHELL_GATEWAY_PORT", "17670")),
            openshell_executable=os.getenv("REACHY_OPENSHELL_BIN") or None,
            command_timeout_seconds=float(os.getenv("REACHY_OPENSHELL_COMMAND_TIMEOUT_SECONDS", "150")),
            reconnect_initial_seconds=float(os.getenv("REACHY_AUDIO_RECONNECT_INITIAL_SECONDS", "0.5")),
            reconnect_max_seconds=float(os.getenv("REACHY_AUDIO_RECONNECT_MAX_SECONDS", "5")),
        )
