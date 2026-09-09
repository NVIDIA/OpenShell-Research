# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the repository reviewer contract in the OAR environment."""

from __future__ import annotations

import copy
import importlib.util
import json
import shlex
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".github/openshell-agents/profiles/ci-reviewer"
CRITERIA = {
    "review-tool": [
        "correctness",
        "robustness_security",
        "maintainability_complexity",
        "tests_verification",
        "usability_integration",
    ],
    "review-research-spike": [
        "method_validity",
        "evidence_claims",
        "reproducibility",
        "clarity_limitations",
        "implementation_proportionality",
    ],
    "review-use-case-example": [
        "workflow_correctness",
        "reproducibility",
        "instructional_clarity",
        "safe_configuration",
        "scope_relevance",
    ],
}


def example_result(task: str) -> dict:
    return {
        "task": task,
        "verdict": "pass",
        "summary": "The project meets its stated purpose.",
        "guidelines_assessment": {
            "verdict": "pass",
            "explanation": "Applicable requirements verified.",
        },
        "criterion_scores": [
            {"criterion": name, "score": 95, "explanation": "Supported by inspection."}
            for name in CRITERIA[task]
        ],
        "overall_score": 95,
        "findings": [],
        "strengths": [],
        "limitations": [],
    }


@unittest.skipUnless(
    importlib.util.find_spec("openshell_agent_runner") is not None,
    "Run with the OAR uv environment; documentation checks do not install OAR.",
)
class CIReviewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from jsonschema import Draft202012Validator
        from openshell_agent_runner.config import load_profile

        cls.profile = load_profile(PROFILE)
        cls.schema = json.loads((PROFILE / "schemas/review.json").read_text())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_tasks_share_runtime_prompt_and_schema_but_select_only_two_skills(self):
        self.assertEqual(self.profile.profile.id, "ci-reviewer")
        self.assertEqual(list(self.profile.profile.tasks), list(CRITERIA))
        for name, task in self.profile.profile.tasks.items():
            with self.subTest(task=name):
                self.assertEqual(task.prompt, Path("prompt.md"))
                self.assertEqual(task.output_schema, Path("schemas/review.json"))
                self.assertEqual(
                    task.skills,
                    [Path("skills/review-common"), Path("skills") / name],
                )
                self.assertEqual(task.prompt_variables["review_skill"].default, name)
                self.assertEqual(task.required_input, "repository")
                self.assertIsNone(task.prompt_variables["guidelines_path"].default)

    def test_each_task_resolves_runtime_inputs_and_multiple_variables(self):
        from openshell_agent_runner.prompt_templates import render_prompt_template
        from openshell_agent_runner.runner import RunRequest, resolve_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "small experiment"
            repository.mkdir()
            for name, task in self.profile.profile.tasks.items():
                with self.subTest(task=name):
                    result = resolve_run(
                        RunRequest(
                            profile_directory=PROFILE,
                            task_id=name,
                            input_path=repository,
                            prompt_variables=(
                                "guidelines_path=/workspace/trusted-guidelines.md",
                                "focus=Assess the complete project",
                                "context=Evidence at /workspace/source; {{ literal }}",
                            ),
                            output=root / "review.json",
                        )
                    )
                    values = dict(result.prompt_variables)
                    self.assertEqual(values["review_skill"], name)
                    prompt = render_prompt_template(
                        (PROFILE / task.prompt).read_text(), values
                    )
                    self.assertIn(values["oar.input_path"], prompt)
                    self.assertIn("Assess the complete project", prompt)
                    self.assertIn("{{ literal }}", prompt)
                    self.assertIn("/workspace/trusted-guidelines.md", prompt)
                    for other in set(CRITERIA) - {name}:
                        self.assertNotIn(other, prompt)

    def test_each_task_accepts_its_own_complete_rubric(self):
        for task in CRITERIA:
            with self.subTest(task=task):
                self.validator.validate(example_result(task))

    def test_real_cli_dry_runs_all_tasks_without_publishing_results(self):
        from openshell_agent_runner.cli import app
        from typer.testing import CliRunner

        fixtures = ROOT / "projects/openshell-agent-runner/tests/fixtures/reviewer-e2e"
        guidelines = ROOT / "projects/PROJECT_GUIDELINES.md"
        remote = "/workspace/review-context/PROJECT_GUIDELINES.md"
        with tempfile.TemporaryDirectory() as directory:
            for case in json.loads((fixtures / "cases.json").read_text()):
                output = Path(directory) / f"{case['id']}.json"
                result = CliRunner().invoke(
                    app,
                    [
                        "run",
                        str(PROFILE),
                        "--task",
                        case["task"],
                        "--input",
                        str(fixtures / case["input"]),
                        "--upload",
                        f"{guidelines}:{remote}",
                        "--prompt-var",
                        f"guidelines_path={remote}",
                        "--output",
                        str(output),
                        "--dry-run",
                    ],
                )
                with self.subTest(task=case["task"]):
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertIn(remote, result.output)
                    self.assertFalse(output.exists())

    def test_workflows_keep_trusted_guidelines_separate_and_smoke_read_only(self):
        import yaml

        workflows = ROOT / ".github/workflows"
        review = yaml.safe_load((workflows / "pr-review.yml").read_text())
        self.assertEqual(set(review["on"]), {"pull_request_target"})
        jobs = review["jobs"]
        preparation = next(
            step["run"]
            for step in jobs["review"]["steps"]
            if step["name"] == "Prepare inert review inputs"
        )
        copy = next(
            shlex.split(line)
            for line in preparation.splitlines()
            if "PROJECT_GUIDELINES.md" in line
        )
        self.assertEqual(
            copy,
            [
                "cp",
                "tooling/projects/PROJECT_GUIDELINES.md",
                "$RUNNER_TEMP/review-inputs/review-context/project-guidelines.md",
            ],
        )
        self.assertEqual(jobs["review"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["report"]["permissions"]["pull-requests"], "write")

        smoke = yaml.safe_load((workflows / "reviewer-profiles-e2e.yml").read_text())
        self.assertEqual(set(smoke["jobs"]), {"reviewer-e2e"})
        job = smoke["jobs"]["reviewer-e2e"]
        self.assertEqual(job["permissions"], {"contents": "read"})
        run = next(step["run"] for step in job["steps"] if step.get("id") == "reviews")
        arguments = shlex.split(run)
        upload = "$GITHUB_WORKSPACE/projects/PROJECT_GUIDELINES.md:/workspace/review-context/PROJECT_GUIDELINES.md"
        self.assertIn(upload, arguments)
        self.assertIn(f"guidelines_path={upload.split(':', 1)[1]}", arguments)

    def test_guideline_assessment_is_part_of_every_result(self):
        for task in CRITERIA:
            result = example_result(task)
            del result["guidelines_assessment"]
            self.assertFalse(self.validator.is_valid(result))
            result["guidelines_assessment"] = {"verdict": "pass", "explanation": ""}
            self.assertFalse(self.validator.is_valid(result))
            for verdict in ("needs_changes", "inconclusive"):
                result["guidelines_assessment"] = {
                    "verdict": verdict,
                    "explanation": "A requirement could not be met.",
                }
                result["verdict"] = "pass"
                self.assertFalse(self.validator.is_valid(result))
                result["verdict"] = verdict
                self.assertTrue(self.validator.is_valid(result))

    def test_smoke_inventory_exercises_all_tasks_with_declared_project_kinds(self):
        import yaml

        fixtures = ROOT / "projects/openshell-agent-runner/tests/fixtures/reviewer-e2e"
        cases = json.loads((fixtures / "cases.json").read_text())
        self.assertEqual({case["task"] for case in cases}, set(CRITERIA))
        for case in cases:
            project = fixtures / case["input"]
            metadata = yaml.safe_load((project / "project.yaml").read_text())
            self.assertEqual(case["task"], f"review-{metadata['kind']}")
            self.assertTrue((project / "README.md").is_file())

    def test_trusted_guidelines_are_required_at_resolution(self):
        from openshell_agent_runner.errors import ConfigurationError
        from openshell_agent_runner.runner import RunRequest, resolve_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for task in CRITERIA:
                with (
                    self.subTest(task=task),
                    self.assertRaisesRegex(ConfigurationError, "guidelines_path"),
                ):
                    resolve_run(
                        RunRequest(
                            profile_directory=PROFILE,
                            task_id=task,
                            input_path=root,
                            output=root / "review.json",
                        )
                    )

    def test_schema_rejects_missing_duplicate_wrong_order_and_wrong_task_criteria(self):
        for task in CRITERIA:
            valid = example_result(task)
            mutations = []
            missing = copy.deepcopy(valid)
            missing["criterion_scores"].pop()
            mutations.append(missing)
            duplicate = copy.deepcopy(valid)
            duplicate["criterion_scores"][1] = duplicate["criterion_scores"][0]
            mutations.append(duplicate)
            reversed_scores = copy.deepcopy(valid)
            reversed_scores["criterion_scores"].reverse()
            mutations.append(reversed_scores)
            wrong_task = copy.deepcopy(valid)
            wrong_task["task"] = next(other for other in CRITERIA if other != task)
            mutations.append(wrong_task)
            for index, result in enumerate(mutations):
                with self.subTest(task=task, mutation=index):
                    self.assertFalse(self.validator.is_valid(result))

    def test_score_bounds_and_verdicts(self):
        for task in CRITERIA:
            for score in (0, 100):
                result = example_result(task)
                result["overall_score"] = score
                for criterion in result["criterion_scores"]:
                    criterion["score"] = score
                for verdict in ("pass", "needs_changes", "inconclusive"):
                    result["verdict"] = verdict
                    with self.subTest(task=task, score=score, verdict=verdict):
                        self.validator.validate(result)
            for score in (-1, 101, 1.5, True):
                for field in ("overall_score", "criterion_score"):
                    result = example_result(task)
                    if field == "overall_score":
                        result[field] = score
                    else:
                        result["criterion_scores"][0]["score"] = score
                    with self.subTest(task=task, field=field, score=score):
                        self.assertFalse(self.validator.is_valid(result))
        result = example_result("review-tool")
        result["verdict"] = "findings"
        self.assertFalse(self.validator.is_valid(result))

    def test_findings_require_evidence_and_allow_missing_file_without_invented_line(
        self,
    ):
        result = example_result("review-tool")
        finding = {
            "severity": "medium",
            "title": "Documented configuration file is missing",
            "path": "projects/example/config.json",
            "evidence": "The documented command reads config.json, absent from the tree.",
            "impact": "The documented command cannot start.",
            "recommendation": "Supply the configuration used by the example.",
        }
        result["findings"] = [finding]
        result["verdict"] = "needs_changes"
        self.validator.validate(result)
        finding["line"] = 1
        self.validator.validate(result)
        finding["line"] = 0
        self.assertFalse(self.validator.is_valid(result))
        del finding["line"]
        for key in ("evidence", "impact", "recommendation", "path"):
            incomplete = copy.deepcopy(result)
            del incomplete["findings"][0][key]
            self.assertFalse(self.validator.is_valid(incomplete))


if __name__ == "__main__":
    unittest.main()
