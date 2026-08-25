import importlib.util
import json
import subprocess
import sys
from pathlib import Path

RUNNER_PATH = Path(__file__).parents[1] / "qa/reviewer_profiles/runner.py"
SPEC = importlib.util.spec_from_file_location("reviewer_profile_qa_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)

PROFILE_CRITERIA = RUNNER.PROFILE_CRITERIA
SUITE_ROOT = RUNNER.SUITE_ROOT
evaluate_output = RUNNER.evaluate_output
load_cases = RUNNER.load_cases

PROFILE_ROOT = Path(__file__).parents[1] / "src/openshell_agent_runner/profiles"
PROJECT_ROOT = Path(__file__).parents[1]


def criterion_scores(profile: str, score: int) -> list[dict[str, object]]:
    return [
        {
            "criterion": criterion,
            "score": score,
            "explanation": "Grounded assessment.",
        }
        for criterion in PROFILE_CRITERIA[profile]
    ]


def test_manifest_covers_each_profile_and_all_inputs_exist() -> None:
    cases = load_cases()

    assert len(cases) == 10
    assert {case["profile"] for case in cases} == set(PROFILE_CRITERIA)
    assert all((SUITE_ROOT / case["input"]).exists() for case in cases)
    coverage = {item for case in cases for item in case["coverage"]}
    assert "multiple prompt variables" in coverage
    assert "prompt injection resistance" in coverage
    assert ".txt document" in coverage
    assert "false-positive control" in coverage


def test_fixture_ground_truth_is_stable() -> None:
    expected_success = {
        "code-clean-library": True,
        "code-boundary-bug": False,
        "code-scope-discipline": False,
        "code-prompt-injection": True,
    }

    for name, should_pass in expected_success.items():
        repository = SUITE_ROOT / "fixtures" / name
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        assert (completed.returncode == 0) is should_pass


def test_evaluator_accepts_grounded_code_result() -> None:
    case = next(case for case in load_cases() if case["id"] == "code-boundary-bug")
    payload = {
        "verdict": "needs_changes",
        "summary": "The page drops its final requested item.",
        "criterion_scores": criterion_scores("code-reviewer", 80),
        "overall_score": 80,
        "findings": [
            {
                "severity": "high",
                "category": "correctness",
                "title": "Off-by-one slice",
                "path": "src/paging.py",
                "line": 10,
                "evidence": "limit - 1 excludes the final requested item.",
                "impact": "Pages return too few items.",
                "recommendation": "Slice through limit.",
            }
        ],
        "strengths": [],
        "limitations": [],
    }

    checks = evaluate_output(case, payload, SUITE_ROOT / case["input"], PROFILE_ROOT)

    assert all(check.status == "passed" for check in checks)


def test_evaluator_rejects_bad_score_math_and_ungrounded_quote() -> None:
    case = next(
        case
        for case in load_cases()
        if case["id"] == "technical-writing-prompt-injection"
    )
    payload = {
        "verdict": "needs_changes",
        "summary": "The deprecation timing is contradictory.",
        "criterion_scores": criterion_scores("technical-writing-reviewer", 80),
        "overall_score": 99,
        "findings": [
            {
                "severity": "high",
                "category": "accuracy",
                "title": "Contradictory deadline",
                "quote": "This quote is not in the document.",
                "line": 500,
                "explanation": "Immediately conflicts with 30 days.",
                "recommendation": "State one deadline.",
            }
        ],
        "strengths": [],
        "limitations": [],
    }

    checks = evaluate_output(case, payload, SUITE_ROOT / case["input"], PROFILE_ROOT)
    failed = {check.name for check in checks if check.status == "failed"}

    assert "Overall score arithmetic" in failed
    assert "Finding grounding" in failed


def test_evaluator_enforces_slop_verdict_and_authorship_boundary() -> None:
    case = next(
        case for case in load_cases() if case["id"] == "slop-systemic-launch-post"
    )
    payload = {
        "verdict": "polish",
        "summary": "This AI-generated document repeats stock framing.",
        "criterion_scores": criterion_scores("slop-cop", 70),
        "overall_score": 70,
        "findings": [
            {
                "prevalence": "systemic",
                "category": "formulaic_structure",
                "quote": "It is important to note that deployment is not merely a task; it is a journey.",
                "line": 4,
                "effect": "The repeated formula obscures the claim.",
                "suggested_rewrite": "Deployment failures compound across releases.",
            }
        ],
        "voice_to_preserve": [],
        "limitations": [],
    }

    checks = evaluate_output(case, payload, SUITE_ROOT / case["input"], PROFILE_ROOT)
    failed = {check.name for check in checks if check.status == "failed"}

    assert "Verdict consistency" in failed
    assert "Expected verdict" in failed
    assert "No authorship claim" in failed


def test_dry_run_suite_resolves_every_case_and_writes_html(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    results = tmp_path / "results.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            "--mode",
            "dry-run",
            "--model",
            "qa/model",
            "--report",
            str(report),
            "--results-json",
            str(results),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(results.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert all(result["static_status"] == "passed" for result in payload["results"])
    assert all(result["live_status"] == "not_run" for result in payload["results"])
    assert "Live profile behavior was not exercised" in report.read_text(
        encoding="utf-8"
    )
