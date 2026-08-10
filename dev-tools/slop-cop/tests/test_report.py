from __future__ import annotations

import json
from pathlib import Path

import pytest

from slop_cop.report import (
    ReportError,
    html_report,
    json_report,
    terminal_report,
    write_report_directory,
)


def sample_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "analysis_state": "complete",
        "decision": "fail",
        "score": 76,
        "threshold": 80,
        "base_sha": "b" * 40,
        "head_sha": "a" * 40,
        "tool_version": "0.1.0",
        "config_digest": "d" * 64,
        "files": [
            {
                "path": "docs/dev-notes/posts/example.md",
                "analysis_state": "complete",
                "decision": "fail",
                "score": 76,
                "threshold": 80,
                "hard_fail": False,
                "metrics": {
                    "source_bytes": 61,
                    "source_code_points": 61,
                    "analyzable_words": 40,
                    "analyzable_sentences": 1,
                    "analyzable_paragraphs": 1,
                    "masked_code_points": 5,
                },
                "category_costs": [
                    {
                        "category": "rhetoric",
                        "rule_cost": 24,
                        "density": None,
                        "cap": 24,
                        "charged_cost": 24,
                    }
                ],
                "rule_costs": [
                    {
                        "rule_id": "rhetoric.not-just",
                        "deduplicated_units": 2,
                        "allowance": 1,
                        "document_excess": 1,
                        "base_cost": 2,
                        "density": {
                            "unit": "paragraph",
                            "window": 3,
                            "allowed_units": 1,
                            "peak_units": 2,
                            "peak_excess": 1,
                            "cost": 3,
                            "window_span": {"start": 0, "end": 61},
                        },
                        "cap": 5,
                        "charged_cost": 5,
                    }
                ],
                "findings": [
                    {
                        "rule_id": "rhetoric.not-just",
                        "category": "rhetoric",
                        "severity": "warning",
                        "source_path": "docs/dev-notes/posts/example.md",
                        "span": {"start": 40, "end": 48},
                        "line": 1,
                        "column": 40,
                        "excerpt": "<img src=x onerror=alert(1)>",
                        "normalized_key": "not just",
                        "score_group": "not-but",
                        "explanation": "Formulaic contrast.",
                        "advice": "State the concrete distinction.",
                        "units": 1,
                    }
                ],
                "errors": [],
                "suppressions": [],
                "base": {
                    "score": 83,
                    "delta": -7,
                    "analysis_state": "complete",
                    "findings": {"added": [], "removed": [], "persistent": []},
                    "errors": [],
                },
            }
        ],
        "rule_errors": [],
        "external_audits": [
            {
                "rule_id": "custom.judge",
                "rule_version": 1,
                "service": "judge.example",
                "endpoint_hostname": "judge.example",
                "content_digest": "c" * 64,
                "request_schema_version": "1",
                "judge_revision": "v1",
                "attempts": 1,
                "latency_ms": 42,
                "outcome": "success",
                "response_digest": "e" * 64,
            }
        ],
    }


def test_json_report_is_stable_and_versioned() -> None:
    rendered = json_report(sample_result())
    assert rendered.endswith("\n")
    assert json.loads(rendered)["schema_version"] == 1
    assert rendered.index('"analysis_state"') < rendered.index('"decision"')


def test_terminal_report_includes_decision_score_density_and_location() -> None:
    rendered = terminal_report(sample_result())
    assert "Slop Cop: FAIL  score=76  threshold=80" in rendered
    assert "delta=-7" in rendered
    assert "category rhetoric: -24 points" in rendered
    assert "docs/dev-notes/posts/example.md:1:40 [rhetoric.not-just]" in rendered


def test_html_report_is_self_contained_and_escapes_all_result_text() -> None:
    source = "A <script>alert(1)</script> note that is not just vague."
    rendered = html_report(sample_result(), sources={"docs/dev-notes/posts/example.md": source})
    assert "Content-Security-Policy" in rendered
    assert "default-src &#x27;none&#x27;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "https://" not in rendered
    assert "External rule audit" in rendered
    assert "Passage density" in rendered


def test_report_directory_contains_canonical_pair(tmp_path: Path) -> None:
    html_path, json_path = write_report_directory(sample_result(), tmp_path / "artifact")
    assert html_path.name == "index.html"
    assert json_path.name == "report.json"
    assert json.loads(json_path.read_text())["decision"] == "fail"


def test_html_size_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("slop_cop.report.MAX_HTML_BYTES", 100)
    with pytest.raises(ReportError, match="exceeds"):
        html_report(sample_result())


def test_not_applicable_has_no_synthetic_score() -> None:
    result = {
        "schema_version": 1,
        "analysis_state": "not_applicable",
        "decision": "not_applicable",
        "score": None,
        "threshold": 80,
        "tool_version": "0.1.0",
        "config_digest": "d" * 64,
        "files": [],
    }
    rendered = html_report(result)
    assert "No changed Dev Note required analysis." in rendered
    assert "Score</dt><dd>—" in rendered
