# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate OAR results and publish a bounded, current-revision PR report."""

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

REVIEW_MARKER = "<!-- oar-pr-review -->"
VERDICTS = {
    "pass": "✅ Pass",
    "needs_changes": "⚠️ Needs changes",
    "inconclusive": "❔ Inconclusive",
}


def validate_result(result, task=None):
    if not isinstance(result, dict):
        raise TypeError("Expected a review result object.")
    if task and result.get("task") != task:
        raise ValueError(f"Expected task {task}, got {result.get('task')}.")
    if result.get("verdict") not in VERDICTS:
        raise ValueError("Invalid verdict.")
    guidelines = result.get("guidelines_assessment")
    if (
        not isinstance(guidelines, dict)
        or guidelines.get("verdict") not in VERDICTS
        or not isinstance(guidelines.get("explanation"), str)
        or not guidelines["explanation"].strip()
    ):
        raise ValueError("Missing or invalid guidelines assessment.")
    if result["verdict"] == "pass" and guidelines["verdict"] != "pass":
        raise ValueError("A passing review requires a passing guidelines assessment.")
    scores = result.get("criterion_scores")
    if (
        not isinstance(scores, list)
        or not scores
        or any(
            not isinstance(item, dict)
            or type(item.get("score")) not in (int, float)
            or not 0 <= item["score"] <= 100
            or item["score"] != int(item["score"])
            for item in scores
        )
    ):
        raise ValueError("Missing or invalid criterion scores.")
    # Scores are nonnegative integers; ties round up, not Python's ties-to-even.
    total = (2 * sum(int(item["score"]) for item in scores) + len(scores)) // (
        2 * len(scores)
    )
    if (
        type(result.get("overall_score")) not in (int, float)
        or result["overall_score"] != total
    ):
        raise ValueError(f"Overall score must be {total}.")
    for field in ("findings", "strengths", "limitations"):
        if not isinstance(result.get(field), list):
            raise TypeError(f"Missing {field}.")
    return result


def read_results(directory, tasks):
    reviews = []
    for task in tasks:
        review = dict(task)
        path = Path(directory) / f"{task['id']}.json"
        try:
            review["result"] = validate_result(
                json.loads(path.read_text(encoding="utf-8")), task.get("task")
            )
        except FileNotFoundError:
            review["error"] = "No result produced."
        except (OSError, ValueError, TypeError) as error:
            review["error"] = str(error)
        reviews.append(review)
    return reviews


def render_report(*, request, reviews, run_url, run_id, outcome):
    reason = f" — {_escape_text(request['reason'])}" if request.get("reason") else ""
    source_url = f"{run_url.partition('/actions/')[0]}/blob/{request['head']}"
    lines = [
        REVIEW_MARKER,
        f"<!-- oar-report-run:{run_id} -->",
        "## New project review",
        "",
        f"Revision: `{request['head']}` · [Workflow and result artifacts]({run_url})",
        "",
        "Review findings are advisory. Required checks remain separate merge gates.",
        "",
        f"Execution: **{_escape_text(outcome)}**{reason}",
        "",
    ]
    if request.get("tooling"):
        lines.extend([f"Reviewer and guidelines revision: `{request['tooling']}`", ""])
    if reviews:
        lines.extend(
            [
                "### Reviews",
                "",
                "| Project | Verdict | Guidelines | Findings |",
                "| --- | --- | --- | ---: |",
            ]
        )
        for review in reviews:
            label = _escape_text(review["label"])
            result = review.get("result")
            lines.append(
                f"| {label} | {VERDICTS[result['verdict']]} | {VERDICTS[result['guidelines_assessment']['verdict']]} | {len(result['findings'])} |"
                if result
                else f"| {label} | Not completed | — | — |"
            )
        for review in reviews:
            lines.extend(
                [
                    "",
                    "<details>",
                    f"<summary>{_escape_text(review['label'])}</summary>",
                    "",
                ]
            )
            result = review.get("result")
            if not result:
                lines.append(
                    _escape_text(
                        review.get("error") or request.get("reason") or "Not completed."
                    )
                )
            else:
                lines.extend(
                    [
                        _escape_text(result.get("summary")),
                        "",
                        f"**Project guidelines: {VERDICTS[result['guidelines_assessment']['verdict']]}**",
                        "",
                        _escape_text(result["guidelines_assessment"]["explanation"]),
                    ]
                )
                lines.extend(["", "#### Findings", ""])
                if not result["findings"]:
                    lines.append("No actionable findings.")
                for finding in result["findings"]:
                    location = _finding_location(
                        finding, review["label"], source_url=source_url
                    )
                    evidence = finding["evidence"]
                    lines.extend(
                        [
                            f"- **{_escape_text(finding.get('severity'))}: {_escape_text(finding.get('title'))}** — {location}",
                            f"  - Evidence: {_escape_text(evidence)}",
                            f"  - Recommendation: {_escape_text(finding.get('recommendation'))}",
                        ]
                    )
                    impact = finding["impact"]
                    if impact:
                        lines.append(f"  - Impact: {_escape_text(impact)}")
                for field in ("strengths", "limitations"):
                    if result[field]:
                        lines.extend(["", f"#### {field.capitalize()}", ""])
                        lines.extend(
                            f"- {_escape_text(item)}" for item in result[field]
                        )
            lines.extend(["", "</details>"])
    body = "\n".join(lines)
    if len(body) > 55000:
        return (
            body.split("\n<details>")[0][:53000]
            + "\n\nReport exceeds the comment limit; see the linked JSON artifacts for complete results."
        )
    return body


def publish_report(github, request, body, run_id):
    number = request["number"]
    pr = github.request("GET", f"pulls/{number}")
    if (
        pr["state"] != "open"
        or pr["head"]["sha"] != request["head"]
        or (pr.get("draft") and not request.get("retire"))
    ):
        return False
    comments = github.paginate(f"issues/{number}/comments")
    existing = find_existing_report(comments)
    previous_run = (
        re.search(r"<!-- oar-report-run:(\d+) -->", existing["body"])
        if existing
        else None
    )
    if previous_run and int(previous_run.group(1)) > int(run_id):
        return False
    if request.get("retire"):
        if not existing:
            return False
        github.request("DELETE", f"issues/comments/{existing['id']}")
        return True
    if existing:
        github.request("PATCH", f"issues/comments/{existing['id']}", {"body": body})
    else:
        github.request("POST", f"issues/{number}/comments", {"body": body})
    return True


def find_existing_report(comments):
    return next(
        (
            comment
            for comment in comments
            if (comment.get("user") or {}).get("type") == "Bot"
            and REVIEW_MARKER in (comment.get("body") or "")
        ),
        None,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Validate all expected review results")
    publish = commands.add_parser(
        "publish", help="Render and publish the current PR report"
    )
    for command in (verify, publish):
        command.add_argument("--request", required=True, type=Path)
        command.add_argument("--results", required=True, type=Path)
    publish.add_argument("--outcome", required=True)
    publish.add_argument("--output", type=Path, default=Path("report.md"))
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    reviews = read_results(args.results, request["tasks"])
    if args.command == "verify":
        errors = [
            f"{review['id']}: {review['error']}"
            for review in reviews
            if "error" in review
        ]
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"Validated {len(reviews)} review results.")
        return 0
    run_id = os.environ["GITHUB_RUN_ID"]
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
    )
    body = render_report(
        request=request,
        reviews=reviews,
        run_url=run_url,
        run_id=run_id,
        outcome=args.outcome,
    )
    args.output.write_text(body + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open(
            "a", encoding="utf-8"
        ) as summary:
            summary.write(body + "\n")
    if args.command == "publish" and request.get("number"):
        from github_api import GitHub

        publish_report(
            GitHub(),
            request,
            body,
            run_id,
        )
    return 0


def _escape_text(value):
    text = "" if value is None else str(value)
    for original, escaped in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ("|", "&#124;"),
        ("`", "&#96;"),
        ("@", "&#64;"),
        ("[", "&#91;"),
        ("]", "&#93;"),
    ):
        text = text.replace(original, escaped)
    return re.sub(r"\r?\n", " ", text)


def _finding_location(finding, fallback, *, source_url):
    path = finding.get("path") or fallback
    line = finding.get("line")
    display = f"{path}:{line}" if line else path
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != path:
        return _escape_text(display)
    anchor = f"#L{line}" if line else ""
    return f"[{_escape_text(display)}]({source_url}/{quote(path, safe='/')}{anchor})"


if __name__ == "__main__":
    sys.exit(main())
