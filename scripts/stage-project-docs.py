#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage canonical project documentation in the site source tree."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DOCUMENTATION = {
    "egress-gate": ROOT / "projects" / "egress-gate" / "docs",
    "openshell-agent-runner": ROOT
    / "projects"
    / "openshell-agent-runner"
    / "docs",
}
DOCUMENTATION_ROOT = ROOT / "docs" / "documentation"


def stage_project_docs(source: Path, destination: Path) -> None:
    """Replace one generated site mirror with its canonical project-docs tree."""

    source = source.resolve()
    destination_is_symlink = destination.is_symlink()
    destination = destination.resolve(strict=False)
    if not source.is_dir():
        raise ValueError(f"documentation source does not exist: {source}")
    if destination_is_symlink:
        raise ValueError(f"documentation destination must not be a symlink: {destination}")
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("documentation source and destination must not overlap")

    symlinks = sorted(path for path in source.rglob("*") if path.is_symlink())
    if symlinks:
        paths = "\n".join(f"  - {path}" for path in symlinks)
        raise ValueError(f"project documentation must not contain symlinks:\n{paths}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)

    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staging, destination)


def main() -> int:
    for project, source in PROJECT_DOCUMENTATION.items():
        stage_project_docs(source, DOCUMENTATION_ROOT / project)
        print(f"Staged {project} documentation from {source}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
