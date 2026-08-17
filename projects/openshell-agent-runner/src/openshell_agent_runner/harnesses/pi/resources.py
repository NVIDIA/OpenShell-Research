# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Materialize the explicit native-upload runtime bundle for Pi."""

import json
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from openshell_agent_runner.config import ResolvedProfile
from openshell_agent_runner.document_review import document_review_schema
from openshell_agent_runner.harnesses.resources import PreparedResources


def assets_directory() -> Path:
    return Path(str(files("openshell_agent_runner.harnesses.pi") / "assets"))


def prepare_resources(
    resolved: ResolvedProfile, task_id: str, model: str
) -> PreparedResources:
    temporary = tempfile.TemporaryDirectory(prefix="oar-pi-")
    runtime = Path(temporary.name) / "runtime"
    (runtime / "skills").mkdir(parents=True, exist_ok=True)
    (runtime / "extensions").mkdir(parents=True, exist_ok=True)
    (runtime / "schemas").mkdir(parents=True, exist_ok=True)
    task = resolved.profile.tasks[task_id]
    shutil.copy2(resolved.profile_dir / task.prompt, runtime / "prompt.md")
    contract = task.output.contract
    schema = document_review_schema(
        reviewer_id=contract.reviewer_id,
        model_id=model,
        criteria=contract.criteria,
        max_findings=contract.max_findings,
    )
    (runtime / "schemas" / "output.schema.json").write_text(
        json.dumps(schema), encoding="utf-8"
    )
    arguments: list[str] = (
        ["--tools", ",".join(task.tools)] if task.tools else ["--no-tools"]
    )
    for index, skill in enumerate(task.skills):
        target = runtime / "skills" / f"{index:02d}-{skill.name}"
        shutil.copytree(resolved.profile_dir / skill, target)
        arguments.extend(["--skill", f"/sandbox/oar-runtime/skills/{target.name}"])
    for index, extension in enumerate(task.extensions):
        target = runtime / "extensions" / f"{index:02d}-{extension.name}"
        shutil.copy2(resolved.profile_dir / extension, target)
        arguments.extend(
            ["--extension", f"/sandbox/oar-runtime/extensions/{target.name}"]
        )
    models = {
        "providers": {
            "openshell": {
                "baseUrl": "https://inference.local/v1",
                "api": "openai-completions",
                "apiKey": "unused",
                "authHeader": True,
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [
                    {
                        "id": model,
                        "name": model,
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": resolved.profile.harness.context_window,
                        "maxTokens": resolved.profile.harness.max_tokens,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }
    (runtime / "models.json").write_text(json.dumps(models), encoding="utf-8")
    (runtime / "settings.json").write_text(
        json.dumps({"enableInstallTelemetry": False, "defaultProjectTrust": "never"}),
        encoding="utf-8",
    )
    uploads = [
        f"{runtime / 'prompt.md'}:/sandbox/oar-runtime/prompt.md",
        f"{runtime / 'models.json'}:/sandbox/oar-runtime/models.json",
        f"{runtime / 'settings.json'}:/sandbox/oar-runtime/settings.json",
    ]
    uploads.extend(
        f"{path}:/sandbox/oar-runtime/schemas/{path.name}"
        for path in sorted((runtime / "schemas").iterdir())
    )
    uploads.extend(
        f"{path}:/sandbox/oar-runtime/skills"
        for path in sorted((runtime / "skills").iterdir())
    )
    uploads.extend(
        f"{path}:/sandbox/oar-runtime/extensions/{path.name}"
        for path in sorted((runtime / "extensions").iterdir())
    )
    return PreparedResources(temporary, tuple(uploads), tuple(arguments))
