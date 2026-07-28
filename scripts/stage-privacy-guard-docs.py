#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage canonical Privacy Guard documentation in the site source tree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "projects" / "privacy-guard" / "docs"
DEFAULT_DESTINATION = ROOT / "docs" / "documentation" / "privacy-guard"


def stage_privacy_guard_docs(source: Path, destination: Path) -> None:
    """Replace the generated site mirror with one canonical project-docs tree."""

    source = source.resolve()
    destination = destination.absolute()
    if not source.is_dir():
        raise ValueError(f"documentation source does not exist: {source}")
    if destination.is_symlink():
        raise ValueError(f"documentation destination must not be a symlink: {destination}")
    if source == destination.resolve():
        raise ValueError("documentation source and destination must differ")

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    try:
        stage_privacy_guard_docs(args.source, args.destination)
    except ValueError as error:
        parser.error(str(error))
    print(f"Staged Privacy Guard documentation from {args.source}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
