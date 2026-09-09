# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""New-project selection without inference or GitHub access."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))

from ci_scope import PROJECT_TASKS, new_project_paths, project_task


class ScopeTests(unittest.TestCase):
    def test_complete_project_selected_once_in_mixed_pr(self):
        files = [
            {"filename": name, "status": "added"}
            for name in (
                "projects/new/README.md",
                "projects/new/src/main.py",
                "projects/new/docs/guide.md",
                "projects/existing/new-file.py",
                "docs/dev-notes/posts/note.md",
                ".github/workflows/test.yml",
                "projects/README.md",
            )
        ]
        self.assertEqual(new_project_paths(files, {"existing"}), ["projects/new"])

    def test_updates_on_introducing_pr_still_select_project(self):
        files = [{"filename": "projects/new/README.md", "status": "modified"}]
        self.assertEqual(new_project_paths(files, set()), ["projects/new"])
        self.assertEqual(new_project_paths(files, {"new"}), [])

    def test_deleted_files_do_not_create_reviews(self):
        files = [{"filename": "projects/deleted/run.py", "status": "removed"}]
        self.assertEqual(new_project_paths(files, set()), [])

    def test_rename_to_new_directory_selects_destination_only(self):
        files = [
            {
                "filename": "projects/new/README.md",
                "status": "renamed",
                "previous_filename": "projects/old/README.md",
            }
        ]
        self.assertEqual(new_project_paths(files, {"old"}), ["projects/new"])
        self.assertEqual(new_project_paths(files, {"old", "new"}), [])

    def test_multiple_projects_are_sorted_and_not_prefix_matched(self):
        files = [
            {"filename": f"projects/{name}/README.md", "status": "added"}
            for name in ("zeta", "alpha", "alpha-copy")
        ]
        self.assertEqual(
            new_project_paths(files, {"alpha"}),
            ["projects/alpha-copy", "projects/zeta"],
        )

    def test_spaces_and_unicode_in_project_names(self):
        files = [{"filename": "projects/Δ tool/README.md", "status": "added"}]
        self.assertEqual(new_project_paths(files, set()), ["projects/Δ tool"])

    def test_escaping_and_noncanonical_paths_are_rejected(self):
        for name in (
            "/projects/a/x",
            "projects/../x",
            "projects//a/x",
            "./projects/a/x",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                new_project_paths([{"filename": name, "status": "added"}], set())

    def test_all_three_kinds_select_their_dedicated_task(self):
        for index, (kind, task) in enumerate(PROJECT_TASKS.items(), 1):
            with self.subTest(kind=kind):
                self.assertEqual(
                    project_task("projects/new", {"kind": kind}, index),
                    {
                        "id": f"review-{index}",
                        "task": task,
                        "kind": kind,
                        "input": "projects/new",
                        "label": "projects/new",
                    },
                )

    def test_missing_unknown_and_malformed_kinds_fail(self):
        for metadata in (
            None,
            [],
            "tool",
            {},
            {"kind": "library"},
            {"kind": ["tool"]},
            {"kind": True},
        ):
            with (
                self.subTest(metadata=metadata),
                self.assertRaisesRegex(ValueError, "project.yaml"),
            ):
                project_task("projects/new", metadata, 1)


if __name__ == "__main__":
    unittest.main()
