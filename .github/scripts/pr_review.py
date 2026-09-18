# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a same-repository PR's new projects before allocating inference."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from ci_scope import PROJECT_KIND_BY_DIRECTORY, new_project_paths, project_task
from github_api import GitHub
from review_report import find_existing_report


def resolve_request(github, context):
    if context["event_name"] != "pull_request_target":
        return None
    payload_pr = context["payload"]["pull_request"]
    number = payload_pr["number"]
    pr = github.request("GET", f"pulls/{number}")
    if (
        pr["state"] != "open"
        or pr["head"]["sha"] != payload_pr["head"]["sha"]
        or (pr["head"].get("repo") or {}).get("full_name") != github.repository
        or pr["user"]["login"] == "dependabot[bot]"
    ):
        return None
    if pr["draft"]:
        return _retirement_request(github, pr)

    files = github.paginate(f"pulls/{number}/files?per_page=100")
    if len(files) != pr["changed_files"]:
        raise ValueError("GitHub did not return the complete PR file list.")
    # Non-recursive trees avoid GitHub's recursive-tree truncation limit.
    base_tree = github.request("GET", f"git/trees/{pr['base']['sha']}")["tree"]
    projects = next((entry for entry in base_tree if entry["path"] == "projects"), None)
    existing = set()
    if projects and projects["type"] == "tree":
        project_types = github.request("GET", f"git/trees/{projects['sha']}")["tree"]
        for project_type in project_types:
            if (
                project_type["type"] != "tree"
                or project_type["path"] not in PROJECT_KIND_BY_DIRECTORY
            ):
                continue
            projects_of_type = github.request(
                "GET", f"git/trees/{project_type['sha']}"
            )["tree"]
            existing.update(
                f"projects/{project_type['path']}/{entry['path']}"
                for entry in projects_of_type
                if entry["type"] == "tree"
            )
    tasks = [
        project_task(path, index)
        for index, path in enumerate(new_project_paths(files, existing), start=1)
    ]
    if not tasks:
        return _retirement_request(github, pr)
    return {
        "number": number,
        "head": pr["head"]["sha"],
        "base": pr["base"]["sha"],
        "title": pr["title"],
        "description": pr.get("body") or "",
        "tasks": tasks,
        "reason": "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("request/request.json"))
    parser.add_argument("--tooling", type=Path, default=Path("tooling"))
    args = parser.parse_args()
    context = {
        "payload": json.loads(
            Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
        ),
        "event_name": os.environ["GITHUB_EVENT_NAME"],
    }
    request = resolve_request(GitHub(), context)
    if request is None:
        print("No eligible new projects to review.")
        return
    tooling = subprocess.run(
        ["git", "-C", str(args.tooling), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    request["tooling"] = tooling
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    outputs = {key: request[key] for key in ("number", "head", "base")}
    outputs.update(
        ready=str(bool(request["tasks"]) and not request["reason"]).lower(),
        tooling=tooling,
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.writelines(f"{key}={value}\n" for key, value in outputs.items())
    if request["reason"]:
        raise SystemExit(request["reason"])


def _retirement_request(github, pr):
    comments = github.paginate(f"issues/{pr['number']}/comments")
    if find_existing_report(comments) is None:
        return None
    return {
        "number": pr["number"],
        "head": pr["head"]["sha"],
        "base": pr["base"]["sha"],
        "title": pr["title"],
        "description": pr.get("body") or "",
        "tasks": [],
        "reason": "",
        "retire": True,
    }


if __name__ == "__main__":
    main()
