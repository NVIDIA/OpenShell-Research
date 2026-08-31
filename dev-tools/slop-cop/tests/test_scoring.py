from __future__ import annotations

from pathlib import Path

from slop_cop.config import Severity, load_config
from slop_cop.document import Document, Span, build_document
from slop_cop.findings import Finding
from slop_cop.rules.registry import build_registry
from slop_cop.scoring import score_findings

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"


def _finding(document: Document, start: int) -> Finding:
    line, column = document.line_column(start)
    return Finding(
        rule_id="rhetoric.not-just",
        category="rhetoric",
        severity=Severity.WARNING,
        source_path=document.path,
        span=Span(start=start, end=start + 8),
        line=line,
        column=column,
        excerpt="not just",
        normalized_key="not just",
        score_group="contrast",
        explanation="The contrast repeats.",
        advice="State the distinction directly.",
    )


def test_dense_findings_cost_more_than_spread_findings() -> None:
    config = load_config(CONFIG_PATH)
    registry = build_registry(config)
    dense_source = "not just one. not just two.\n\nA clean paragraph.\n\nAnother clean paragraph."
    dense = build_document("dense.md", dense_source)
    dense_findings = tuple(_finding(dense, offset) for offset in (0, 14))
    dense_result = score_findings(dense, dense_findings, registry, config)

    spread_source = "not just one.\n\nClean one.\n\nClean two.\n\nClean three.\n\nnot just two."
    spread = build_document("spread.md", spread_source)
    spread_findings = tuple(
        _finding(spread, offset) for offset in (0, spread_source.rfind("not just"))
    )
    spread_result = score_findings(spread, spread_findings, registry, config)
    assert dense_result.score < spread_result.score


def test_appending_clean_prose_does_not_remove_peak_density_cost() -> None:
    config = load_config(CONFIG_PATH)
    registry = build_registry(config)
    prefix = "not just one. not just two."
    short = build_document("short.md", prefix)
    long = build_document("long.md", prefix + "\n\n" + "A direct statement.\n\n" * 20)
    short_score = score_findings(short, (_finding(short, 0), _finding(short, 14)), registry, config)
    long_score = score_findings(long, (_finding(long, 0), _finding(long, 14)), registry, config)
    short_cost = next(
        item for item in short_score.rule_costs if item.rule_id == "rhetoric.not-just"
    )
    long_cost = next(item for item in long_score.rule_costs if item.rule_id == "rhetoric.not-just")
    assert short_cost.density is not None and long_cost.density is not None
    assert short_cost.density.cost == long_cost.density.cost


def test_repetition_rules_have_explicit_density_policy() -> None:
    config = load_config(CONFIG_PATH)
    for rule_id in (
        "repetition.ngram",
        "repetition.sentence-opener",
        "repetition.template-shape",
        "repetition.emphatic-fragments",
    ):
        assert config.rules[rule_id].density is not None
    assert config.categories["repetition"].density is not None
