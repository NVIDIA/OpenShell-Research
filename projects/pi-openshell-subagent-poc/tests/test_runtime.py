from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from openshell_tool_service.config import Settings
from openshell_tool_service.runtime import (
    MAX_OUTPUT_BYTES,
    OpenShellCliParentPolicySource,
    OpenShellCliRuntime,
    ParentPolicyUnavailableError,
    RuntimeExecutionError,
)
from openshell_tool_service.store import Job


def job() -> Job:
    return Job(
        id="1234567890abcdef",
        caller_id="pi-parent",
        step_index=0,
        prompt="Return OPEN_SHELL_CHILD_OK",
        prompt_digest="digest",
        child_policy="version: 1\nnetwork_policies: {}",
        state="queued",
        sandbox_name="pi-child-1234567890",
        output=None,
        stderr=None,
        exit_code=None,
        failure_code=None,
        failure_message=None,
        cleanup_error=None,
        sandbox_logs=None,
        sandbox_log_error=None,
        created_at=0,
        updated_at=0,
    )


def settings(tmp_path: Path) -> Settings:
    models = tmp_path / "models.json"
    models.write_text("{}\n")
    return Settings(
        token="token",
        database_path=tmp_path / "jobs.sqlite3",
        gateway="gateway",
        workspace="workspace",
        child_provider="poc-openai",
        child_models_file=models,
    )


def phases(calls: list[list[str]]) -> list[str]:
    return ["logs" if call[1] == "logs" else call[2] for call in calls]


def test_runtime_creates_executes_captures_logs_and_deletes(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    inputs: list[str | None] = []

    def runner(
        command: Sequence[str], input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append(argv)
        inputs.append(input_text)
        stdout = (
            "OPEN_SHELL_CHILD_OK\n"
            if "exec" in argv
            else "captured sandbox log\n"
            if "logs" in argv
            else ""
        )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    result = OpenShellCliRuntime(settings(tmp_path), runner).run(job())

    assert result.output == "OPEN_SHELL_CHILD_OK"
    assert result.sandbox_logs == "captured sandbox log\n"
    assert phases(calls) == ["create", "exec", "logs", "delete"]
    assert inputs[1] == "Return OPEN_SHELL_CHILD_OK"
    assert "--provider" in calls[0]
    assert "--upload" in calls[0]
    assert "PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent" in calls[0]
    assert not any("COLLABORATION" in value for value in calls[0])
    assert "--extension" not in calls[1]


def test_parent_policy_source_reads_live_policy(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(
            list(command),
            0,
            stdout='{"version":1,"config_revision":9,"policy":{"version":1,'
            '"network_policies":{}}}\n',
            stderr="",
        )

    policy = OpenShellCliParentPolicySource(settings(tmp_path), runner).get("pi-parent")
    assert policy == '{"network_policies":{},"version":1}'
    assert calls[0][:4] == ["openshell", "policy", "get", "pi-parent"]


def test_parent_policy_source_fails_closed(tmp_path: Path) -> None:
    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), 1, stdout="", stderr="not found")

    with pytest.raises(ParentPolicyUnavailableError, match="not found"):
        OpenShellCliParentPolicySource(settings(tmp_path), runner).get("pi-parent")


def test_runtime_deletes_after_child_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append(argv)
        returncode = 9 if "exec" in argv else 0
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="failed")

    with pytest.raises(RuntimeExecutionError, match="Pi failed"):
        OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert phases(calls) == ["create", "exec", "logs", "delete"]


def test_runtime_deletes_after_create_reports_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append(argv)
        if "create" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="rejected")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="not found")

    with pytest.raises(RuntimeExecutionError) as raised:
        OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _delay: None).run(job())
    assert raised.value.code == "sandbox-create"
    assert phases(calls) == ["create", "delete"]


def test_runtime_retries_transient_create_transport_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    attempts = 0

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        argv = list(command)
        calls.append(argv)
        if "create" in argv:
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr="transport error: tls handshake eof"
                )
        stdout = "DONE\n" if "exec" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    result = OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _delay: None).run(job())
    assert result.output == "DONE"
    assert phases(calls) == ["create", "delete", "create", "exec", "logs", "delete"]


def test_runtime_retries_transient_exec_transport_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    attempts = 0

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        argv = list(command)
        calls.append(argv)
        if "exec" in argv:
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    stdout="",
                    stderr="failed to establish ssh transport: connection reset by peer",
                )
            return subprocess.CompletedProcess(argv, 0, stdout="DONE\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _delay: None).run(job())
    assert result.output == "DONE"
    assert phases(calls) == ["create", "exec", "exec", "logs", "delete"]


@pytest.mark.parametrize("stdout,stderr", [
    ("", "connection reset by peer"),
    ("", "tls handshake eof"),
    ("task already ran", "failed to establish ssh transport"),
])
def test_runtime_does_not_replay_ambiguous_exec_failure(tmp_path, stdout, stderr):
    calls = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        if "exec" in command:
            return subprocess.CompletedProcess(command, 1, stdout=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(RuntimeExecutionError):
        OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _: None).run(job())
    assert phases(calls) == ["create", "exec", "logs", "delete"]


@pytest.mark.parametrize("diagnostic,absent", [
    ("sandbox 'pi-child-1234567890' not found", True),
    ("sandbox pi-child-1234567890 does not exist", True),
    ("gateway 'gateway' not found", False),
    ("sandbox 'another-child' not found", False),
    ("executable file not found", False),
])
def test_cleanup_only_accepts_absence_of_the_requested_sandbox(tmp_path, diagnostic, absent):
    calls = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=diagnostic)

    error = OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _: None).cleanup(job())
    assert (error is None) == absent
    assert len(calls) == (1 if absent else 3)


@pytest.mark.parametrize("phase", ["create", "exec"])
@pytest.mark.parametrize("error_type", [OSError, subprocess.TimeoutExpired])
def test_command_exceptions_preserve_diagnostics_and_cleanup(
    tmp_path: Path, phase: str, error_type: type[Exception]
) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, timeout):
        calls.append(list(command))
        if phase in command:
            if error_type is OSError:
                raise OSError("transport unavailable")
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, stdout="logs", stderr="")

    config = settings(tmp_path)
    with pytest.raises(RuntimeExecutionError) as raised:
        OpenShellCliRuntime(config, runner).run(job())
    assert raised.value.code == ("runtime-os-error" if error_type is OSError else "timeout")
    assert raised.value.stderr
    assert phases(calls) == (
        ["create", "delete"] if phase == "create" else ["create", "exec", "logs", "delete"]
    )
    assert not (config.database_path.parent / "policies" / f"{job().id}.yaml").exists()


@pytest.mark.parametrize(
    "output,code",
    [(" \n", "empty-output"), ("x" * (MAX_OUTPUT_BYTES + 1), "output-too-large")],
    ids=["empty", "oversized"],
)
def test_invalid_output_still_captures_logs_and_deletes(
    tmp_path: Path, output: str, code: str
) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(
            command, 0, stdout=output if "exec" in command else "log tail", stderr="diagnostic"
        )

    with pytest.raises(RuntimeExecutionError) as raised:
        OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert raised.value.code == code
    assert raised.value.stderr == "diagnostic"
    assert raised.value.sandbox_logs == "log tail"
    assert phases(calls)[-2:] == ["logs", "delete"]


@pytest.mark.parametrize("failure", ["exit", "timeout", "os-error", "unexpected"])
def test_log_failure_does_not_lose_result_or_prevent_cleanup(tmp_path: Path, failure: str) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, timeout):
        calls.append(list(command))
        if "logs" in command:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, timeout)
            if failure == "os-error":
                raise OSError("logs unavailable")
            if failure == "unexpected":
                raise ValueError("unexpected log failure")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="logs unavailable")
        return subprocess.CompletedProcess(command, 0, stdout="DONE", stderr="")

    result = OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert result.output == "DONE"
    assert result.sandbox_log_error
    assert result.sandbox_logs is None
    assert phases(calls)[-1] == "delete"


@pytest.mark.parametrize("child_failed", [False, True])
def test_cleanup_failure_retains_task_outcome(tmp_path: Path, child_failed: bool) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        if "delete" in command:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="delete unavailable")
        failed = child_failed and "exec" in command
        return subprocess.CompletedProcess(
            command, 9 if failed else 0, stdout="DONE", stderr="pi error"
        )

    runtime = OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _delay: None)
    if child_failed:
        with pytest.raises(RuntimeExecutionError) as raised:
            runtime.run(job())
        assert raised.value.code == "child-exit"
        assert raised.value.exit_code == 9
        outcome = raised.value
    else:
        outcome = runtime.run(job())
        assert outcome.output == "DONE"
    assert outcome.cleanup_error == "delete unavailable"
    assert phases(calls)[-3:] == ["delete", "delete", "delete"]


def test_failed_partial_cleanup_prevents_create_retry(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        error = "transport error" if "create" in command else "delete unavailable"
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=error)

    with pytest.raises(RuntimeExecutionError) as raised:
        OpenShellCliRuntime(settings(tmp_path), runner, sleep=lambda _delay: None).run(job())
    assert phases(calls).count("create") == 1
    assert raised.value.code == "sandbox-create"
    assert raised.value.cleanup_error == "delete unavailable"


@pytest.mark.parametrize("phase", ["create", "exec"])
def test_unexpected_failure_preserves_cleanup_diagnostics(tmp_path: Path, phase: str) -> None:
    calls: list[list[str]] = []

    def runner(command, _input, _timeout):
        calls.append(list(command))
        if phase in command:
            raise ValueError("unexpected execution failure")
        if "delete" in command:
            raise ValueError("unexpected deletion failure")
        return subprocess.CompletedProcess(command, 0, stdout="log tail", stderr="")

    with pytest.raises(RuntimeExecutionError) as raised:
        OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert raised.value.code == "tool-service"
    assert isinstance(raised.value.__cause__, ValueError)
    assert raised.value.cleanup_error == "unexpected deletion failure"
    assert raised.value.sandbox_logs == ("log tail" if phase == "exec" else None)
    assert phases(calls).count("delete") == 1
    assert not list((tmp_path / "policies").glob("*.yaml"))
