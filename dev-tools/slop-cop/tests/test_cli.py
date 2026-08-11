from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import slop_cop.cli as cli_module
from slop_cop.cli import EXIT_ERROR, EXIT_OK, EXIT_POLICY, main
from slop_cop.findings import AnalysisState, Decision

PROJECT = Path(__file__).parents[1]
REPOSITORY = PROJECT.parents[1]
CONFIG = PROJECT / "slop-cop.toml"


def _write_override(tmp_path: Path) -> Path:
    path = tmp_path / "override.json"
    path.write_text(
        json.dumps(
            {
                "reviewer": "maintainer",
                "reason": "Reviewed manually",
                "review_id": 1,
                "review_url": "https://github.com/example/repository/pull/1#pullrequestreview-1",
                "head_sha": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_check_writes_one_run_result_to_json_and_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# A concrete implementation\n\nThe controller rejects requests outside its policy.\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "artifact"
    explicit_json = tmp_path / "explicit.json"

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(tmp_path),
            "--html-dir",
            str(artifact),
            "--json",
            str(explicit_json),
            str(note),
        ]
    )

    assert result == EXIT_OK
    assert "Slop Cop: PASS" in capsys.readouterr().out
    artifact_data = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
    explicit_data = json.loads(explicit_json.read_text(encoding="utf-8"))
    assert artifact_data == explicit_data
    assert artifact_data["files"][0]["path"] == "note.md"
    assert "A concrete implementation" in (artifact / "index.html").read_text(encoding="utf-8")


def test_check_without_paths_is_not_applicable_and_writes_reports(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--html-dir",
            str(artifact),
        ]
    )
    data = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
    assert result == EXIT_OK
    assert data["decision"] == "not_applicable"
    assert data["score"] is None


def test_override_does_not_pass_an_analysis_error(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("```text\nmasked\n```\n", encoding="utf-8")
    override = _write_override(tmp_path)
    report = tmp_path / "report.json"

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(tmp_path),
            "--head-sha",
            "a" * 40,
            "--override-json",
            str(override),
            "--json",
            str(report),
            str(note),
        ]
    )

    data = json.loads(report.read_text(encoding="utf-8"))
    assert result == EXIT_POLICY
    assert data["analysis_state"] == "error"
    assert data["decision"] == "fail"
    assert data["override"] is None


def test_override_does_not_pass_incomplete_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Concrete prose remains available for analysis.\n", encoding="utf-8")
    override = _write_override(tmp_path)
    report = tmp_path / "report.json"
    original_analyze = cli_module._analyze

    async def force_incomplete(*args: Any, **kwargs: Any) -> Any:
        output, document = await original_analyze(*args, **kwargs)
        file_result = output.file_result.model_copy(
            update={"analysis_state": AnalysisState.INCOMPLETE, "decision": Decision.FAIL}
        )
        return output.__class__(file_result, output.external_audits), document

    monkeypatch.setattr(cli_module, "_analyze", force_incomplete)
    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(tmp_path),
            "--head-sha",
            "a" * 40,
            "--override-json",
            str(override),
            "--json",
            str(report),
            str(note),
        ]
    )

    data = json.loads(report.read_text(encoding="utf-8"))
    assert result == EXIT_POLICY
    assert data["analysis_state"] == "incomplete"
    assert data["decision"] == "fail"
    assert data["override"] is None


def test_check_rejects_path_outside_repository_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Visible prose.", encoding="utf-8")

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(repository),
            str(outside),
        ]
    )
    assert result == EXIT_ERROR
    assert "outside the repository root" in capsys.readouterr().err


def test_check_rejects_symlink_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "target.md"
    target.write_text("Visible prose.", encoding="utf-8")
    link = tmp_path / "note.md"
    link.symlink_to(target)

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(tmp_path),
            str(link),
        ]
    )
    assert result == EXIT_ERROR
    assert "must not contain symlinks" in capsys.readouterr().err


def test_check_rejects_symlink_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = tmp_path / "repository"
    baseline = tmp_path / "baseline"
    repository.mkdir()
    baseline.mkdir()
    note = repository / "note.md"
    note.write_text("Visible head prose.", encoding="utf-8")
    baseline_target = tmp_path / "old.md"
    baseline_target.write_text("Visible base prose.", encoding="utf-8")
    (baseline / "note.md").symlink_to(baseline_target)

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--repository-root",
            str(repository),
            "--baseline-root",
            str(baseline),
            str(note),
        ]
    )
    assert result == EXIT_ERROR
    assert "baseline input must not contain symlinks" in capsys.readouterr().err


def test_list_and_explain_use_the_effective_registry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list-rules", "--config", str(CONFIG), "--kind", "builtin"]) == EXIT_OK
    assert "rhetoric.not-just" in capsys.readouterr().out

    assert main(["explain", "--config", str(CONFIG), "rhetoric.not-just"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "Name the broader property directly" in output
    assert '"fixed_allowance"' in output


def test_explain_unknown_rule_is_an_input_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "--config", str(CONFIG), "custom.missing"]) == EXIT_ERROR
    assert "unknown rule" in capsys.readouterr().err


def test_workflow_preserves_renamed_baselines_and_has_stdlib_error_report() -> None:
    workflow = (REPOSITORY / ".github/workflows/slop-cop.yml").read_text(encoding="utf-8")
    candidate_workflow = (REPOSITORY / ".github/workflows/slop-cop-candidate.yml").read_text(
        encoding="utf-8"
    )
    fallback = workflow.split("Create an error report after an early analysis failure", 1)[1]
    fallback = fallback.split("Add bounded annotations and job summary", 1)[0]
    assert "previous_filename" in workflow
    assert "baseline.mkdir(parents=True, exist_ok=True)" in workflow
    assert "uv run" not in fallback
    assert "from slop_cop" not in fallback
    assert "json.dumps(result" in fallback
    assert "html.escape(message)" in fallback
    assert "Test candidate implementation" not in workflow
    assert "working-directory: dev-tools/slop-cop" in candidate_workflow
    assert "slop-cop-pr-" not in candidate_workflow


def test_trusted_reporter_revalidates_override_and_uses_pr_head() -> None:
    workflow = (REPOSITORY / ".github/workflows/slop-cop-report.yml").read_text(encoding="utf-8")
    assert "github.rest.pulls.getReview" in workflow
    assert "github.rest.repos.getCollaboratorPermissionLevel" in workflow
    assert "review.commit_id !== headSha" in workflow
    assert "report.head_sha !== headSha" in workflow
    assert "report.head_sha !== run.head_sha" not in workflow
    assert "run.pull_requests.length !== 1" in workflow
    assert ".replaceAll('@', '&#64;')" in workflow
    assert "github.rest.actions.getWorkflow" in workflow
    assert "run.workflow_id !== trustedWorkflow.id" in workflow
    assert "run.path !== trustedWorkflow.path" in workflow
    assert "Override requires a completed policy failure." in workflow
