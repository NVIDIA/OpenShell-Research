from __future__ import annotations

import asyncio
from pathlib import Path

from slop_cop.config import load_config
from slop_cop.document import build_document
from slop_cop.engine import analyze_document
from slop_cop.findings import Decision, FileResult
from slop_cop.rules.registry import build_registry

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"


def _analyze(source: str) -> FileResult:
    config = load_config(CONFIG_PATH)
    registry = build_registry(config)
    document = build_document("docs/dev-notes/posts/test.md", source)
    return asyncio.run(analyze_document(document, registry, config)).file_result


def test_blocking_artifact_fails_independently_of_score() -> None:
    result = _analyze("As an AI language model, I cannot complete this Dev Note.")
    assert result.decision is Decision.FAIL
    assert result.hard_fail
    assert any(finding.rule_id == "artifact.ai-disclosure" for finding in result.findings)


def test_overlap_charges_one_primary_finding() -> None:
    result = _analyze(
        "This is not just a wrapper, but an enforcement boundary. "
        "It is not just configuration, but policy."
    )
    overlapping = [
        finding
        for finding in result.findings
        if finding.rule_id in {"rhetoric.not-but", "rhetoric.not-just"}
    ]
    assert sum(finding.chargeable for finding in overlapping) == 2
    assert any(finding.related_rule_ids for finding in overlapping)


def test_ignore_next_suppresses_only_the_target_block() -> None:
    source = (
        '<!-- slop-cop: ignore-next=rhetoric.not-just reason="Named API contrast" -->\n'
        "This is not just transport.\n\n"
        "This is not just storage."
    )
    result = _analyze(source)
    matches = [item for item in result.findings if item.rule_id == "rhetoric.not-just"]
    assert sum(item.suppressed for item in matches) == 1
    assert sum(not item.suppressed for item in matches) == 1


def test_dense_multi_family_fixture_fails_threshold() -> None:
    paragraph = (
        "Clients receive data now. Clients receive output later. "
        "Clients receive errors immediately. "
        "It is not just a wrapper, but a policy boundary. "
        "This applies in the realm of systems."
    )
    result = _analyze("\n\n".join(paragraph for _ in range(6)))
    assert result.score is not None and result.score < result.threshold
    assert result.decision is Decision.FAIL
