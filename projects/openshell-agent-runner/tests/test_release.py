# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
PUBLISH_SCRIPT = REPOSITORY / "projects/openshell-agent-runner/scripts/publish.sh"


def test_publish_prints_tag_deletion_commands_for_existing_remote_tag(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    script = project / "scripts/publish.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PUBLISH_SCRIPT, script)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "git",
        """
        #!/usr/bin/env bash
        if [[ "$1" == "status" ]]; then exit 0; fi
        if [[ "$1" == "branch" ]]; then printf 'main\\n'; exit 0; fi
        if [[ "$1" == "fetch" ]]; then exit 0; fi
        if [[ "$1" == "rev-parse" && "$2" == "--verify" ]]; then exit 0; fi
        if [[ "$1" == "rev-parse" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "rev-list" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "ls-remote" ]]; then
            printf 'abc123\\trefs/tags/v0.1.0\\n'
            exit 0
        fi
        exit 91
        """,
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    result = subprocess.run(
        ["bash", str(script), "0.1.0"],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 1
    assert "remote tag 'v0.1.0' already exists" in result.stderr
    assert "git tag -d 'v0.1.0'" in result.stderr
    assert "git push origin --delete 'v0.1.0'" in result.stderr


@pytest.mark.parametrize(
    ("first_accepts_wheel", "retry_artifact"),
    [(True, "sdist"), (False, "both")],
)
def test_publish_retry_uploads_only_missing_artifacts(
    tmp_path: Path, first_accepts_wheel: bool, retry_artifact: str
) -> None:
    project = tmp_path / "project"
    script = project / "scripts/publish.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PUBLISH_SCRIPT, script)
    entrypoint = (
        project / "src/openshell_agent_runner/harnesses/pi/runtime/image/exec.sh"
    )
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/usr/bin/env bash\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "git",
        """
        #!/usr/bin/env bash
        if [[ "$1" == "status" ]]; then exit 0; fi
        if [[ "$1" == "branch" ]]; then printf 'main\\n'; exit 0; fi
        if [[ "$1" == "fetch" ]]; then exit 0; fi
        if [[ "$1" == "rev-parse" && "$2" == "--verify" ]]; then
            test -e "$FAKE_GIT_STATE/local-tag"
            exit
        fi
        if [[ "$1" == "rev-parse" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "rev-list" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "ls-remote" ]]; then
            if [[ -e "$FAKE_GIT_STATE/remote-tag" ]]; then
                printf 'abc123\\trefs/tags/v0.1.0\\n'
            fi
            exit 0
        fi
        if [[ "$1" == "tag" && "$2" == "-d" ]]; then
            rm -f "$FAKE_GIT_STATE/local-tag"
            exit 0
        fi
        if [[ "$1" == "tag" ]]; then
            touch "$FAKE_GIT_STATE/local-tag"
            exit 0
        fi
        if [[ "$1" == "push" ]]; then
            touch "$FAKE_GIT_STATE/remote-tag"
            exit 0
        fi
        exit 91
        """,
    )
    _write_executable(
        fake_bin / "uv",
        """
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "$FAKE_UV_LOG"
        if [[ "$1" == "build" ]]; then
            mkdir -p dist
            : > dist/openshell_agent_runner-0.1.0-py3-none-any.whl
            : > dist/openshell_agent_runner-0.1.0.tar.gz
        fi
        if [[ "$1" == "publish" && "$*" != *"--dry-run"* ]]; then
            if [[ "$*" == *".whl"* && "$*" == *".tar.gz"* ]]; then
                if [[ ! -e "$FAKE_REPOSITORY_STATE/attempted" ]]; then
                    touch "$FAKE_REPOSITORY_STATE/attempted"
                    if [[ "$FAKE_FIRST_ACCEPTS_WHEEL" == "true" ]]; then
                        touch "$FAKE_REPOSITORY_STATE/wheel"
                    fi
                    exit 93
                fi
                touch "$FAKE_REPOSITORY_STATE/wheel"
                touch "$FAKE_REPOSITORY_STATE/sdist"
                exit 0
            fi
            if [[ "$*" == *".tar.gz"* ]]; then
                touch "$FAKE_REPOSITORY_STATE/sdist"
                exit 0
            fi
        fi
        exit 0
        """,
    )

    uv_log = tmp_path / "uv.log"
    git_state = tmp_path / "git-state"
    repository_state = tmp_path / "repository-state"
    git_state.mkdir()
    repository_state.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_UV_LOG"] = str(uv_log)
    environment["FAKE_GIT_STATE"] = str(git_state)
    environment["FAKE_REPOSITORY_STATE"] = str(repository_state)
    environment["FAKE_FIRST_ACCEPTS_WHEEL"] = str(first_accepts_wheel).lower()
    environment["UV_PUBLISH_TOKEN"] = "test-token"

    first_attempt = subprocess.run(
        ["bash", str(script), "0.1.0"],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert first_attempt.returncode == 1
    assert (repository_state / "wheel").exists() is first_accepts_wheel
    assert not (repository_state / "sdist").exists()

    retry = subprocess.run(
        ["bash", str(script), "0.1.0", "--retry-artifact", retry_artifact],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert retry.returncode == 0, retry.stderr
    assert (repository_state / "sdist").exists()
    publish_commands = [
        line for line in uv_log.read_text().splitlines() if line.startswith("publish ")
    ]
    assert all("--trusted-publishing never" in line for line in publish_commands)
    uploads = [line for line in publish_commands if "--dry-run" not in line]
    assert ".whl" in uploads[0] and ".tar.gz" in uploads[0]
    assert (".whl" in uploads[1]) is (retry_artifact == "both")
    assert ".tar.gz" in uploads[1]
    assert (
        "PyPI: https://pypi.org/project/openshell-agent-runner/0.1.0/" in retry.stdout
    )
    if retry_artifact == "sdist":
        assert "Uploaded the missing sdist artifact" in retry.stdout
        assert "Published openshell-agent-runner" not in retry.stdout


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip())
    path.chmod(0o755)
