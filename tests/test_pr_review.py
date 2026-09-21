# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline request selection and exact-revision boundary tests."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))

from pr_review import main, resolve_request
from review_report import REVIEW_MARKER

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
        self.files = [{"filename": "projects/tools/new/README.md", "status": "added"}]
        self.base_tree = [{"path": "projects", "type": "tree", "sha": "projects-tree"}]
        self.projects_tree = [{"path": "tools", "type": "tree", "sha": "tools-tree"}]
        self.tools_tree = [{"path": "existing", "type": "tree"}]
        self.comments = []
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == "pulls/7":
            return self.pr
        if path == f"git/trees/{BASE}":
            return {"tree": self.base_tree}
        if path == "git/trees/projects-tree":
            return {"tree": self.projects_tree}
        if path == "git/trees/tools-tree":
            return {"tree": self.tools_tree}
        raise AssertionError(f"Unexpected request: {method} {path}")

    def paginate(self, path, key=None):
        self.calls.append(("paginate", path, key))
        if path == "pulls/7/files?per_page=100":
            return self.files
        if path == "issues/7/comments":
            return self.comments
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
        self.assertFalse(any("contents/" in path for _, path, _ in self.github.calls))
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
                expected = [("GET", "pulls/7", None)]
                if kind == "draft":
                    expected.append(("paginate", "issues/7/comments", None))
                self.assertEqual(github.calls, expected)

    def test_other_events_do_not_start_review(self):
        for event in ("workflow_dispatch", "workflow_run", "push"):
            self.assertIsNone(resolve_request(self.github, {"event_name": event}))

    def test_existing_project_is_not_selected(self):
        self.github.files[0]["filename"] = "projects/tools/existing/new-file.py"
        self.assertIsNone(resolve_request(self.github, self.context))
        self.assertFalse(any("contents/" in path for _, path, _ in self.github.calls))

    def test_existing_report_is_retired_when_review_no_longer_applies(self):
        self.github.files[0]["filename"] = "projects/tools/existing/new-file.py"
        self.github.comments = [
            {
                "id": 9,
                "user": {"type": "Bot", "login": "github-actions[bot]"},
                "body": REVIEW_MARKER,
            }
        ]

        request = resolve_request(self.github, self.context)

        self.assertTrue(request["retire"])
        self.assertEqual(request["tasks"], [])
        self.github.pr["draft"] = True
        request = resolve_request(self.github, self.context)
        self.assertTrue(request["retire"])

    def test_no_base_projects_directory(self):
        self.github.base_tree = []
        self.assertEqual(
            resolve_request(self.github, self.context)["tasks"][0]["input"],
            "projects/tools/new",
        )

    def test_multiple_new_projects_are_sorted_without_file_reads(self):
        self.github.files.extend(
            [
                {"filename": "projects/tools/second/README.md", "status": "added"},
                {"filename": "projects/tools/a space/README.md", "status": "added"},
            ]
        )
        self.github.pr["changed_files"] = 3
        request = resolve_request(self.github, self.context)
        self.assertEqual(
            [task["id"] for task in request["tasks"]],
            ["review-1", "review-2", "review-3"],
        )
        self.assertFalse(any("contents/" in path for _, path, _ in self.github.calls))

    def test_incomplete_file_inventory_fails(self):
        self.github.pr["changed_files"] = 2
        with self.assertRaisesRegex(ValueError, "complete PR file list"):
            resolve_request(self.github, self.context)

    def test_cli_writes_pinned_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps(self.context["payload"]))
            request_file = root / "request/request.json"
            output = root / "outputs"
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
                    return_value=subprocess.CompletedProcess([], 0, "c" * 40 + "\n"),
                ) as git,
            ):
                main()
            saved = json.loads(request_file.read_text())
            self.assertEqual(saved["head"], HEAD)
            self.assertEqual(saved["tooling"], "c" * 40)
            self.assertIn("ready=true\n", output.read_text())
            self.assertIn("number=7\n", output.read_text())
            self.assertEqual(
                git.call_args.args[0],
                ["git", "-C", "trusted tooling", "rev-parse", "HEAD"],
            )

    def test_cli_marks_report_retirement_as_not_ready_for_inference(self):
        self.github.files[0]["filename"] = "projects/tools/existing/new-file.py"
        self.github.comments = [
            {
                "id": 9,
                "user": {"type": "Bot", "login": "github-actions[bot]"},
                "body": REVIEW_MARKER,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps(self.context["payload"]))
            request_file = root / "request/request.json"
            output = root / "outputs"
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
                    return_value=subprocess.CompletedProcess([], 0, "c" * 40 + "\n"),
                ),
            ):
                main()

            self.assertTrue(json.loads(request_file.read_text())["retire"])
            self.assertIn("ready=false\n", output.read_text())


@pytest.mark.parametrize(
    ("variable", "labels", "ready", "bypass"),
    [
        ("", [], True, ""),
        ("false", ["documentation"], True, ""),
        ("true", [], False, "Repository variable OAR_SKIP_LIVE=true."),
        ("TRUE", [], False, "Repository variable OAR_SKIP_LIVE=true."),
        ("", ["skip-oar-live"], False, "PR label skip-oar-live."),
        ("", ["Skip-OAR-Live"], False, "PR label skip-oar-live."),
        ("false", ["skip-oar-live"], False, "PR label skip-oar-live."),
    ],
)
def test_cli_live_bypass_preserves_scope_and_disables_inference(
    tmp_path, monkeypatch, variable, labels, ready, bypass
):
    github = MockGitHub()
    event = tmp_path / "event.json"
    # The latest API labels, not a stale event's labels, control the PR review.
    event.write_text(json.dumps({"pull_request": github.pr}))
    github.pr["labels"] = [{"name": label} for label in labels]
    request_file = tmp_path / "request.json"
    output = tmp_path / "outputs"
    for key, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_OUTPUT": str(output),
        "OAR_SKIP_LIVE": variable,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("pr_review.GitHub", lambda: github)
    monkeypatch.setattr("sys.argv", ["pr_review.py", "--output", str(request_file)])
    with patch(
        "pr_review.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "c" * 40 + "\n"),
    ):
        main()

    saved = json.loads(request_file.read_text())
    assert saved["bypass"] == bypass
    assert saved["head"] == HEAD
    assert saved["tasks"][0]["input"] == "projects/tools/new"
    assert f"ready={str(ready).lower()}\n" in output.read_text()


def test_removing_bypass_label_resumes_review_despite_stale_payload():
    github = MockGitHub()
    payload = copy.deepcopy(github.pr)
    payload["labels"] = [{"name": "skip-oar-live"}]
    request = resolve_request(
        github,
        {"event_name": "pull_request_target", "payload": {"pull_request": payload}},
    )
    assert request["tasks"]
    assert not request["bypass"]


if __name__ == "__main__":
    unittest.main()
