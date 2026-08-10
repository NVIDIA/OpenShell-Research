from pathlib import Path

import pytest

from slop_cop.config import ContextConfig
from slop_cop.document import ProjectionError, build_document, load_document

FIXTURES = Path(__file__).parent / "fixtures" / "markdown"
REPOSITORY_ROOT = Path(__file__).parents[3]


def test_projection_masks_ignored_contexts_and_preserves_visible_prose() -> None:
    document = load_document(FIXTURES / "all-contexts.md")

    assert len(document.prose_projection) == len(document.source)
    assert [index for index, char in enumerate(document.source) if char in "\r\n"] == [
        index for index, char in enumerate(document.prose_projection) if char in "\r\n"
    ]
    assert "Visible prose is not just readable" in document.prose_projection
    assert "Visible label" in document.prose_projection
    assert "Visible caption text" in document.prose_projection
    assert "Hidden generated" not in document.prose_projection
    assert "inline not just code" not in document.prose_projection
    assert "not just fenced code" not in document.prose_projection
    assert "example.test" not in document.prose_projection
    assert "hidden not just alt text" not in document.prose_projection
    assert "Quoted not just prose" not in document.prose_projection


def test_blockquotes_can_be_scanned_explicitly() -> None:
    source = "> Quoted prose.\n\nVisible prose.\n"

    ignored = build_document("note.md", source)
    scanned = build_document("note.md", source, contexts=ContextConfig(scan_blockquotes=True))

    assert "Quoted prose" not in ignored.prose_projection
    assert "Quoted prose" in scanned.prose_projection


def test_indented_code_and_lazy_blockquote_continuations_are_masked() -> None:
    source = (
        "    As an AI language model, this is sample output.\n\n"
        "> Quoted first line\n"
        "As an AI language model, this is a lazy continuation.\n\n"
        "Visible prose.\n"
    )

    document = build_document("note.md", source)

    assert "AI language model" not in document.prose_projection
    assert "Visible prose" in document.prose_projection
    assert any("indented-code" in item.reasons for item in document.masked_ranges)
    assert any("blockquote" in item.reasons for item in document.masked_ranges)


@pytest.mark.parametrize(
    "source",
    [
        "> # Quoted heading\nAs an AI language model, this is outside prose.\n",
        "> Quoted paragraph.\n# As an AI language model, this heading is outside.\n",
        "> - Quoted list item\nAs an AI language model, this is outside prose.\n",
        "> Quoted paragraph.\n---\nAs an AI language model, this is outside prose.\n",
    ],
)
def test_completed_blockquotes_do_not_mask_following_blocks(source: str) -> None:
    document = build_document("note.md", source)

    assert "As an AI language model" in document.prose_projection


def test_segments_retain_exact_source_offsets_and_unicode_keys() -> None:
    source = "# Heading\n\nCafé’s source-aware token. Next sentence!\n"
    document = build_document("note.md", source)

    cafe = next(token for token in document.tokens if token.text.startswith("Café"))
    assert document.source[cafe.start : cafe.end] == "Café’s"
    assert cafe.normalized == "café’s"
    assert document.line_column(cafe.start) == (3, 1)
    assert len(document.sentences) == 3
    assert len(document.paragraphs) == 2


def test_suppression_binds_to_the_next_prose_block() -> None:
    source = (
        '<!-- slop-cop: ignore-next=rhetoric.not-just reason="Named API contrast" -->\n\n'
        "This is not just a wrapper.\n\nLater prose.\n"
    )
    document = build_document("note.md", source)

    assert len(document.suppressions) == 1
    suppression = document.suppressions[0]
    assert suppression.rule_ids == ("rhetoric.not-just",)
    assert document.source[suppression.target_span.start : suppression.target_span.end].startswith(
        "This is not just"
    )
    assert "slop-cop" not in document.prose_projection


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("---\ntitle: Missing end\n", "unterminated YAML"),
        ("```python\npass\n", "unterminated fenced"),
        ("Text with `unfinished code.\n", "unterminated inline"),
        ("<!-- unfinished\n", "unterminated HTML"),
        ("<!-- slop-cop: ignore-next=rhetoric.not-just -->\nText.\n", "invalid Slop Cop"),
        (
            '<!-- slop-cop: ignore-next=rhetoric.not-just reason="No target" -->\n',
            "no following prose",
        ),
    ],
)
def test_malformed_masked_constructs_are_errors(source: str, message: str) -> None:
    with pytest.raises(ProjectionError, match=message):
        build_document("note.md", source)


def test_invalid_utf8_nul_and_size_are_errors() -> None:
    with pytest.raises(ProjectionError, match="valid UTF-8"):
        build_document("note.md", b"\xff")
    with pytest.raises(ProjectionError, match="NUL"):
        build_document("note.md", "before\0after")
    with pytest.raises(ProjectionError, match="exceeds 5 bytes"):
        build_document("note.md", b"123456", max_source_bytes=5)


def test_masked_ranges_are_sorted_and_non_overlapping() -> None:
    document = build_document("note.md", "[label](https://example.test/`x`) and `code`.\n")

    assert all(
        left.end <= right.start
        for left, right in zip(document.masked_ranges, document.masked_ranges[1:], strict=False)
    )
    for masked in document.masked_ranges:
        assert all(
            projected == original if original in "\r\n" else projected == " "
            for original, projected in zip(
                document.source[masked.start : masked.end],
                document.prose_projection[masked.start : masked.end],
                strict=True,
            )
        )


def test_current_dev_note_projects_without_ambiguous_ranges() -> None:
    note = REPOSITORY_ROOT / (
        "docs/dev-notes/posts/2026-07-20-policy-controlling-reachy-mini-with-openshell.md"
    )

    document = load_document(note)

    assert document.metrics.analyzable_words > 500
    assert any("front-matter" in item.reasons for item in document.masked_ranges)
    assert any("generated-byline" in item.reasons for item in document.masked_ranges)
    assert "Bringing Privacy and Security to the Edge" in document.prose_projection
