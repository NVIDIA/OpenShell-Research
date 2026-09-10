# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real selection, snapshot, CLI, and report flow; GitHub/OpenShell are simulated."""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
sys.path.insert(0, str(ROOT / "projects/openshell-agent-runner/tests"))

import github_api
import pr_review
import review_report
from test_ci_reviewer import PROFILE, example_result
from test_lifecycle import fake_openshell


class ReviewGitHub:
    repository = "example/research"

    def __init__(self, responses, files):
        self.responses = responses
        self.files = files
        self.comments = []
        self.writes = []

    def request(self, method, path, data=None):
        if method == "GET":
            return self.responses[path]
        self.writes.append((method, path))
        if (method, path) == ("POST", "issues/7/comments"):
            self.comments.append(
                {
                    "id": 1,
                    "user": {"type": "Bot", "login": "github-actions[bot]"},
                    **data,
                }
            )
        elif (method, path) == ("PATCH", "issues/comments/1"):
            self.comments[0].update(data)
        else:
            raise AssertionError((method, path))

    def paginate(self, path):
        if path == "pulls/7/files?per_page=100":
            return self.files
        assert path == "issues/7/comments"
        return self.comments


@pytest.mark.parametrize("invalid_task", [None, "review-research-spike"])
def test_new_projects_reach_one_current_report(tmp_path, monkeypatch, invalid_task):
    checkout = tmp_path / "pr-data"
    existing = checkout / "projects/existing"
    existing.mkdir(parents=True)
    (existing / "README.md").write_text("Existing project.\n")
    _git(checkout, "init", "-q")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "Base")
    base = _git(checkout, "rev-parse", "HEAD")
    for kind in ("tool", "research-spike", "use-case-example"):
        project = checkout / "projects" / kind
        project.mkdir()
        (project / "project.yaml").write_text(f"kind: {kind}\n")
        (project / "README.md").write_text(f"# New {kind}\n")
    (existing / "README.md").write_text("Changed, but not a new project.\n")
    (checkout / "projects/PROJECT_GUIDELINES.md").write_text(
        "Untrusted PR guidelines.\n"
    )
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "Add projects")
    head = _git(checkout, "rev-parse", "HEAD")
    changed = []
    for line in _git(checkout, "diff", "--name-status", base, head).splitlines():
        status, path = line.split("\t", 1)
        changed.append(
            {"filename": path, "status": {"A": "added", "M": "modified"}[status]}
        )
    pr = {
        "number": 7,
        "state": "open",
        "draft": False,
        "changed_files": len(changed),
        "title": "Add three projects",
        "body": "Assess these additions.",
        "user": {"login": "contributor"},
        "base": {"sha": base},
        "head": {"sha": head, "repo": {"full_name": ReviewGitHub.repository}},
    }
    responses = {
        "pulls/7": pr,
        f"git/trees/{base}": {
            "tree": [{"path": "projects", "type": "tree", "sha": "base-projects"}]
        },
        "git/trees/base-projects": {"tree": [{"path": "existing", "type": "tree"}]},
    }
    for metadata in checkout.glob("projects/*/project.yaml"):
        responses[f"contents/{metadata.relative_to(checkout)}?ref={head}"] = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(metadata.read_bytes()).decode(),
        }
    github = ReviewGitHub(responses, changed)
    monkeypatch.setattr(pr_review, "GitHub", lambda: github)
    monkeypatch.setattr(github_api, "GitHub", lambda: github)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": pr}))
    request_file = tmp_path / "request.json"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pr_review.py", "--tooling", str(ROOT), "--output", str(request_file)],
    )
    pr_review.main()
    request = json.loads(request_file.read_text())
    assert {item["kind"] for item in request["tasks"]} == {
        "tool",
        "research-spike",
        "use-case-example",
    }
    assert request["head"] == head and request["base"] == base
    assert "ready=true" in (tmp_path / "outputs").read_text()

    snapshot = tmp_path / "inputs"
    subprocess.run(
        [
            "bash",
            str(ROOT / ".github/scripts/prepare-review-inputs.sh"),
            str(checkout),
            str(snapshot),
            base,
            head,
        ],
        check=True,
    )
    context = snapshot / "review-context"
    shutil.copyfile(request_file, context / "request.json")
    guidelines = context / "project-guidelines.md"
    shutil.copyfile(ROOT / "projects/PROJECT_GUIDELINES.md", guidelines)
    assert (
        guidelines.read_bytes()
        != (snapshot / "source/projects/PROJECT_GUIDELINES.md").read_bytes()
    )

    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    executable, state, log = fake_openshell(fakebin)
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(state))
    monkeypatch.setenv("FAKE_LOG", str(log))
    results = tmp_path / "results"
    results.mkdir()
    for item in request["tasks"]:
        result = example_result(item["task"])
        result["verdict"] = "needs_changes"
        result["findings"] = [
            {
                "severity": "medium",
                "title": "Missing instructions",
                "path": f"{item['input']}/README.md",
                "evidence": "The README only contains a title.",
                "impact": "Readers cannot reproduce the project.",
                "recommendation": "Document the intended workflow.",
            }
        ]
        monkeypatch.setenv(
            "FAKE_OUTPUT", "{}" if item["task"] == invalid_task else json.dumps(result)
        )
        run = subprocess.run(
            [
                str(Path(sys.executable).with_name("oar")),
                "run",
                str(PROFILE),
                "--task",
                item["task"],
                "--input",
                str(snapshot / "source" / item["input"]),
                "--upload",
                f"{snapshot}/source:/workspace/source",
                "--upload",
                f"{context}:/workspace",
                "--prompt-var",
                "guidelines_path=/workspace/review-context/project-guidelines.md",
                "--prompt-var",
                f"focus=Assess the complete new project at {item['input']}.",
                "--output",
                str(results / f"{item['id']}.json"),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert run.returncode == (3 if item["task"] == invalid_task else 0), run.stderr
        assert not state.exists()
        prompt = log.with_name("uploaded-prompt.md").read_text()
        assert (
            item["task"] in prompt
            and "/workspace/review-context/project-guidelines.md" in prompt
        )
    assert review_report.main(
        ["verify", "--request", str(request_file), "--results", str(results)]
    ) == (1 if invalid_task else 0)

    monkeypatch.setenv("GITHUB_REPOSITORY", github.repository)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    arguments = [
        "publish",
        "--request",
        str(request_file),
        "--results",
        str(results),
        "--outcome",
        "failure" if invalid_task else "success",
        "--output",
        str(tmp_path / "report.md"),
    ]
    for run_id in (100, 101):
        monkeypatch.setenv("GITHUB_RUN_ID", str(run_id))
        assert review_report.main(arguments) == 0
    assert github.writes == [
        ("POST", "issues/7/comments"),
        ("PATCH", "issues/comments/1"),
    ]
    body = github.comments[0]["body"]
    assert body == (tmp_path / "report.md").read_text().strip()
    assert body in (tmp_path / "summary.md").read_text()
    assert head in body and "advisory" in body and "Needs changes" in body
    assert "projects/existing" not in body
    assert ("Not completed" in body) is bool(invalid_task)
    for item in request["tasks"]:
        assert item["input"] in body
    pr["head"]["sha"] = "f" * 40
    assert review_report.main(arguments) == 0
    assert len(github.writes) == 2, "A stale run must not update the PR comment"


def _git(directory, *arguments):
    return subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
