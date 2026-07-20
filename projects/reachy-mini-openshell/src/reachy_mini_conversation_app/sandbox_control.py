"""Idempotent lifecycle control for the agent process inside a sandbox."""

from __future__ import annotations
import os
import sys
import time
import signal
import argparse
import subprocess
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class ControlSettings:
    """Filesystem, process, and health settings for sandbox lifecycle control."""

    state_directory: Path = Path("/sandbox/run")
    log_directory: Path = Path("/sandbox/logs")
    listen_port: int = 8765
    startup_timeout_seconds: float = 120.0
    shutdown_timeout_seconds: float = 10.0

    @property
    def pid_path(self) -> Path:
        """Return the PID file path."""
        return self.state_directory / "reachy-agent.pid"

    @property
    def log_path(self) -> Path:
        """Return the detached agent log path."""
        return self.log_directory / "reachy-agent.log"


def _read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None
    return pid if pid > 1 else None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _port_is_listening(port: int) -> bool:
    """Check Linux TCP tables without making a policy-controlled network call.

    OpenShell deliberately blocks sandbox egress to loopback. The gateway can
    still forward an exposed service to a loopback listener, so lifecycle
    readiness must be established from kernel state instead of an HTTP probe.
    """
    expected_port = f"{port:04X}"
    for table_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = table_path.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) < 4:
                continue
            local_address = fields[1]
            state = fields[3]
            _, separator, encoded_port = local_address.rpartition(":")
            if separator and encoded_port.upper() == expected_port and state == "0A":
                return True
    return False


def _wait_until(predicate: object, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        time.sleep(interval)
    return bool(callable(predicate) and predicate())


class SandboxAgentControl:
    """Start, stop, and inspect the detached sandbox audio agent."""

    def __init__(self, settings: ControlSettings | None = None) -> None:
        """Initialize lifecycle control with environment-aware defaults."""
        self.settings = settings or ControlSettings(
            state_directory=Path(os.getenv("REACHY_AGENT_STATE_DIR", "/sandbox/run")),
            log_directory=Path(os.getenv("REACHY_AGENT_LOG_DIR", "/sandbox/logs")),
            listen_port=int(os.getenv("REACHY_AUDIO_PORT", "8765")),
            startup_timeout_seconds=float(os.getenv("REACHY_AGENT_START_TIMEOUT_SECONDS", "120")),
            shutdown_timeout_seconds=float(os.getenv("REACHY_AGENT_STOP_TIMEOUT_SECONDS", "10")),
        )

    def status(self) -> str:
        """Return stopped, unhealthy, or running."""
        pid = _read_pid(self.settings.pid_path)
        if pid is None or not _process_exists(pid):
            return "stopped"
        return "running" if _port_is_listening(self.settings.listen_port) else "unhealthy"

    def start(self) -> int:
        """Start the agent if needed and wait for health."""
        self.settings.state_directory.mkdir(parents=True, exist_ok=True)
        self.settings.log_directory.mkdir(parents=True, exist_ok=True)

        current_status = self.status()
        if current_status == "running":
            print("reachy-agent is already running")
            return 0
        if current_status == "unhealthy":
            print("reachy-agent process exists but is unhealthy; stop it before restarting", file=sys.stderr)
            return 1

        self.settings.pid_path.unlink(missing_ok=True)
        command = [sys.executable, "-m", "reachy_mini_conversation_app.sandbox_audio"]
        with self.settings.log_path.open("ab", buffering=0) as log_file:
            process = subprocess.Popen(  # noqa: S603 - fixed internal command
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd="/sandbox",
                start_new_session=True,
                close_fds=True,
            )

        temporary_pid = self.settings.pid_path.with_suffix(".pid.tmp")
        temporary_pid.write_text(f"{process.pid}\n", encoding="utf-8")
        temporary_pid.replace(self.settings.pid_path)

        ready = _wait_until(
            lambda: process.poll() is None and _port_is_listening(self.settings.listen_port),
            self.settings.startup_timeout_seconds,
        )
        if ready:
            print(f"reachy-agent started pid={process.pid}")
            return 0

        self._signal_process(process.pid, signal.SIGTERM)
        _wait_until(lambda: not _process_exists(process.pid), 2.0)
        if _process_exists(process.pid):
            self._signal_process(process.pid, signal.SIGKILL)
        self.settings.pid_path.unlink(missing_ok=True)
        print(f"reachy-agent failed to become healthy; inspect {self.settings.log_path}", file=sys.stderr)
        return 1

    def stop(self) -> int:
        """Stop the agent process group and clear its PID file."""
        pid = _read_pid(self.settings.pid_path)
        if pid is None or not _process_exists(pid):
            self.settings.pid_path.unlink(missing_ok=True)
            print("reachy-agent is already stopped")
            return 0

        self._signal_process(pid, signal.SIGTERM)
        stopped = _wait_until(lambda: not _process_exists(pid), self.settings.shutdown_timeout_seconds)
        if not stopped:
            self._signal_process(pid, signal.SIGKILL)
            stopped = _wait_until(lambda: not _process_exists(pid), 2.0)
        if stopped:
            self.settings.pid_path.unlink(missing_ok=True)
            print("reachy-agent stopped")
            return 0
        print(f"reachy-agent pid={pid} did not stop", file=sys.stderr)
        return 1

    @staticmethod
    def _signal_process(pid: int, requested_signal: signal.Signals) -> None:
        try:
            os.killpg(pid, requested_signal)
        except ProcessLookupError:
            return


def build_parser() -> argparse.ArgumentParser:
    """Build the lifecycle CLI parser."""
    parser = argparse.ArgumentParser(description="Control the Reachy agent inside an OpenShell sandbox")
    parser.add_argument("command", choices=["start", "stop", "status"])
    return parser


def main() -> None:
    """Run the sandbox lifecycle command."""
    args = build_parser().parse_args()
    control = SandboxAgentControl()
    if args.command == "start":
        raise SystemExit(control.start())
    if args.command == "stop":
        raise SystemExit(control.stop())
    status = control.status()
    print(status)
    raise SystemExit(0 if status == "running" else 1)


if __name__ == "__main__":
    main()
