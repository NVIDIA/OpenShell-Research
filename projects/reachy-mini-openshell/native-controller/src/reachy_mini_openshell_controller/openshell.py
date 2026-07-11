"""Fixed OpenShell lifecycle commands used by the trusted Reachy App."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from reachy_mini_openshell_controller.settings import ControllerSettings

logger = logging.getLogger(__name__)

ANSI_CONTROL_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class OpenShellLifecycleError(RuntimeError):
    """Raised when the fixed sandbox lifecycle cannot be completed."""


@dataclass(frozen=True)
class CommandResult:
    """Captured output from one fixed OpenShell invocation."""

    returncode: int
    stdout: str
    stderr: str


class OpenShellController:
    """Control one pre-created sandbox without accepting arbitrary commands."""

    def __init__(self, settings: ControllerSettings) -> None:
        """Resolve the OpenShell CLI used for lifecycle commands."""
        self.settings = settings
        self.executable = self._resolve_executable(settings.openshell_executable)

    @staticmethod
    def _resolve_executable(configured: str | None) -> str:
        if configured:
            path = Path(configured).expanduser()
            if path.is_file() and path.stat().st_mode & 0o111:
                return str(path)
            raise OpenShellLifecycleError(f"REACHY_OPENSHELL_BIN is not executable: {path}")

        discovered = shutil.which("openshell")
        if discovered:
            return discovered
        for candidate in (
            Path("/home/pollen/.local/bin/openshell"),
            Path("/home/pollen/.cargo/bin/openshell"),
            Path("/usr/local/bin/openshell"),
            Path("/usr/bin/openshell"),
        ):
            if candidate.is_file() and candidate.stat().st_mode & 0o111:
                return str(candidate)
        raise OpenShellLifecycleError("openshell executable not found; set REACHY_OPENSHELL_BIN")

    def _run(
        self,
        arguments: list[str],
        *,
        check: bool = True,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        environment = os.environ.copy()
        environment["NO_COLOR"] = "1"
        environment["CLICOLOR"] = "0"
        environment["CLICOLOR_FORCE"] = "0"
        environment["FORCE_COLOR"] = "0"
        environment["TERM"] = "dumb"
        completed = subprocess.run(  # noqa: S603 - executable and arguments are fixed by this class
            [self.executable, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout_seconds or self.settings.command_timeout_seconds,
            check=False,
            env=environment,
        )
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise OpenShellLifecycleError(f"openshell {' '.join(arguments[:3])} failed: {detail}")
        return result

    def verify_ready(self) -> None:
        """Require the pre-created sandbox to be Ready."""
        result = self._run(["sandbox", "get", self.settings.sandbox_name])
        combined = ANSI_CONTROL_SEQUENCE.sub("", f"{result.stdout}\n{result.stderr}").lower()
        ready_line = re.search(r"(?im)^\s*(?:phase\s*:?\s*)?ready\s*$", combined)
        if ready_line is None:
            raise OpenShellLifecycleError(
                f"sandbox {self.settings.sandbox_name!r} is not Ready; output was: {result.stdout.strip()}"
            )

    def ensure_audio_service(self) -> None:
        """Create the fixed audio service endpoint when missing."""
        existing = self._run(
            ["service", "get", self.settings.sandbox_name, self.settings.service_name],
            check=False,
        )
        if existing.returncode == 0:
            return
        self._run(
            [
                "service",
                "expose",
                self.settings.sandbox_name,
                str(self.settings.service_port),
                self.settings.service_name,
            ]
        )

    def start_agent(self) -> None:
        """Start the agent inside the existing sandbox."""
        self.verify_ready()
        self._run(
            [
                "sandbox",
                "exec",
                "--name",
                self.settings.sandbox_name,
                "--no-tty",
                "--",
                "/opt/venv/bin/reachy-agent-control",
                "start",
            ]
        )
        # Register the service only after its loopback listener is live. The
        # gateway can otherwise retain an unavailable target for the service.
        self.ensure_audio_service()
        logger.info("Started agent inside sandbox %s", self.settings.sandbox_name)

    def stop_agent(self) -> None:
        """Stop the sandbox agent without deleting its sandbox."""
        result = self._run(
            [
                "sandbox",
                "exec",
                "--name",
                self.settings.sandbox_name,
                "--no-tty",
                "--",
                "/opt/venv/bin/reachy-agent-control",
                "stop",
            ],
            check=False,
            timeout_seconds=min(self.settings.command_timeout_seconds, 15.0),
        )
        if result.returncode != 0:
            logger.warning("Unable to stop sandbox agent cleanly: %s", (result.stderr or result.stdout).strip())
