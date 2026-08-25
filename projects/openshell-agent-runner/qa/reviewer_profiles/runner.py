#!/usr/bin/env python3
"""Run isolated end-to-end QA experiments for packaged reviewer profiles."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SUITE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SUITE_ROOT.parents[1]
DEFAULT_REPORT = SUITE_ROOT / "report.html"
MINIMUM_OPEN_SHELL_VERSION = (0, 0, 111)
PROFILE_CRITERIA = {
    "code-reviewer": [
        "correctness",
        "robustness_security",
        "maintainability_complexity",
        "tests_verification",
        "usability_integration",
    ],
    "technical-writing-reviewer": [
        "accuracy_grounding",
        "clarity_precision",
        "completeness",
        "structure_navigation",
        "audience_fit",
        "actionability_evidence",
    ],
    "slop-cop": [
        "substance_directness",
        "specificity",
        "structural_naturalness",
        "rhythm_style",
        "distinctive_voice",
    ],
}


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class CaseResult:
    case_id: str
    profile: str
    coverage: list[str]
    static_status: str = "pending"
    live_status: str = "not_run"
    duration_seconds: float = 0.0
    verdict: str | None = None
    overall_score: int | None = None
    summary: str = ""
    checks: list[Check] = field(default_factory=list)
    error: str = ""
    output: dict[str, Any] | None = None


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "dry-run"), default="live")
    parser.add_argument("--gateway", default="openshell")
    parser.add_argument("--gateway-endpoint")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--model", required=True)
    parser.add_argument("--thinking", default="high")
    parser.add_argument("--openshell-bin", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--results-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.case_ids)
    results = [
        CaseResult(case["id"], case["profile"], case["coverage"]) for case in cases
    ]
    started = datetime.now(UTC)
    environment: dict[str, str] = {
        "mode": args.mode,
        "gateway": args.gateway_endpoint or args.gateway,
        "workspace": args.workspace,
        "model": args.model,
        "commit": git_commit(),
    }
    suite_status = "passed"

    with tempfile.TemporaryDirectory(prefix="oar-reviewer-qa-") as temporary:
        session_root = Path(temporary)
        try:
            command_environment = build_environment(args, session_root)
            gateway = configure_gateway(args, session_root, command_environment)
        except (RuntimeError, ValueError) as error:
            block_all(results, str(error), static=True)
            suite_status = "blocked"
        else:
            environment["gateway"] = args.gateway_endpoint or gateway
            environment["openshell_version"] = openshell_version(command_environment)

            profiles_root = session_root / "profiles"
            init = run_command(
                oar_command(
                    "init",
                    str(profiles_root),
                    "--model",
                    args.model,
                    "--thinking",
                    args.thinking,
                ),
                command_environment,
                120,
            )
            if init.returncode != 0:
                message = command_error("profile initialization", init)
                block_all(results, message, static=True)
                suite_status = "failed"
            else:
                run_static_checks(
                    cases,
                    results,
                    profiles_root,
                    gateway,
                    args,
                    command_environment,
                    session_root,
                )
                if any(result.static_status == "failed" for result in results):
                    suite_status = "failed"
                elif args.mode == "dry-run":
                    for result in results:
                        result.live_status = "not_run"
                else:
                    blocker = live_preflight(
                        gateway, args, command_environment, environment
                    )
                    if blocker:
                        block_all(results, blocker)
                        suite_status = "blocked"
                    else:
                        run_live_checks(
                            cases,
                            results,
                            profiles_root,
                            gateway,
                            args,
                            command_environment,
                            session_root,
                        )
                        if any(result.live_status == "failed" for result in results):
                            suite_status = "failed"

    finished = datetime.now(UTC)
    report = render_report(suite_status, started, finished, environment, results)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    if args.results_json:
        args.results_json.parent.mkdir(parents=True, exist_ok=True)
        args.results_json.write_text(
            json.dumps(
                {
                    "status": suite_status,
                    "environment": environment,
                    "results": [asdict(result) for result in results],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"QA status: {suite_status}")
    print(f"HTML report: {args.report.resolve()}")
    return 0 if suite_status == "passed" else 2


def load_cases(selected: list[str] | None = None) -> list[dict[str, Any]]:
    manifest = json.loads((SUITE_ROOT / "cases.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = manifest["cases"]
    identifiers = [case["id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("QA case identifiers must be unique")
    if selected:
        unknown = sorted(set(selected) - set(identifiers))
        if unknown:
            raise ValueError(f"unknown QA case: {unknown[0]}")
        cases = [case for case in cases if case["id"] in selected]
    return cases


def build_environment(args: argparse.Namespace, session_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    if args.openshell_bin:
        executable = args.openshell_bin.resolve()
        if not executable.is_file():
            raise ValueError(f"OpenShell executable does not exist: {executable}")
        bin_directory = session_root / "bin"
        bin_directory.mkdir()
        (bin_directory / "openshell").symlink_to(executable)
        environment["PATH"] = f"{bin_directory}{os.pathsep}{environment['PATH']}"
    if args.gateway_endpoint:
        environment["XDG_CONFIG_HOME"] = str(session_root / "config")
    return environment


def configure_gateway(
    args: argparse.Namespace,
    session_root: Path,
    environment: dict[str, str],
) -> str:
    if not args.gateway_endpoint:
        return args.gateway
    gateway = "reviewer-qa"
    command = [
        "openshell",
        "gateway",
        "add",
        args.gateway_endpoint,
        "--name",
        gateway,
    ]
    if args.gateway_endpoint.startswith("http://"):
        command.append("--local")
    completed = run_command(command, environment, 30)
    if completed.returncode != 0:
        raise RuntimeError(command_error("gateway registration", completed))
    return gateway


def run_static_checks(
    cases: list[dict[str, Any]],
    results: list[CaseResult],
    profiles_root: Path,
    gateway: str,
    args: argparse.Namespace,
    environment: dict[str, str],
    session_root: Path,
) -> None:
    validated: dict[str, CommandResult] = {}
    for case, result in zip(cases, results, strict=True):
        profile = profiles_root / case["profile"]
        if case["profile"] not in validated:
            validated[case["profile"]] = run_command(
                oar_command("validate", str(profile)), environment, 60
            )
        validation = validated[case["profile"]]
        if validation.returncode != 0:
            result.static_status = "failed"
            result.error = command_error("profile validation", validation)
            continue
        output = session_root / "dry-run" / f"{case['id']}.json"
        output.parent.mkdir(exist_ok=True)
        command = case_command(
            case, profile, output, gateway, args.workspace, args.timeout_seconds
        )
        command.append("--dry-run")
        completed = run_command(command, environment, 120)
        if completed.returncode != 0:
            result.static_status = "failed"
            result.error = command_error("resolved-command check", completed)
            continue
        result.static_status = "passed"
        result.checks.append(
            Check("CLI resolution", "passed", "Profile validates and dry-run resolves.")
        )


def live_preflight(
    gateway: str,
    args: argparse.Namespace,
    environment: dict[str, str],
    metadata: dict[str, str],
) -> str | None:
    version = metadata["openshell_version"]
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MINIMUM_OPEN_SHELL_VERSION:
        return f"OpenShell 0.0.111+ is required; found {version or 'unknown version'}."
    doctor = run_command(
        oar_command("doctor", "--gateway", gateway, "--workspace", args.workspace),
        environment,
        60,
    )
    if doctor.returncode != 0:
        return command_error("OpenShell readiness check", doctor)
    inference = run_command(
        [
            "openshell",
            "inference",
            "get",
            "--gateway",
            gateway,
            "--workspace",
            args.workspace,
        ],
        environment,
        60,
    )
    if inference.returncode != 0:
        return command_error("inference route check", inference)
    if "not configured" in inference.stdout.lower():
        return "The selected gateway/workspace has no configured inference route."
    metadata["inference"] = "configured"
    return None


def run_live_checks(
    cases: list[dict[str, Any]],
    results: list[CaseResult],
    profiles_root: Path,
    gateway: str,
    args: argparse.Namespace,
    environment: dict[str, str],
    session_root: Path,
) -> None:
    before = sandbox_names(gateway, args.workspace, environment)
    for case, result in zip(cases, results, strict=True):
        if result.static_status != "passed":
            result.live_status = "blocked"
            continue
        output = session_root / "outputs" / f"{case['id']}.json"
        output.parent.mkdir(exist_ok=True)
        input_path = SUITE_ROOT / case["input"]
        before_hash = hash_input(input_path)
        completed = run_command(
            case_command(
                case,
                profiles_root / case["profile"],
                output,
                gateway,
                args.workspace,
                args.timeout_seconds,
            ),
            environment,
            args.timeout_seconds + 120,
        )
        result.duration_seconds = completed.duration_seconds
        if completed.returncode != 0:
            result.live_status = "failed"
            result.error = command_error("live OAR run", completed)
            continue
        if before_hash != hash_input(input_path):
            result.live_status = "failed"
            result.error = "The host input fixture changed during the run."
            continue
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result.live_status = "failed"
            result.error = f"Cannot read structured result: {error}"
            continue
        result.output = payload
        result.verdict = payload.get("verdict")
        result.overall_score = payload.get("overall_score")
        result.summary = payload.get("summary", "")
        result.checks.extend(evaluate_output(case, payload, input_path, profiles_root))
        result.checks.append(
            Check("Host input isolation", "passed", "Fixture hash is unchanged.")
        )
        result.live_status = (
            "passed"
            if all(check.status == "passed" for check in result.checks)
            else "failed"
        )
    after = sandbox_names(gateway, args.workspace, environment)
    leaked = sorted(after - before)
    if leaked:
        detail = f"New sandboxes remain: {', '.join(leaked)}"
        for result in results:
            result.checks.append(Check("Sandbox cleanup", "failed", detail))
            if result.live_status == "passed":
                result.live_status = "failed"
    else:
        for result in results:
            if result.live_status != "blocked":
                result.checks.append(
                    Check("Sandbox cleanup", "passed", "No new sandbox remains.")
                )


def evaluate_output(
    case: dict[str, Any],
    payload: dict[str, Any],
    input_path: Path,
    profiles_root: Path,
) -> list[Check]:
    checks: list[Check] = []
    schema_path = profiles_root / case["profile"] / "schemas/review.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    checks.append(
        Check(
            "Output schema",
            "passed" if not errors else "failed",
            "Valid profile result." if not errors else errors[0].message,
        )
    )
    if errors:
        return checks

    criteria = [item["criterion"] for item in payload["criterion_scores"]]
    scores = [item["score"] for item in payload["criterion_scores"]]
    expected_criteria = PROFILE_CRITERIA[case["profile"]]
    checks.append(
        check("Criterion order", criteria == expected_criteria, ", ".join(criteria))
    )
    computed = math.floor(sum(scores) / len(scores) + 0.5)
    checks.append(
        check(
            "Overall score arithmetic",
            payload["overall_score"] == computed,
            f"reported {payload['overall_score']}; computed {computed}",
        )
    )
    checks.append(verdict_check(case["profile"], payload))
    checks.extend(expectation_checks(case["expect"], payload))
    checks.append(grounding_check(case["profile"], payload, input_path))
    if case["profile"] == "slop-cop":
        lowered = json.dumps(payload).lower()
        attribution = ("ai-generated", "written by ai", "generated by ai", "ai wrote")
        checks.append(
            check(
                "No authorship claim",
                not any(term in lowered for term in attribution),
                "No AI-authorship claim appears in the result.",
            )
        )
    return checks


def verdict_check(profile: str, payload: dict[str, Any]) -> Check:
    verdict = payload["verdict"]
    score = payload["overall_score"]
    findings = payload["findings"]
    if profile == "slop-cop":
        systemic = any(item["prevalence"] == "systemic" for item in findings)
        valid = (
            (verdict == "clean" and score >= 90 and not findings)
            or (verdict == "polish" and score >= 75 and findings and not systemic)
            or (verdict == "revise" and (score < 75 or systemic))
        )
    else:
        valid = (
            (verdict == "pass" and score >= 90 and not findings)
            or (verdict == "needs_changes" and bool(findings))
            or (verdict == "inconclusive" and bool(payload["limitations"]))
        )
    return check(
        "Verdict consistency",
        valid,
        f"{verdict}, score {score}, {len(findings)} finding(s)",
    )


def expectation_checks(
    expectation: dict[str, Any], payload: dict[str, Any]
) -> list[Check]:
    checks = [
        check(
            "Expected verdict",
            payload["verdict"] in expectation["verdict_in"],
            f"got {payload['verdict']}; expected {', '.join(expectation['verdict_in'])}",
        )
    ]
    score = payload["overall_score"]
    findings = payload["findings"]
    if "score_min" in expectation:
        checks.append(
            check(
                "Minimum score",
                score >= expectation["score_min"],
                f"{score} >= {expectation['score_min']}",
            )
        )
    if "score_max" in expectation:
        checks.append(
            check(
                "Maximum score",
                score <= expectation["score_max"],
                f"{score} <= {expectation['score_max']}",
            )
        )
    if "findings_min" in expectation:
        checks.append(
            check(
                "Minimum findings",
                len(findings) >= expectation["findings_min"],
                f"{len(findings)} found",
            )
        )
    if "findings_max" in expectation:
        checks.append(
            check(
                "Maximum findings",
                len(findings) <= expectation["findings_max"],
                f"{len(findings)} found",
            )
        )
    rendered = json.dumps(payload).lower()
    for index, terms in enumerate(expectation.get("required_any", []), start=1):
        checks.append(
            check(
                f"Required evidence {index}",
                any(term.lower() in rendered for term in terms),
                "one of: " + ", ".join(terms),
            )
        )
    forbidden = expectation.get("forbidden", [])
    if forbidden:
        present = [term for term in forbidden if term.lower() in rendered]
        checks.append(
            check(
                "Scope discipline",
                not present,
                "No forbidden scope expansion."
                if not present
                else "found: " + ", ".join(present),
            )
        )
    return checks


def grounding_check(profile: str, payload: dict[str, Any], input_path: Path) -> Check:
    findings = payload["findings"]
    if not findings:
        return Check("Finding grounding", "passed", "No findings to ground.")
    if profile == "code-reviewer":
        missing = []
        for finding in findings:
            candidate = resolve_reported_path(input_path, finding["path"])
            if candidate is None:
                missing.append(finding["path"])
                continue
            line = finding.get("line")
            if line and line > len(candidate.read_text(encoding="utf-8").splitlines()):
                missing.append(f"{finding['path']}:{line}")
        return check(
            "Finding grounding",
            not missing,
            "All paths and lines resolve."
            if not missing
            else "unresolved: " + ", ".join(missing),
        )
    text = input_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    ungrounded = []
    for finding in findings:
        quote = finding["quote"]
        line = finding["line"]
        if quote not in text or line > len(lines):
            ungrounded.append(f"line {line}: {quote[:40]}")
    return check(
        "Finding grounding",
        not ungrounded,
        "All quotes and lines resolve."
        if not ungrounded
        else "unresolved: " + "; ".join(ungrounded),
    )


def resolve_reported_path(repository: Path, reported: str) -> Path | None:
    direct = repository / reported
    if direct.is_file():
        return direct
    matches = [path for path in repository.rglob(Path(reported).name) if path.is_file()]
    return matches[0] if len(matches) == 1 else None


def check(name: str, passed: bool, detail: str) -> Check:
    return Check(name, "passed" if passed else "failed", detail)


def case_command(
    case: dict[str, Any],
    profile: Path,
    output: Path,
    gateway: str,
    workspace: str,
    timeout_seconds: int,
) -> list[str]:
    command = oar_command(
        "run",
        str(profile),
        "--task",
        case["task"],
        "--input",
        str((SUITE_ROOT / case["input"]).resolve()),
        "--output",
        str(output),
        "--gateway",
        gateway,
        "--workspace",
        workspace,
        "--timeout-seconds",
        str(timeout_seconds),
    )
    for name, value in case.get("prompt_variables", {}).items():
        command.extend(["--prompt-var", f"{name}={value}"])
    return command


def oar_command(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "openshell_agent_runner.cli", *arguments]


def run_command(
    command: list[str], environment: dict[str, str], timeout: int
) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode(errors="replace")
            if isinstance(error.stdout, bytes)
            else error.stdout or ""
        )
        stderr = (
            error.stderr.decode(errors="replace")
            if isinstance(error.stderr, bytes)
            else error.stderr or ""
        )
        return CommandResult(
            124,
            stdout,
            stderr,
            time.monotonic() - started,
        )


def sandbox_names(
    gateway: str, workspace: str, environment: dict[str, str]
) -> set[str]:
    completed = run_command(
        [
            "openshell",
            "sandbox",
            "list",
            "--gateway",
            gateway,
            "--workspace",
            workspace,
            "--names",
        ],
        environment,
        60,
    )
    return set(completed.stdout.splitlines()) if completed.returncode == 0 else set()


def openshell_version(environment: dict[str, str]) -> str:
    completed = run_command(["openshell", "--version"], environment, 30)
    return (
        completed.stdout.strip()
        if completed.returncode == 0
        else completed.stderr.strip()
    )


def hash_input(path: Path) -> str:
    digest = hashlib.sha256()
    files = (
        [path]
        if path.is_file()
        else sorted(item for item in path.rglob("*") if item.is_file())
    )
    for item in files:
        digest.update(str(item.relative_to(path.parent)).encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


def block_all(results: list[CaseResult], message: str, *, static: bool = False) -> None:
    for result in results:
        if static:
            result.static_status = "failed"
        result.live_status = "blocked"
        result.error = message


def command_error(stage: str, completed: CommandResult) -> str:
    detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostics"
    return f"{stage} failed with exit {completed.returncode}: {detail[-1200:]}"


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() or "unknown"


def render_report(
    status: str,
    started: datetime,
    finished: datetime,
    environment: dict[str, str],
    results: list[CaseResult],
) -> str:
    counts = {
        state: sum(result.live_status == state for result in results)
        for state in ("passed", "failed", "blocked", "not_run")
    }
    rows = "".join(render_case_row(result) for result in results)
    details = "".join(render_case_detail(result) for result in results)
    status_class = "ok" if status == "passed" else status
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reviewer profile QA report</title>
<style>
:root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
body {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 64px; line-height: 1.45; }}
h1, h2 {{ line-height: 1.15; }}
.meta, .coverage {{ color: #667085; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(130px,1fr)); gap: 12px; margin: 24px 0; }}
.card, details {{ border: 1px solid #98a2b3; border-radius: 10px; padding: 14px; }}
.card strong {{ display: block; font-size: 1.45rem; }}
.badge {{ border-radius: 999px; padding: 3px 9px; font-weight: 700; text-transform: uppercase; font-size: .75rem; }}
.ok, .passed {{ color: #067647; background: #ecfdf3; }}
.failed {{ color: #b42318; background: #fef3f2; }}
.blocked {{ color: #b54708; background: #fffaeb; }}
.not_run, .pending {{ color: #344054; background: #f2f4f7; }}
table {{ border-collapse: collapse; width: 100%; margin: 18px 0 28px; }}
th, td {{ border-bottom: 1px solid #98a2b3; padding: 9px; text-align: left; vertical-align: top; }}
details {{ margin: 12px 0; }}
summary {{ cursor: pointer; font-weight: 700; }}
ul {{ margin-bottom: 0; }}
code {{ font-size: .9em; }}
@media (prefers-color-scheme: dark) {{ .meta, .coverage {{ color: #d0d5dd; }} .ok,.passed,.failed,.blocked,.not_run,.pending {{ background: #1d2939; }} }}
</style>
</head>
<body>
<p class="badge {status_class}">{html.escape(status)}</p>
<h1>Reviewer profile QA</h1>
<p class="meta">{html.escape(started.isoformat())} · commit <code>{html.escape(environment["commit"])}</code> · mode {html.escape(environment["mode"])}</p>
<p>{report_conclusion(status, environment)}</p>
<section class="summary">
  <div class="card"><strong>{len(results)}</strong>cases</div>
  <div class="card"><strong>{counts["passed"]}</strong>passed live</div>
  <div class="card"><strong>{counts["failed"]}</strong>failed live</div>
  <div class="card"><strong>{counts["blocked"]}</strong>blocked live</div>
</section>
<h2>Environment</h2>
<table><tbody>{"".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in environment.items())}<tr><th>duration</th><td>{(finished - started).total_seconds():.1f}s</td></tr></tbody></table>
<h2>Experiment matrix</h2>
<table><thead><tr><th>Case</th><th>Profile</th><th>Coverage</th><th>CLI</th><th>Live</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Case evidence</h2>
{details}
</body>
</html>
"""


def report_conclusion(status: str, environment: dict[str, str]) -> str:
    if environment["mode"] == "dry-run":
        return "All CLI-level checks completed. Live profile behavior was not exercised in dry-run mode."
    if status == "passed":
        return "All live experiments and semantic assertions passed."
    if status == "blocked":
        return "CLI-level checks passed, but live profile execution was blocked by the environment. Blocked cases are not profile failures."
    return (
        "At least one experiment or assertion failed; inspect the case evidence below."
    )


def render_case_row(result: CaseResult) -> str:
    outcome = result.verdict or (
        "—" if result.overall_score is None else str(result.overall_score)
    )
    if result.verdict and result.overall_score is not None:
        outcome = f"{result.verdict} · {result.overall_score}/100"
    return f"<tr><td><code>{html.escape(result.case_id)}</code></td><td>{html.escape(result.profile)}</td><td class='coverage'>{html.escape(', '.join(result.coverage))}</td><td><span class='badge {result.static_status}'>{result.static_status}</span></td><td><span class='badge {result.live_status}'>{result.live_status}</span></td><td>{html.escape(outcome)}</td></tr>"


def render_case_detail(result: CaseResult) -> str:
    checks = (
        "".join(
            f"<li><span class='badge {check.status}'>{check.status}</span> <strong>{html.escape(check.name)}</strong> — {html.escape(check.detail)}</li>"
            for check in result.checks
        )
        or "<li>No live assertions ran.</li>"
    )
    error = (
        f"<p><strong>Diagnostic:</strong> {html.escape(result.error)}</p>"
        if result.error
        else ""
    )
    summary = (
        f"<p><strong>Reviewer summary:</strong> {html.escape(result.summary)}</p>"
        if result.summary
        else ""
    )
    return f"<details><summary>{html.escape(result.case_id)} · {result.live_status}</summary>{error}{summary}<ul>{checks}</ul></details>"


if __name__ == "__main__":
    raise SystemExit(main())
