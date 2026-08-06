#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage canonical Egress Gate documentation in the site source tree."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "projects" / "egress-gate" / "docs"
DEFAULT_DESTINATION = ROOT / "docs" / "documentation" / "egress-gate"


def stage_egress_gate_docs(source: Path, destination: Path) -> None:
    """Replace the generated site mirror with one canonical project-docs tree."""

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
    stage_egress_gate_docs(DEFAULT_SOURCE, DEFAULT_DESTINATION)
    print(f"Staged Egress Gate documentation from {DEFAULT_SOURCE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
