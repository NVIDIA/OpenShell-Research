from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import pytest

from slop_cop.config import ContextConfig, load_config
from slop_cop.document import build_document
from slop_cop.rules.api import RuleContext, RuleSignal
from slop_cop.rules.registry import build_registry

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"
CASE_PATH = Path(__file__).with_name("rule_cases.toml")
RULE_CASES = tomllib.loads(CASE_PATH.read_text(encoding="utf-8"))["case"]


def _signals(
    rule_id: str, source: str, *, contexts: ContextConfig | None = None
) -> tuple[RuleSignal, ...]:
    config = load_config(CONFIG_PATH)
    rule = build_registry(config).by_id(rule_id).rule
    document = build_document("note.md", source, contexts=contexts)
    return asyncio.run(rule.evaluate(RuleContext(document), object())).signals


@pytest.mark.parametrize("case", RULE_CASES, ids=lambda case: str(case["name"]))
def test_rule_case(case: dict[str, object]) -> None:
    rule_id = str(case["rule_id"])
    source = str(case["source"])
    signals = _signals(rule_id, source)
    if case["kind"] == "positive":
        assert signals, f"{rule_id} did not emit a signal"
    else:
        assert not signals, f"{rule_id} emitted an unexpected signal"
    if "expected_spans" in case:
        expected_raw = case["expected_spans"]
        assert isinstance(expected_raw, list)
        expected: list[tuple[int, int]] = []
        for span in expected_raw:
            assert isinstance(span, list) and len(span) == 2
            assert isinstance(span[0], int) and isinstance(span[1], int)
            expected.append((span[0], span[1]))
        actual = [(signal.start, signal.end) for signal in signals]
        assert actual == expected


def test_artifact_rule_ignores_inline_code() -> None:
    assert not _signals("artifact.ai-disclosure", "Use `as an AI language model` as test input.")
    assert _signals("artifact.ai-disclosure", "As an AI language model, I cannot verify that.")


def test_artifact_rule_ignores_indented_code_and_lazy_blockquotes() -> None:
    assert not _signals(
        "artifact.ai-disclosure",
        "    As an AI language model, I cannot verify that.\n",
    )
    assert not _signals(
        "artifact.ai-disclosure",
        "> Example response:\nAs an AI language model, I cannot verify that.\n",
    )


def test_artifact_rule_ignores_code_inside_scanned_blockquotes() -> None:
    source = (
        "> Visible quoted prose.\n\n> ```text\n> As an AI language model, sample output.\n> ```\n"
    )

    assert not _signals(
        "artifact.ai-disclosure",
        source,
        contexts=ContextConfig(scan_blockquotes=True),
    )


def test_not_just_uses_word_boundaries() -> None:
    signals = _signals("rhetoric.not-just", "This is not just a wrapper.")
    assert len(signals) == 1
    assert signals[0].start == 8
    assert not _signals("rhetoric.not-just", "The value is not-justified by that result.")


def test_repeated_ngram_requires_three_paragraphs() -> None:
    source = (
        "The service returns one stable result here.\n\n"
        "We expect one stable result here after retry.\n\n"
        "Clients receive one stable result here every time."
    )
    signals = _signals("repetition.ngram", source)
    assert [signal.key for signal in signals].count("one stable result here") == 3


def test_repeated_ngram_ignores_headings_links_and_captions() -> None:
    source = (
        "# One stable result here\n\n"
        "A [one stable result here](https://example.test/a) appears in a citation.\n\n"
        "<figcaption>One stable result here</figcaption>\n\n"
        "Only one stable result here remains in body prose.\n"
    )

    assert not _signals("repetition.ngram", source)


def test_emphatic_fragments_ignore_numbered_list_markers() -> None:
    source = "1. Build the project.\n2. Run the tests.\n3. Review the report."
    assert not _signals("repetition.emphatic-fragments", source)


def test_horizontal_rules_ignore_front_matter_delimiters() -> None:
    source = "---\ntitle: Example\ndescription: Direct note.\n---\n\nVisible prose."
    assert not _signals("structure.horizontal-rules", source)


def test_duplicate_title_compares_front_matter_with_h1() -> None:
    source = "---\ntitle: Example\n---\n\n# Example\n\nVisible prose."
    signals = _signals("structure.duplicate-title", source)
    assert [(signal.start, signal.end) for signal in signals] == [(24, 33)]


def test_horizontal_rule_spans_do_not_include_adjacent_html() -> None:
    source = '<img alt="Descriptive text" src="chart.png">\n\n---\n\n---\n\n---\n\n---\n'

    signals = _signals("structure.horizontal-rules", source)

    assert [source[signal.start : signal.end] for signal in signals] == ["---"] * 4


def test_vague_authority_requires_an_attribution_verb() -> None:
    assert _signals("attribution.vague-authority", "Experts say the cache is unsafe.")
    assert not _signals("attribution.vague-authority", "Experts configured the cache.")
