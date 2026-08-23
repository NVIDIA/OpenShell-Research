# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from openshell_agent_runner.errors import ArtifactError, ExecutionError
from openshell_agent_runner.runner import (
    RunRequest,
    render_dry_run,
    resolve_run,
    run_agent,
)


def fixture(tmp_path: Path) -> Path:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("Return the configured output.\n")
    (tmp_path / "models.json").write_text(
        '{"providers":{"openshell":{"models":[{"id":"fake-model"}]}}}'
    )
    (tmp_path / "settings.json").write_text(
        '{"defaultProvider":"openshell","defaultModel":"fake-model",'
        '"defaultThinkingLevel":"high"}'
    )
    (tmp_path / "output.schema.json").write_text(
        '{"type":"object","additionalProperties":false,"required":["status"],'
        '"properties":{"status":{"const":"pass"}}}'
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Fake OpenShell contract profile.
sandbox:
  policy: policy.yaml
tasks:
  smoke:
    prompt: prompt.md
    output_schema: output.schema.json
"""
    )
    return tmp_path


def fake_openshell(tmp_path: Path) -> tuple[Path, Path, Path]:
    executable = tmp_path / "openshell"
    state = tmp_path / "state.json"
    log = tmp_path / "commands.jsonl"
    executable.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["FAKE_STATE"])
log = pathlib.Path(os.environ["FAKE_LOG"])
with log.open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
operation = args[1]
if operation == "create":
    if "--upload" in args and "--" in args: sys.exit(2)
    name = args[args.index("--name") + 1]
    labels = [args[index + 1] for index, item in enumerate(args) if item == "--label"]
    token = next(item.split("=", 1)[1] for item in labels if item.startswith("oar-run-id="))
    state.write_text(json.dumps({"name": name, "labels": {"oar-run-id": token}}))
    if os.environ.get("FAKE_FAIL_CREATE") == "1": sys.exit(1)
    if os.environ.get("FAKE_SLEEP_CREATE") == "1":
        import time; time.sleep(5)
elif operation in {"upload", "exec"}:
    pass
elif operation == "get":
    if not state.exists(): sys.exit(1)
    document = json.loads(state.read_text())
    if os.environ.get("FAKE_COLLISION") == "1": document["labels"]["oar-run-id"] = "wrong"
    print(json.dumps(document))
elif operation == "download":
    if os.environ.get("FAKE_FAIL_DOWNLOAD") == "1": sys.exit(1)
    if os.environ.get("FAKE_DIRECTORY_OUTPUT") == "1":
        if pathlib.Path(args[4]).is_file(): sys.exit(1)
        pathlib.Path(args[4]).mkdir()
        for index in range(4): (pathlib.Path(args[4]) / str(index)).write_bytes(b"x" * (512 * 1024))
        sys.exit(0)
    fallback = json.dumps({"status": "pass"})
    output = "x" * (2 * 1024 * 1024) if os.environ.get("FAKE_LARGE_OUTPUT") == "1" else os.environ.get("FAKE_OUTPUT", fallback)
    pathlib.Path(args[4]).write_text(output + "\\n")
elif operation == "delete":
    if os.environ.get("FAKE_FAIL_DELETE") == "1": sys.exit(1)
    state.unlink(missing_ok=True)
else:
    sys.exit(8)
"""
    )
    executable.chmod(0o755)
    return executable, state, log


def request(profile: Path, executable: Path, output: Path) -> RunRequest:
    return RunRequest(
        profile_directory=profile,
        task_id="smoke",
        output=output,
        openshell_bin=str(executable),
        uploads=(".:/workspace/source",),
        timeout_seconds=30,
    )


def prepare(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path, Path]:
    profile = fixture(tmp_path)
    executable, state, log = fake_openshell(tmp_path)
    monkeypatch.setenv("FAKE_STATE", str(state))
    monkeypatch.setenv("FAKE_LOG", str(log))
    return profile, executable, state, log


def test_create_download_owned_delete_order(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    output = tmp_path / "result.json"

    name = run_agent(request(profile, executable, output))

    assert len(name) == 19
    assert json.loads(output.read_text())["status"] == "pass"
    assert not state.exists()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    operations = [command[1] for command in commands]
    assert operations[0] == "create"
    assert operations.count("upload") == 8
    assert operations[-4:] == ["exec", "download", "get", "delete"]


def test_resolved_command_is_the_create_prefix(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    item = request(profile, executable, tmp_path / "result.json")
    resolved = resolve_run(item)

    run_agent(item)

    create = json.loads(log.read_text().splitlines()[0])
    assert create[: len(resolved.create_command) - 1] == list(
        resolved.create_command[1:]
    )
    assert "--upload" not in create
    assert create[-4:] == ["--detach", "--", "sleep", "infinity"]
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    harness = next(command for command in commands if command[1] == "exec")
    harness = harness[harness.index("--") :]
    assert harness[:3] == ["--", "bash", "/opt/oar/pi/exec.sh"]
    assert harness[harness.index("--provider") + 1] == "openshell"
    assert harness[harness.index("--model") + 1] == "fake-model"
    assert harness[harness.index("--thinking") + 1] == "high"
    uploads = [command for command in commands if command[1] == "upload"]
    assert any(
        command[4] == "/sandbox/oar-runtime/output.schema.json" for command in uploads
    )
    assert not state.exists()


def test_dry_run_prints_every_command_without_executing(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    output = tmp_path / "result.json"

    preview = render_dry_run(request(profile, executable, output))

    assert "Dry run: no commands were executed." in preview
    assert "[create]" in preview
    assert "sandbox create" in preview
    assert "[upload]" in preview
    assert "sandbox upload" in preview
    assert "[execute]" in preview
    assert "sandbox exec" in preview
    assert "[download]" in preview
    assert "sandbox download" in preview
    assert "[verify ownership]" in preview
    assert "sandbox get" in preview
    assert "[delete]" in preview
    assert "sandbox delete" in preview
    assert "/sandbox/oar-runtime/output.schema.json" in preview
    assert f"[publish] atomically replace {output}" in preview
    assert not state.exists()
    assert not log.exists()
    assert not output.exists()


def test_keep_sandbox_dry_run_omits_cleanup_commands(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    item = replace(
        request(profile, executable, tmp_path / "result.json"), keep_sandbox=True
    )

    preview = render_dry_run(item)

    assert "sandbox get" not in preview
    assert "sandbox delete" not in preview
    assert "[cleanup] skipped" in preview
    assert not state.exists()
    assert not log.exists()


def test_keep_sandbox_skips_inspection_and_delete(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    item = replace(
        request(profile, executable, tmp_path / "result.json"), keep_sandbox=True
    )
    run_agent(item)
    assert state.exists()
    assert [json.loads(line)[1] for line in log.read_text().splitlines()] == [
        "create",
        *(["upload"] * 8),
        "exec",
        "download",
    ]


def test_keep_sandbox_reports_name_after_artifact_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_OUTPUT", "{}")
    item = replace(
        request(profile, executable, tmp_path / "result.json"), keep_sandbox=True
    )

    with pytest.raises(ArtifactError):
        run_agent(item)

    assert state.exists()
    assert "oar: sandbox name (--keep-sandbox): oar-" in capsys.readouterr().err


def test_timeout_cleans_owned_sandbox(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_SLEEP_CREATE", "1")
    item = replace(
        request(profile, executable, tmp_path / "result.json"), timeout_seconds=1
    )
    with pytest.raises(ExecutionError):
        run_agent(item)
    assert not state.exists()


def test_collision_refuses_delete(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_COLLISION", "1")
    with pytest.raises(ExecutionError, match="mismatched ownership"):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert state.exists()


def test_malformed_ownership_response_refuses_delete(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    import openshell_agent_runner.openshell as openshell

    original = openshell.run

    def malformed_get(command, timeout, *, capture=False, max_file_bytes=None):
        result = original(
            command,
            timeout,
            capture=capture,
            max_file_bytes=max_file_bytes,
        )
        if command[1:3] == ["sandbox", "get"]:
            return subprocess.CompletedProcess(
                result.args,
                result.returncode,
                '{"name": "wrong-shape", "labels": []}\n',
                result.stderr,
            )
        return result

    monkeypatch.setattr(openshell, "run", malformed_get)
    with pytest.raises(ExecutionError, match="mismatched ownership"):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert state.exists()


def test_invalid_output_still_cleans(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_OUTPUT", "{}")
    with pytest.raises(ArtifactError):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert not state.exists()


def test_cleanup_failure_does_not_mask_primary_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    profile, executable, _, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_FAIL_CREATE", "1")
    monkeypatch.setenv("FAKE_FAIL_DELETE", "1")
    with pytest.raises(ExecutionError, match="sandbox create"):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert "cleanup failed after primary error" in capsys.readouterr().err


def test_cleanup_failure_after_success_is_reported(tmp_path: Path, monkeypatch) -> None:
    profile, executable, _, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_FAIL_DELETE", "1")
    with pytest.raises(ExecutionError, match="sandbox delete"):
        run_agent(request(profile, executable, tmp_path / "result.json"))


def test_interrupt_preserves_interrupt_and_cleans(tmp_path: Path, monkeypatch) -> None:
    import openshell_agent_runner.openshell as openshell

    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    original = openshell.run
    interrupted = False

    def interrupt_after_create(command, timeout, *, capture=False, max_file_bytes=None):
        nonlocal interrupted
        result = original(
            command,
            timeout,
            capture=capture,
            max_file_bytes=max_file_bytes,
        )
        if command[1:3] == ["sandbox", "create"] and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(openshell, "run", interrupt_after_create)
    with pytest.raises(KeyboardInterrupt):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert not state.exists()


def test_download_failure_cleans(tmp_path: Path, monkeypatch) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_FAIL_DOWNLOAD", "1")
    with pytest.raises(ExecutionError, match="sandbox download"):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert not state.exists()


def test_oversized_download_is_stopped_during_transfer(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LARGE_OUTPUT", "1")
    output = tmp_path / "result.json"

    with pytest.raises(ExecutionError, match="sandbox download"):
        run_agent(request(profile, executable, output))

    assert not output.exists()
    assert not state.exists()


def test_directory_result_is_rejected_before_transfer(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_DIRECTORY_OUTPUT", "1")
    output = tmp_path / "result.json"

    with pytest.raises(ExecutionError, match="sandbox download"):
        run_agent(request(profile, executable, output))

    assert not output.exists()
    assert not state.exists()


def test_create_failure_still_deletes_owned_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    profile, executable, state, log = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_FAIL_CREATE", "1")
    with pytest.raises(ExecutionError):
        run_agent(request(profile, executable, tmp_path / "result.json"))
    assert not state.exists()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert [command[1] for command in commands] == ["create", "get", "delete"]
