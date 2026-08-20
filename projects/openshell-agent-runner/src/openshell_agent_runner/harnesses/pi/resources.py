# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Materialize the explicit native-upload runtime bundle for Pi."""

import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from openshell_agent_runner.config import (
    MODELS_FILENAME,
    SETTINGS_FILENAME,
    ResolvedProfile,
)
from openshell_agent_runner.harnesses.resources import PreparedResources

SANDBOX_RUNTIME_ROOT = "/sandbox/oar-runtime"


def image_directory() -> Path:
    return Path(str(files("openshell_agent_runner.harnesses.pi") / "runtime" / "image"))


def prepare_resources(resolved: ResolvedProfile, task_id: str) -> PreparedResources:
    temporary = tempfile.TemporaryDirectory(prefix="oar-pi-")
    runtime = Path(temporary.name) / "runtime"
    (runtime / "skills").mkdir(parents=True, exist_ok=True)
    (runtime / "extensions").mkdir(parents=True, exist_ok=True)
    task = resolved.profile.tasks[task_id]
    shutil.copy2(resolved.profile_dir / task.prompt, runtime / "prompt.md")
    shutil.copy2(resolved.profile_dir / MODELS_FILENAME, runtime / MODELS_FILENAME)
    shutil.copy2(resolved.profile_dir / SETTINGS_FILENAME, runtime / SETTINGS_FILENAME)
    arguments = [
        "--provider",
        resolved.runtime.provider,
        "--model",
        resolved.runtime.model,
        "--thinking",
        resolved.runtime.thinking,
    ]
    tools = list(task.tools)
    if task.output_schema is not None:
        shutil.copy2(
            resolved.profile_dir / task.output_schema, runtime / "output.schema.json"
        )
        submit_result = Path(
            str(
                files("openshell_agent_runner.harnesses.pi")
                / "runtime"
                / "extensions"
                / "submit-result.ts"
            )
        )
        shutil.copy2(submit_result, runtime / "extensions" / "oar-submit-result.ts")
        tools.append("submit_result")
        arguments.extend(
            [
                "--extension",
                f"{SANDBOX_RUNTIME_ROOT}/extensions/oar-submit-result.ts",
            ]
        )
    arguments.extend(["--tools", ",".join(tools)] if tools else ["--no-tools"])
    for index, skill in enumerate(task.skills):
        target = runtime / "skills" / f"{index:02d}-{skill.name}"
        shutil.copytree(resolved.profile_dir / skill, target)
        arguments.extend(["--skill", f"{SANDBOX_RUNTIME_ROOT}/skills/{target.name}"])
    for index, extension in enumerate(task.extensions):
        target = runtime / "extensions" / f"{index:02d}-{extension.name}"
        shutil.copy2(resolved.profile_dir / extension, target)
        arguments.extend(
            ["--extension", f"{SANDBOX_RUNTIME_ROOT}/extensions/{target.name}"]
        )
    uploads = [
        f"{runtime / 'prompt.md'}:{SANDBOX_RUNTIME_ROOT}/prompt.md",
        f"{runtime / 'models.json'}:{SANDBOX_RUNTIME_ROOT}/models.json",
        f"{runtime / 'settings.json'}:{SANDBOX_RUNTIME_ROOT}/settings.json",
    ]
    if task.output_schema is not None:
        uploads.append(
            f"{runtime / 'output.schema.json'}:{SANDBOX_RUNTIME_ROOT}/output.schema.json"
        )
    uploads.extend(
        f"{path}:{SANDBOX_RUNTIME_ROOT}/skills"
        for path in sorted((runtime / "skills").iterdir())
    )
    uploads.extend(
        f"{path}:{SANDBOX_RUNTIME_ROOT}/extensions/{path.name}"
        for path in sorted((runtime / "extensions").iterdir())
    )
    return PreparedResources(temporary, tuple(uploads), tuple(arguments))
