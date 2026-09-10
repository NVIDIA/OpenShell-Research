# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline request selection and exact-revision boundary tests."""

import base64
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))

from github_api import GitHubError

if importlib.util.find_spec("yaml") is None:
    raise unittest.SkipTest(
        "Request tests run in the OAR environment, which supplies PyYAML."
    )

from pr_review import main, resolve_request

HEAD = "a" * 40
BASE = "b" * 40


class MockGitHub:
    repository = "example/research"

    def __init__(self):
        self.pr = {
            "number": 7,
            "state": "open",
            "draft": False,
            "changed_files": 1,
            "title": "Add a project",
            "body": "A focused tool.",
            "user": {"login": "author"},
            "head": {"sha": HEAD, "repo": {"full_name": self.repository}},
            "base": {"sha": BASE, "ref": "main"},
        }
        self.files = [{"filename": "projects/new/README.md", "status": "added"}]
        self.base_tree = [{"path": "projects", "type": "tree", "sha": "projects-tree"}]
        self.projects_tree = [{"path": "existing", "type": "tree"}]
        self.metadata = "kind: tool\n"
        self.metadata_type = "file"
        self.error = None
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == "pulls/7":
            return self.pr
        if path == f"git/trees/{BASE}":
            return {"tree": self.base_tree}
        if path == "git/trees/projects-tree":
            return {"tree": self.projects_tree}
        if path.startswith("contents/projects/") and path.endswith(
            f"/project.yaml?ref={HEAD}"
        ):
            if self.error:
                raise self.error
            return {
                "type": self.metadata_type,
                "encoding": "base64",
                "content": base64.b64encode(self.metadata.encode()).decode(),
            }
        raise AssertionError(f"Unexpected request: {method} {path}")

    def paginate(self, path, key=None):
        self.calls.append(("paginate", path, key))
        if path == "pulls/7/files?per_page=100":
            return self.files
        raise AssertionError(f"Unexpected pagination: {path}")


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.github = MockGitHub()
        self.context = {
            "event_name": "pull_request_target",
            "payload": {"pull_request": copy.deepcopy(self.github.pr)},
        }

    def test_request_pins_head_and_base_without_waiting_for_checks(self):
        request = resolve_request(self.github, self.context)
        self.assertEqual(request["reason"], "")
        self.assertEqual((request["head"], request["base"]), (HEAD, BASE))
        self.assertEqual(request["tasks"][0]["task"], "review-tool")
        self.assertIn(
            ("GET", f"contents/projects/new/project.yaml?ref={HEAD}", None),
            self.github.calls,
        )
        self.assertFalse(any("actions/" in path for _, path, _ in self.github.calls))

    def test_skip_drafts_forks_bots_closed_and_stale_events_before_reading_files(self):
        for kind in ("draft", "fork", "bot", "closed", "stale", "deleted-fork"):
            with self.subTest(kind=kind):
                github = MockGitHub()
                if kind == "draft":
                    github.pr["draft"] = True
                elif kind == "fork":
                    github.pr["head"]["repo"]["full_name"] = "contributor/research"
                elif kind == "bot":
                    github.pr["user"]["login"] = "dependabot[bot]"
                elif kind == "closed":
                    github.pr["state"] = "closed"
                elif kind == "stale":
                    github.pr["head"]["sha"] = BASE
                else:
                    github.pr["head"]["repo"] = None
                self.assertIsNone(resolve_request(github, self.context))
                self.assertEqual(github.calls, [("GET", "pulls/7", None)])

    def test_other_events_do_not_start_review(self):
        for event in ("workflow_dispatch", "workflow_run", "push"):
            self.assertIsNone(resolve_request(self.github, {"event_name": event}))

    def test_existing_project_needs_no_metadata(self):
        self.github.files[0]["filename"] = "projects/existing/new-file.py"
        self.assertIsNone(resolve_request(self.github, self.context))
        self.assertFalse(any("contents/" in path for _, path, _ in self.github.calls))

    def test_no_base_projects_directory(self):
        self.github.base_tree = []
        self.assertEqual(
            resolve_request(self.github, self.context)["tasks"][0]["input"],
            "projects/new",
        )

    def test_multiple_new_projects_and_encoded_names(self):
        self.github.files.extend(
            [
                {"filename": "projects/second/README.md", "status": "added"},
                {"filename": "projects/a space/README.md", "status": "added"},
            ]
        )
        self.github.pr["changed_files"] = 3
        request = resolve_request(self.github, self.context)
        self.assertEqual(
            [task["id"] for task in request["tasks"]],
            ["review-1", "review-2", "review-3"],
        )
        self.assertIn(
            ("GET", f"contents/projects/a%20space/project.yaml?ref={HEAD}", None),
            self.github.calls,
        )

    def test_metadata_failures_are_reportable_before_any_inference(self):
        for metadata in (
            "kind: unsupported",
            "kind: [tool]",
            "kind: [",
            "",
            "- tool",
            "!!python/object:builtins.object {}",
        ):
            with self.subTest(metadata=metadata):
                self.github.metadata = metadata
                request = resolve_request(self.github, self.context)
                self.assertIn("projects/new/project.yaml", request["reason"])
                self.assertEqual(request["tasks"], [])
        self.github.error = GitHubError("Not found", status=404)
        self.assertIn("Missing", resolve_request(self.github, self.context)["reason"])

    def test_metadata_transport_errors_are_not_mislabeled_as_missing_files(self):
        for status in (403, 500, None):
            self.github.error = GitHubError("Unavailable", status=status)
            with self.subTest(status=status), self.assertRaises(GitHubError):
                resolve_request(self.github, self.context)

    def test_non_file_metadata_is_rejected(self):
        self.github.metadata_type = "symlink"
        self.assertIn(
            "regular YAML file", resolve_request(self.github, self.context)["reason"]
        )

    def test_incomplete_file_inventory_fails(self):
        self.github.pr["changed_files"] = 2
        with self.assertRaisesRegex(ValueError, "complete PR file list"):
            resolve_request(self.github, self.context)

    def test_cli_writes_pinned_request_and_reports_metadata_errors(self):
        for invalid in (False, True):
            with (
                self.subTest(invalid=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                event = root / "event.json"
                event.write_text(json.dumps(self.context["payload"]))
                request_file = root / "request/request.json"
                output = root / "outputs"
                self.github.metadata = "kind: unknown" if invalid else "kind: tool"
                environment = {
                    "GITHUB_EVENT_PATH": str(event),
                    "GITHUB_EVENT_NAME": "pull_request_target",
                    "GITHUB_OUTPUT": str(output),
                }
                with (
                    patch.dict(os.environ, environment),
                    patch("pr_review.GitHub", return_value=self.github),
                    patch(
                        "sys.argv",
                        [
                            "pr_review.py",
                            "--output",
                            str(request_file),
                            "--tooling",
                            "trusted tooling",
                        ],
                    ),
                    patch(
                        "pr_review.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            [], 0, "c" * 40 + "\n"
                        ),
                    ) as git,
                ):
                    if invalid:
                        with self.assertRaisesRegex(SystemExit, "project.yaml"):
                            main()
                    else:
                        main()
                saved = json.loads(request_file.read_text())
                self.assertEqual(saved["head"], HEAD)
                self.assertEqual(saved["tooling"], "c" * 40)
                self.assertIn(f"ready={str(not invalid).lower()}\n", output.read_text())
                self.assertIn("number=7\n", output.read_text())
                self.assertEqual(
                    git.call_args.args[0],
                    ["git", "-C", "trusted tooling", "rev-parse", "HEAD"],
                )


if __name__ == "__main__":
    unittest.main()
