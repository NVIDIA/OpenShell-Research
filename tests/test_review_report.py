# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Report contracts, escaping, CLI behavior, and sticky-comment safety."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

from review_report import (
    REVIEW_MARKER,
    main,
    publish_report,
    read_results,
    render_report,
    validate_result,
)

HEAD = "a" * 40
BASE = "b" * 40


def result(task="review-research-spike"):
    return {
        "task": task,
        "verdict": "pass",
        "summary": "Evidence supports the described result.",
        "guidelines_assessment": {
            "verdict": "pass",
            "explanation": "All applicable guidelines verified.",
        },
        "criterion_scores": [
            {"criterion": "accuracy", "score": 90, "explanation": "Supported."},
            {"criterion": "clarity", "score": 91, "explanation": "Clear."},
        ],
        "overall_score": 91,
        "findings": [],
        "strengths": ["A focused example."],
        "limitations": ["Hardware not exercised."],
    }


def report_options():
    return {
        "request": {
            "number": 7,
            "head": HEAD,
            "tooling": "c" * 40,
        },
        "reviews": [
            {
                "id": "review-1",
                "task": "review-research-spike",
                "label": "projects/new-spike",
                "result": result(),
            }
        ],
        "run_url": "https://github.com/example/research/actions/runs/100",
        "run_id": 100,
        "outcome": "completed",
    }


class MockGitHub:
    def __init__(self):
        self.pr = {"number": 7, "state": "open", "head": {"sha": HEAD}}
        self.comments = []
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == "GET" and path == "pulls/7":
            return self.pr
        if method in ("POST", "PATCH"):
            return data
        if method == "DELETE" and path.startswith("issues/comments/"):
            return None
        raise AssertionError(f"Unexpected request: {method} {path}")

    def paginate(self, path, key=None):
        self.calls.append(("PAGINATE", path, None))
        if path != "issues/7/comments" or key is not None:
            raise AssertionError(f"Unexpected pagination: {path}")
        return self.comments


class ReviewReportTests(unittest.TestCase):
    def test_guideline_assessment_is_required_and_cannot_be_hidden_by_passing_score(
        self,
    ):
        for assessment in (
            None,
            {},
            {"verdict": "pass"},
            {"verdict": "unknown", "explanation": "Reviewed."},
        ):
            with (
                self.subTest(assessment=assessment),
                self.assertRaisesRegex(ValueError, "guidelines"),
            ):
                validate_result({**result(), "guidelines_assessment": assessment})
        for verdict in ("needs_changes", "inconclusive"):
            review = result()
            review["guidelines_assessment"]["verdict"] = verdict
            with self.assertRaisesRegex(ValueError, "guidelines"):
                validate_result(review)
            review["verdict"] = verdict
            validate_result(review)
            options = report_options()
            options["reviews"][0]["result"] = review
            self.assertIn("Project guidelines:", render_report(**options))

    def test_result_validation(self):
        self.assertEqual(
            validate_result(result(), "review-research-spike")["overall_score"], 91
        )
        integer_float = result()
        integer_float["criterion_scores"][0]["score"] = 90.0
        integer_float["overall_score"] = 91.0
        self.assertEqual(validate_result(integer_float)["overall_score"], 91)
        with self.assertRaisesRegex(ValueError, "task"):
            validate_result(result(), "review-tool")
        for bad in (90, 91.5, "91", -1, 101, True):
            with (
                self.subTest(overall_score=bad),
                self.assertRaisesRegex(ValueError, "score"),
            ):
                validate_result({**result(), "overall_score": bad})
        for bad in (-1, 101, 1.5, True, "90"):
            review = result()
            review["criterion_scores"][0]["score"] = bad
            with (
                self.subTest(criterion_score=bad),
                self.assertRaisesRegex(ValueError, "score"),
            ):
                validate_result(review)
        for bad in ([], None, [{"score": 1}, None]):
            with self.subTest(scores=bad), self.assertRaisesRegex(ValueError, "score"):
                validate_result({**result(), "criterion_scores": bad})
        with self.assertRaisesRegex(ValueError, "verdict"):
            validate_result({**result(), "verdict": "findings"})
        for field in ("findings", "strengths", "limitations"):
            with self.subTest(field=field), self.assertRaisesRegex(TypeError, field):
                validate_result({**result(), field: None})

    def test_partial_results_preserve_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "good.json").write_text(json.dumps(result()))
            (path / "invalid.json").write_text("{")
            (path / "wrong-score.json").write_text(
                json.dumps({**result(), "overall_score": 12})
            )
            (path / "wrong-task.json").write_text(json.dumps(result("review-tool")))
            tasks = [
                {"id": name, "task": "review-research-spike", "label": name}
                for name in ("good", "missing", "invalid", "wrong-score", "wrong-task")
            ]
            reviews = read_results(directory, tasks)
        self.assertEqual(reviews[0]["result"], result())
        self.assertTrue(
            all(
                review.get("error") and "result" not in review for review in reviews[1:]
            )
        )
        options = report_options()
        options.update(reviews=reviews, outcome="failed")
        body = render_report(**options)
        for expected in (
            "Not completed",
            "Hardware not exercised.",
            "failed",
        ):
            self.assertIn(expected, body)

    def test_report_identifies_revision_guidelines_and_advisory_status(self):
        normal = render_report(**report_options())
        for expected in (
            REVIEW_MARKER,
            HEAD,
            "advisory",
            "Project guidelines:",
            "c" * 40,
        ):
            self.assertIn(expected, normal)
        for omitted in ("91/100", "Criterion", "Supported."):
            self.assertNotIn(omitted, normal)

    def test_report_escapes_reviewer_data(self):
        options = report_options()
        unsafe = "<img src=x> @team | `code`\nnext"
        review = options["reviews"][0]
        review["label"] = unsafe
        review["result"]["summary"] = unsafe
        review["result"]["findings"] = [
            dict.fromkeys(
                ("title", "path", "evidence", "impact", "recommendation"), unsafe
            )
            | {"severity": "medium"}
        ]
        body = render_report(**options)
        for unexpected in ("<img", "@team", "| `code`"):
            self.assertNotIn(unexpected, body)
        self.assertIn("&lt;img src=x&gt; &#64;team &#124; &#96;code&#96; next", body)

    def test_report_retains_finding_evidence_and_impact(self):
        options = report_options()
        options["reviews"][0]["result"]["findings"] = [
            {
                "severity": "medium",
                "title": "Wrong command",
                "evidence": "Run missing-command",
                "impact": "That executable is not installed.",
                "recommendation": "Use the installed command.",
            }
        ]
        body = render_report(**options)
        self.assertIn("Run missing-command", body)
        self.assertIn("That executable is not installed.", body)

    def test_finding_locations_link_to_the_reviewed_revision(self):
        options = report_options()
        options["reviews"][0]["result"]["findings"] = [
            {
                "severity": "medium",
                "title": "Wrong command",
                "path": "projects/new spike/README.md",
                "line": 12,
                "evidence": "The documented command is unavailable.",
                "impact": "The first run fails.",
                "recommendation": "Use the installed command.",
            }
        ]

        body = render_report(**options)

        self.assertIn(
            f"https://github.com/example/research/blob/{HEAD}/projects/new%20spike/README.md#L12",
            body,
        )

    def test_large_details_keep_summary_and_artifact_link(self):
        options = report_options()
        options["reviews"][0]["result"]["summary"] = "Detailed evidence " * 5000
        body = render_report(**options)
        self.assertLessEqual(len(body), 55000)
        self.assertIn(options["run_url"], body)

    def test_stale_and_closed_prs_do_not_publish(self):
        for change in ("head", "closed"):
            with self.subTest(change=change):
                github = MockGitHub()
                if change == "head":
                    github.pr["head"]["sha"] = BASE
                else:
                    github.pr["state"] = "closed"
                self.assertFalse(
                    publish_report(github, {"number": 7, "head": HEAD}, "report", 100)
                )
                self.assertEqual(github.calls, [("GET", "pulls/7", None)])

    def test_older_runs_cannot_overwrite_newer_reports(self):
        github = MockGitHub()
        github.comments = [
            {
                "id": 5,
                "user": {"type": "Bot", "login": "github-actions[bot]"},
                "body": f"{REVIEW_MARKER}\n<!-- oar-report-run:101 -->",
            }
        ]
        self.assertFalse(
            publish_report(github, {"number": 7, "head": HEAD}, "report", 100)
        )
        self.assertFalse(
            any(method in ("PATCH", "POST") for method, _, _ in github.calls)
        )

    def test_retirement_deletes_only_an_existing_current_report(self):
        github = MockGitHub()
        github.comments = [
            {
                "id": 5,
                "user": {"type": "Bot", "login": "github-actions[bot]"},
                "body": f"{REVIEW_MARKER}\n<!-- oar-report-run:99 -->",
            }
        ]

        request = {"number": 7, "head": HEAD, "retire": True}
        self.assertTrue(publish_report(github, request, "unused", 100))
        self.assertIn(("DELETE", "issues/comments/5", None), github.calls)

        github = MockGitHub()
        self.assertFalse(publish_report(github, request, "unused", 100))
        self.assertFalse(any(method == "DELETE" for method, _, _ in github.calls))

    def test_sticky_comments_do_not_edit_human_or_unrelated_bot_comments(self):
        github = MockGitHub()
        github.comments = [
            {"id": 1, "user": {"type": "User"}, "body": REVIEW_MARKER},
            {"id": 2, "user": {"type": "Bot"}, "body": "Documentation preview"},
            {
                "id": 3,
                "user": {"type": "Bot", "login": "other-app[bot]"},
                "body": f"{REVIEW_MARKER}\n<!-- oar-report-run:100 -->",
            },
            {
                "id": 4,
                "user": {"type": "Bot", "login": "github-actions[bot]"},
                "body": f"{REVIEW_MARKER}\n<!-- oar-report-run:100 -->",
            },
        ]
        self.assertTrue(
            publish_report(github, {"number": 7, "head": HEAD}, "updated", 100)
        )
        self.assertIn(("PATCH", "issues/comments/4", {"body": "updated"}), github.calls)
        github.calls.clear()
        github.comments.pop()
        self.assertTrue(publish_report(github, {"number": 7, "head": HEAD}, "new", 101))
        self.assertIn(("POST", "issues/7/comments", {"body": "new"}), github.calls)

    def test_verify_cli_reports_missing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tasks = [
                {"id": "review-1", "task": "review-research-spike", "label": "note.md"}
            ]
            args = [
                "verify",
                "--request",
                str(path / "request.json"),
                "--results",
                directory,
            ]
            (path / "review-1.json").write_text(json.dumps(result()))
            (path / "request.json").write_text(json.dumps({"tasks": tasks}))
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(args), 0)
            self.assertIn("Validated 1", output.getvalue())
            (path / "review-1.json").unlink()
            with redirect_stderr(StringIO()) as output:
                self.assertEqual(main(args), 1)
            self.assertIn("review-1: No result produced.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
