# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise GitHub CLI transport without network access or external writes."""

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "github_api", Path(__file__).resolve().parents[1] / ".github/scripts/github_api.py"
)
github_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(github_api)


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.run_patch = patch.object(github_api.subprocess, "run")
        self.run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.github = github_api.GitHub("example/repository")

    def respond(self, output, *, code=0, stderr=""):
        self.run.return_value = subprocess.CompletedProcess(
            [], code, stdout=output, stderr=stderr
        )

    def test_repository_defaults_to_workflow_environment(self):
        with patch.dict(github_api.os.environ, {"GITHUB_REPOSITORY": "owner/repo"}):
            self.assertEqual(github_api.GitHub().repository, "owner/repo")
            self.assertEqual(github_api.GitHub("other/repo").repository, "other/repo")

    def test_get_returns_json_and_preserves_query(self):
        self.respond('{"number": 42}')
        self.assertEqual(self.github.request("GET", "pulls/42?x=y"), {"number": 42})
        self.run.assert_called_once_with(
            ["gh", "api", "repos/example/repository/pulls/42?x=y", "--method", "GET"],
            input=None,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_write_body_is_json_on_stdin_never_shell_or_arguments(self):
        body = {"body": '$(touch /tmp/not-executed) `command` "quoted"\n🦖'}
        self.respond('{"id": 7}')
        self.assertEqual(
            self.github.request("POST", "issues/42/comments", body), {"id": 7}
        )
        arguments, options = self.run.call_args
        self.assertEqual(
            arguments[0],
            [
                "gh",
                "api",
                "repos/example/repository/issues/42/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
        )
        self.assertEqual(json.loads(options["input"]), body)
        self.assertNotIn("shell", options)

    def test_empty_object_is_still_sent(self):
        self.respond("{}")
        self.github.request("PATCH", "issues/42", {})
        self.assertEqual(self.run.call_args.kwargs["input"], "{}")
        self.assertIn("--input", self.run.call_args.args[0])

    def test_empty_success_response(self):
        self.respond("")
        self.assertIsNone(self.github.request("DELETE", "issues/comments/7"))

    def test_paginate_array_pages(self):
        self.respond('[[{"id": 1}, {"id": 2}], [{"id": 3}]]')
        self.assertEqual(
            self.github.paginate("pulls/42/files"), [{"id": 1}, {"id": 2}, {"id": 3}]
        )
        self.assertEqual(
            self.run.call_args.args[0][-4:],
            ["--method", "GET", "--paginate", "--slurp"],
        )

    def test_paginate_wrapped_pages(self):
        self.respond(
            '[{"total_count": 2, "workflow_runs": [{"id": 1}]}, {"workflow_runs": [{"id": 2}]}]'
        )
        self.assertEqual(
            self.github.paginate("actions/runs?head_sha=abc", key="workflow_runs"),
            [{"id": 1}, {"id": 2}],
        )

    def test_paginate_empty_collection(self):
        self.respond("[[]]")
        self.assertEqual(self.github.paginate("issues/42/comments"), [])

    def test_http_error_status_from_response_or_cli_stderr(self):
        cases = [
            ('{"message":"Not Found", "status":"404"}', "gh: Not Found", 404),
            ('{"status":403}', "gh: Forbidden", 403),
            ('{"message":"Not Found"}', "gh: Not Found (HTTP 404)", 404),
            ("not JSON", "gh: Forbidden (HTTP 403)", 403),
            ("", "error connecting to api.github.com", None),
            ('{"status":"invalid"}', "gh: API failed", None),
            ("[]", "gh: API failed", None),
        ]
        for stdout, stderr, status in cases:
            with self.subTest(stdout=stdout, stderr=stderr):
                self.respond(stdout, code=1, stderr=stderr)
                with self.assertRaises(github_api.GitHubError) as raised:
                    self.github.request("GET", "contents/missing")
                self.assertEqual(raised.exception.status, status)
                self.assertIn(stderr, str(raised.exception))

    def test_error_without_stderr_remains_an_error(self):
        self.respond('{"status":404}', code=1)
        with self.assertRaisesRegex(github_api.GitHubError, "request failed") as raised:
            self.github.request("GET", "contents/missing")
        self.assertEqual(raised.exception.status, 404)

    def test_malformed_success_is_not_mistaken_for_missing_content(self):
        self.respond("not JSON")
        with self.assertRaisesRegex(github_api.GitHubError, "invalid JSON") as raised:
            self.github.request("GET", "contents/registry")
        self.assertIsNone(raised.exception.status)


if __name__ == "__main__":
    unittest.main()
