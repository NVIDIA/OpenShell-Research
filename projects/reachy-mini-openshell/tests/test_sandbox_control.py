"""Tests for in-sandbox lifecycle control."""

# ruff: noqa: D103

import signal
from pathlib import Path

from reachy_mini_conversation_app import sandbox_control
from reachy_mini_conversation_app.sandbox_control import ControlSettings, SandboxAgentControl


def settings(tmp_path: Path) -> ControlSettings:
    return ControlSettings(
        state_directory=tmp_path / "run",
        log_directory=tmp_path / "logs",
        startup_timeout_seconds=0.01,
        shutdown_timeout_seconds=0.01,
    )


def test_status_reports_stopped_without_pid(tmp_path: Path) -> None:
    assert SandboxAgentControl(settings(tmp_path)).status() == "stopped"


def test_start_is_idempotent_when_agent_is_healthy(tmp_path: Path, monkeypatch) -> None:
    configured = settings(tmp_path)
    configured.state_directory.mkdir(parents=True)
    configured.pid_path.write_text("123\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_control, "_process_exists", lambda pid: pid == 123)
    monkeypatch.setattr(sandbox_control, "_port_is_listening", lambda port: port == 8765)

    assert SandboxAgentControl(configured).start() == 0


def test_port_is_listening_reads_linux_tcp_table(tmp_path: Path, monkeypatch) -> None:
    tcp_table = tmp_path / "tcp"
    tcp_table.write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt\n"
        "   0: 0100007F:223D 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n",
        encoding="ascii",
    )

    real_path = sandbox_control.Path

    def fake_path(value: str):
        if value == "/proc/net/tcp":
            return tcp_table
        if value == "/proc/net/tcp6":
            return tmp_path / "missing"
        return real_path(value)

    monkeypatch.setattr(sandbox_control, "Path", fake_path)

    assert sandbox_control._port_is_listening(8765) is True
    assert sandbox_control._port_is_listening(8766) is False


def test_stop_terminates_process_group_and_removes_pid(tmp_path: Path, monkeypatch) -> None:
    configured = settings(tmp_path)
    configured.state_directory.mkdir(parents=True)
    configured.pid_path.write_text("321\n", encoding="utf-8")
    alive = True
    signals: list[tuple[int, signal.Signals]] = []

    def process_exists(pid: int) -> bool:
        assert pid == 321
        return alive

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        nonlocal alive
        signals.append((pid, requested_signal))
        alive = False

    monkeypatch.setattr(sandbox_control, "_process_exists", process_exists)
    monkeypatch.setattr(sandbox_control.os, "killpg", killpg)

    assert SandboxAgentControl(configured).stop() == 0
    assert signals == [(321, signal.SIGTERM)]
    assert not configured.pid_path.exists()
