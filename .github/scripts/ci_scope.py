# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Select new projects; existing projects and other PR types are out of scope."""

from pathlib import PurePosixPath

PROJECT_TASKS = {
    "tool": "review-tool",
    "research": "review-research-spike",
    "use-case-example": "review-use-case-example",
}
PROJECT_KIND_BY_DIRECTORY = {
    "tools": "tool",
    "research": "research",
    "use-case-examples": "use-case-example",
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
            len(path.parts) >= 4
            and path.parts[0] == "projects"
            and path.parts[1] in PROJECT_KIND_BY_DIRECTORY
        ):
            project = f"projects/{path.parts[1]}/{path.parts[2]}"
            if project not in existing_projects:
                candidates.add(project)
    return sorted(candidates)


def project_task(path, index):
    project_path = PurePosixPath(path)
    parts = project_path.parts
    if (
        project_path.is_absolute()
        or ".." in parts
        or str(project_path) != path
        or len(parts) != 3
        or parts[0] != "projects"
        or parts[1] not in PROJECT_KIND_BY_DIRECTORY
    ):
        raise ValueError(f"Invalid project path: {path!r}")
    kind = PROJECT_KIND_BY_DIRECTORY[parts[1]]
    return {
        "id": f"review-{index}",
        "task": PROJECT_TASKS[kind],
        "kind": kind,
        "input": path,
        "label": path,
    }
