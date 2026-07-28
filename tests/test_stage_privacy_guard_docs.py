# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stage-privacy-guard-docs.py"

SPEC = importlib.util.spec_from_file_location("stage_privacy_guard_docs", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {SCRIPT}")
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


class StagePrivacyGuardDocsTests(unittest.TestCase):
    def test_stage_replaces_destination_with_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "project-docs"
            destination = root / "site-docs"
            (source / "architecture").mkdir(parents=True)
            (source / "index.md").write_text("# Overview\n", encoding="utf-8")
            (source / "architecture/index.md").write_text(
                "# Architecture\n",
                encoding="utf-8",
            )
            destination.mkdir()
            (destination / "stale.md").write_text("# Stale\n", encoding="utf-8")

            STAGER.stage_privacy_guard_docs(source, destination)

            self.assertEqual(
                (destination / "index.md").read_text(encoding="utf-8"),
                "# Overview\n",
            )
            self.assertTrue((destination / "architecture/index.md").is_file())
            self.assertFalse((destination / "stale.md").exists())

    def test_stage_rejects_symlinks_in_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "project-docs"
            source.mkdir()
            target = root / "outside.md"
            target.write_text("# Outside\n", encoding="utf-8")
            (source / "linked.md").symlink_to(target)

            with self.assertRaisesRegex(ValueError, "must not contain symlinks"):
                STAGER.stage_privacy_guard_docs(source, root / "site-docs")

    def test_stage_rejects_destination_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "project-docs"
            source.mkdir()

            with self.assertRaisesRegex(ValueError, "must not overlap"):
                STAGER.stage_privacy_guard_docs(source, source / "published")

    def test_stage_rejects_source_inside_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "site-docs"
            source = destination / "project-docs"
            source.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "must not overlap"):
                STAGER.stage_privacy_guard_docs(source, destination)


if __name__ == "__main__":
    unittest.main()
