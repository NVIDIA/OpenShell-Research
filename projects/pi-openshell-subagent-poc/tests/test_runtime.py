from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from openshell_tool_service.config import Settings
from openshell_tool_service.runtime import (
    OpenShellCliParentPolicySource,
    OpenShellCliRuntime,
    ParentPolicyUnavailableError,
    RuntimeExecutionError,
)
from openshell_tool_service.store import Job


def job() -> Job:
    return Job(
        id="1234567890abcdef",
        caller_id="sandbox-1",
        run_id="run-1",
        step_index=0,
        agent="openshell-worker",
        prompt="Return OPEN_SHELL_CHILD_OK",
        prompt_digest="digest",
        profile="worker",
        github_repositories=(),
        child_policy="version: 1\nnetwork_policies: {}",
        state="queued",
        sandbox_name="pi-child-1234567890",
        output=None,
        stderr=None,
        exit_code=None,
        failure_code=None,
        failure_message=None,
        cleanup_error=None,
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


def test_runtime_creates_executes_and_deletes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[list[str], str | None, int]] = []

    def runner(
        command: Sequence[str], input_text: str | None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append((argv, input_text, timeout))
        stdout = "OPEN_SHELL_CHILD_OK\n" if "exec" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    caplog.set_level(logging.DEBUG, logger="openshell_tool_service.runtime")
    result = OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert result.output == "OPEN_SHELL_CHILD_OK"
    assert [call[0][2] for call in calls] == ["create", "exec", "delete"]
    assert calls[1][1] == "Return OPEN_SHELL_CHILD_OK"
    assert "--provider" in calls[0][0]
    assert "--upload" in calls[0][0]
    upload_index = calls[0][0].index("--upload")
    assert calls[0][0][upload_index + 1].endswith(":/home/sandbox/.pi/agent")
    assert "PI_CODING_AGENT_DIR=/home/sandbox/.pi/agent" in calls[0][0]
    assert "--detach" in calls[0][0]
    assert "/bin/true" not in calls[0][0]
    assert "job 12345678 creating sandbox pi-child-1234567890" in caplog.text
    assert "job 12345678 sandbox ready" in caplog.text
    assert "job 12345678 running Pi" in caplog.text
    assert "job 12345678 Pi completed" in caplog.text
    assert "job 12345678 sandbox deleted" in caplog.text
    assert "prompt_sha256=" in caplog.text
    assert "policy_sha256=" in caplog.text
    assert "Return OPEN_SHELL_CHILD_OK" not in caplog.text
    assert "version: 1" not in caplog.text


def test_parent_policy_source_reads_active_policy_from_openshell(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(
        command: Sequence[str], _input_text: str | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"version":1,"config_revision":9,"policy":{"version":1,'
            '"network_policies":{}}}\n',
            stderr="",
        )

    policy = OpenShellCliParentPolicySource(settings(tmp_path), runner).get("pi-parent")

    assert policy == '{"network_policies":{},"version":1}'
    assert calls == [
        [
            "openshell",
            "policy",
            "get",
            "pi-parent",
            "--workspace",
            "workspace",
            "--gateway",
            "gateway",
            "--full",
            "--output",
            "json",
        ]
    ]


def test_parent_policy_source_fails_on_openshell_error(tmp_path: Path) -> None:
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

    with pytest.raises(RuntimeExecutionError, match="Pi failed") as raised:
        OpenShellCliRuntime(settings(tmp_path), runner).run(job())
    assert raised.value.code == "child-exit"
    assert [call[2] for call in calls] == ["create", "exec", "delete"]


def test_runtime_applies_parent_authored_policy_and_removes_it(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, int]] = []
    repo_job = Job(
        **{
            **job().__dict__,
            "id": "repojob1234567890",
            "sandbox_name": "pi-child-repojob123",
            "prompt": "Clone https://github.com/NVIDIA/OpenShell and review it",
            "github_repositories": ("NVIDIA/OpenShell",),
            "child_policy": """version: 1
network_policies:
  parent_authored:
    name: exact-parent-policy
    endpoints:
      - host: github.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow:
              method: GET
              path: /NVIDIA/OpenShell.git/info/refs
    binaries:
      - path: /usr/bin/git""",
        }
    )

    def runner(
        command: Sequence[str], input_text: str | None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        calls.append((argv, input_text, timeout))
        if "create" in argv:
            policy_path = Path(argv[argv.index("--policy") + 1])
            policy = policy_path.read_text()
            assert "name: exact-parent-policy" in policy
            assert "path: /NVIDIA/OpenShell.git/info/refs" in policy
            assert "path: /usr/bin/git" in policy
        stdout = "REVIEW_OK\n" if "exec" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    result = OpenShellCliRuntime(settings(tmp_path), runner).run(repo_job)
    assert result.output == "REVIEW_OK"
    create_argv = calls[0][0]
    policy_path = Path(create_argv[create_argv.index("--policy") + 1])
    assert not policy_path.exists()
