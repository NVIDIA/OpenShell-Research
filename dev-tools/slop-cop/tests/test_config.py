import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from slop_cop.config import (
    RegexFlag,
    RegexRuleConfig,
    RulePolicy,
    ServiceConfig,
    Severity,
    load_config,
)
from slop_cop.document import DocumentMetrics
from slop_cop.findings import AnalysisState, Decision, FileResult, RunResult

PROJECT_ROOT = Path(__file__).parents[1]


def test_checked_in_configuration_is_valid_and_stable() -> None:
    config = load_config(PROJECT_ROOT / "slop-cop.toml")

    assert config.schema_version == 1
    assert config.threshold == 80
    assert len(config.digest) == 64
    assert config.digest == load_config(PROJECT_ROOT / "slop-cop.toml").digest
    assert "artifact.ai-disclosure" in config.rules
    assert config.custom_rules.phrase == ()
    assert config.services == {}


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
schema_version = 1
profile = "dev-notes"
threshold = 80
paths = ["docs/dev-notes/posts/*.md"]
unexpected = true
[categories.structure]
cap = 0
[rules]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unexpected"):
        load_config(path)


def test_invalid_rule_policy_relationships_are_rejected() -> None:
    with pytest.raises(ValidationError, match="info rules"):
        RulePolicy(
            severity=Severity.INFO,
            max_signal_units=1,
            fixed_allowance=0,
            first_cost=1,
            repeat_cost=0,
            cap=1,
        )

    with pytest.raises(ValidationError, match="error rules"):
        RulePolicy(
            severity=Severity.ERROR,
            max_signal_units=1,
            fixed_allowance=0,
            first_cost=1,
            repeat_cost=0,
            cap=1,
        )


def test_service_requires_https_except_for_loopback() -> None:
    assert ServiceConfig(
        url="http://localhost:8080/judge",
        token_env="SLOP_COP_TEST_TOKEN",
    ).url.startswith("http://")
    with pytest.raises(ValidationError, match="HTTPS"):
        ServiceConfig(
            url="http://judge.example.test/v1",
            token_env="SLOP_COP_TEST_TOKEN",
        )


def _regex_rule(
    pattern: str,
    *,
    flags: tuple[RegexFlag, ...] = (),
) -> RegexRuleConfig:
    return RegexRuleConfig(
        id="custom.regex-test",
        version=1,
        category="rhetoric",
        severity=Severity.WARNING,
        title="Regex test",
        rationale="Finds a configured pattern.",
        advice="Revise the matching prose.",
        max_signal_units=1,
        fixed_allowance=0,
        first_cost=1,
        repeat_cost=1,
        cap=3,
        pattern=pattern,
        flags=flags,
    )


def test_regex_rule_rejects_unsafe_constructs() -> None:
    with pytest.raises(ValidationError, match="unsupported construct"):
        _regex_rule(r"(word)\1")
    assert _regex_rule(r"\bword\b", flags=(RegexFlag.IGNORECASE,)).pattern


def test_run_result_round_trips_through_versioned_json() -> None:
    config = load_config(PROJECT_ROOT / "slop-cop.toml")
    metrics = DocumentMetrics(
        source_bytes=100,
        source_code_points=100,
        analyzable_words=12,
        analyzable_sentences=2,
        analyzable_paragraphs=1,
        masked_code_points=0,
    )
    file_result = FileResult(
        path="docs/dev-notes/posts/note.md",
        analysis_state=AnalysisState.COMPLETE,
        decision=Decision.PASS,
        score=95,
        threshold=80,
        metrics=metrics,
    )
    result = RunResult(
        analysis_state=AnalysisState.COMPLETE,
        decision=Decision.PASS,
        score=95,
        threshold=80,
        repository="NVIDIA/OpenShell-Research",
        head_sha="a" * 40,
        tool_version="0.1.0",
        config_digest=config.digest,
        files=(file_result,),
    )

    restored = RunResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.schema_version == 1


def test_result_models_reject_unknown_fields_and_inconsistent_minimum_score() -> None:
    payload = {
        "schema_version": 1,
        "analysis_state": "complete",
        "decision": "pass",
        "score": 99,
        "threshold": 80,
        "tool_version": "0.1.0",
        "config_digest": "0" * 64,
        "files": [
            {
                "path": "docs/dev-notes/posts/note.md",
                "analysis_state": "complete",
                "decision": "pass",
                "score": 95,
                "threshold": 80,
                "metrics": {
                    "source_bytes": 1,
                    "source_code_points": 1,
                    "analyzable_words": 1,
                    "analyzable_sentences": 1,
                    "analyzable_paragraphs": 1,
                    "masked_code_points": 0,
                },
            }
        ],
    }
    with pytest.raises(ValidationError, match="minimum"):
        RunResult.model_validate_json(json.dumps(payload))

    payload["score"] = 95
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="unexpected"):
        RunResult.model_validate_json(json.dumps(payload))


def test_analysis_error_can_fail_without_manufacturing_a_score() -> None:
    metrics = DocumentMetrics(
        source_bytes=1,
        source_code_points=1,
        analyzable_words=0,
        analyzable_sentences=0,
        analyzable_paragraphs=0,
        masked_code_points=0,
    )
    file_result = FileResult(
        path="docs/dev-notes/posts/note.md",
        analysis_state=AnalysisState.ERROR,
        decision=Decision.FAIL,
        score=None,
        threshold=80,
        metrics=metrics,
    )

    result = RunResult(
        analysis_state=AnalysisState.ERROR,
        decision=Decision.FAIL,
        score=None,
        threshold=80,
        tool_version="0.1.0",
        config_digest="0" * 64,
        files=(file_result,),
    )

    assert result.score is None
