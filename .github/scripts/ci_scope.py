# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Select new projects; existing projects and other PR types are out of scope."""

from pathlib import PurePosixPath

PROJECT_TASKS = {
    "tool": "review-tool",
    "research-spike": "review-research-spike",
    "use-case-example": "review-use-case-example",
}


def new_project_paths(files, existing_projects):
    candidates = set()
    for change in files:
        name = change["filename"]
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name:
            raise ValueError(f"Invalid repository path: {name!r}")
        if change["status"] == "removed":
            continue
        if (
            len(path.parts) >= 3
            and path.parts[0] == "projects"
            and path.parts[1] not in existing_projects
        ):
            candidates.add(f"projects/{path.parts[1]}")
    return sorted(candidates)


def project_task(path, metadata, index):
    if (
        not isinstance(metadata, dict)
        or not isinstance(metadata.get("kind"), str)
        or metadata["kind"] not in PROJECT_TASKS
    ):
        raise ValueError(
            f"{path}/project.yaml must declare kind: " + ", ".join(PROJECT_TASKS)
        )
    kind = metadata["kind"]
    return {
        "id": f"review-{index}",
        "task": PROJECT_TASKS[kind],
        "kind": kind,
        "input": path,
        "label": path,
    }
