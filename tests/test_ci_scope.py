# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""New-project selection without inference or GitHub access."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))

from ci_scope import (
    PROJECT_KIND_BY_DIRECTORY,
    PROJECT_TASKS,
    new_project_paths,
    project_task,
)


class ScopeTests(unittest.TestCase):
    def test_complete_project_selected_once_in_mixed_pr(self):
        files = [
            {"filename": name, "status": "added"}
            for name in (
                "projects/tools/new/README.md",
                "projects/tools/new/src/main.py",
                "projects/tools/new/docs/guide.md",
                "projects/tools/existing/new-file.py",
                "docs/dev-notes/posts/note.md",
                ".github/workflows/test.yml",
                "projects/README.md",
            )
        ]
        self.assertEqual(
            new_project_paths(files, {"projects/tools/existing"}),
            ["projects/tools/new"],
        )

    def test_updates_on_introducing_pr_still_select_project(self):
        files = [{"filename": "projects/tools/new/README.md", "status": "modified"}]
        self.assertEqual(new_project_paths(files, set()), ["projects/tools/new"])
        self.assertEqual(new_project_paths(files, {"projects/tools/new"}), [])

    def test_deleted_files_do_not_create_reviews(self):
        files = [{"filename": "projects/tools/deleted/run.py", "status": "removed"}]
        self.assertEqual(new_project_paths(files, set()), [])

    def test_rename_to_new_directory_selects_destination_only(self):
        files = [
            {
                "filename": "projects/tools/new/README.md",
                "status": "renamed",
                "previous_filename": "projects/tools/old/README.md",
            }
        ]
        self.assertEqual(
            new_project_paths(files, {"projects/tools/old"}),
            ["projects/tools/new"],
        )
        self.assertEqual(
            new_project_paths(files, {"projects/tools/old", "projects/tools/new"}),
            [],
        )

    def test_multiple_projects_are_sorted_and_not_prefix_matched(self):
        files = [
            {"filename": f"projects/tools/{name}/README.md", "status": "added"}
            for name in ("zeta", "alpha", "alpha-copy")
        ]
        self.assertEqual(
            new_project_paths(files, {"projects/tools/alpha"}),
            ["projects/tools/alpha-copy", "projects/tools/zeta"],
        )

    def test_spaces_and_unicode_in_project_names(self):
        files = [
            {
                "filename": "projects/use-case-examples/Δ example/README.md",
                "status": "added",
            }
        ]
        self.assertEqual(
            new_project_paths(files, set()),
            ["projects/use-case-examples/Δ example"],
        )

    def test_files_outside_project_type_directories_are_not_projects(self):
        files = [
            {"filename": "projects/README.md", "status": "modified"},
            {"filename": "projects/other/new/README.md", "status": "added"},
        ]
        self.assertEqual(new_project_paths(files, set()), [])

    def test_escaping_and_noncanonical_paths_are_rejected(self):
        for name in (
            "/projects/tools/a/x",
            "projects/tools/../x/file",
            "projects//tools/a/x",
            "./projects/tools/a/x",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                new_project_paths([{"filename": name, "status": "added"}], set())

    def test_all_three_kinds_select_their_dedicated_task(self):
        directory_by_kind = {
            kind: directory for directory, kind in PROJECT_KIND_BY_DIRECTORY.items()
        }
        for index, (kind, task) in enumerate(PROJECT_TASKS.items(), 1):
            with self.subTest(kind=kind):
                path = f"projects/{directory_by_kind[kind]}/new"
                self.assertEqual(
                    project_task(path, index),
                    {
                        "id": f"review-{index}",
                        "task": task,
                        "kind": kind,
                        "input": path,
                        "label": path,
                    },
                )

    def test_task_rejects_paths_outside_project_type_directories(self):
        for path in (
            "projects/new",
            "projects/other/new",
            "projects/tools/new/nested",
            "projects/tools/../new",
            "./projects/tools/new",
            "tools/new",
        ):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ValueError, "Invalid project path"),
            ):
                project_task(path, 1)


if __name__ == "__main__":
    unittest.main()
