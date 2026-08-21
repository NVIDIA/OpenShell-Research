# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
PUBLISH_SCRIPT = REPOSITORY / "projects/openshell-agent-runner/scripts/publish.sh"


def test_publish_retry_skips_an_artifact_already_in_the_repository(
    tmp_path: Path,
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
        if [[ "$1" == "rev-parse" && "$2" == "--verify" ]]; then exit 0; fi
        if [[ "$1" == "rev-parse" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "rev-list" ]]; then printf 'abc123\\n'; exit 0; fi
        if [[ "$1" == "ls-remote" ]]; then
            printf 'abc123\\trefs/tags/v0.1.0\\n'
            exit 0
        fi
        if [[ "$1" == "push" ]]; then exit 90; fi
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
        if [[ "$*" == *"twine upload"* && "$*" != *"--skip-existing"* ]]; then
            exit 92
        fi
        exit 0
        """,
    )

    uv_log = tmp_path / "uv.log"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_UV_LOG"] = str(uv_log)

    completed = subprocess.run(
        ["bash", str(script), "0.1.0"],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    upload = next(
        line for line in uv_log.read_text().splitlines() if "twine upload" in line
    )
    assert "--skip-existing" in upload


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip())
    path.chmod(0o755)
