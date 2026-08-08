"""Contracts for reusable request-content text parsers."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from egress_gate.errors import GateInputError
from egress_gate.request_content import (
    JsonEachSegment,
    JsonFieldsParser,
    JsonKeySegment,
    JsonMessageBlockExtractor,
    JsonMessageMapConfig,
    JsonSelector,
    MessageBlocksParser,
    MessageRole,
    TextReplacement,
    Utf8TextParser,
)
from egress_gate.timeout import Timeout


def _content_selector() -> JsonSelector:
    return JsonSelector(
        segments=(
            JsonKeySegment(kind="key", value="messages"),
            JsonEachSegment(kind="each"),
            JsonKeySegment(kind="key", value="content"),
        )
    )


def test_text_replacement_is_named_and_immutable() -> None:
    replacement = TextReplacement(target_id="target", text="safe")

    assert replacement.target_id == "target"
    assert replacement.text == "safe"
    with pytest.raises(FrozenInstanceError):
        setattr(replacement, "text", "changed")


def test_utf8_parser_extracts_and_replaces_the_complete_body() -> None:
    content = Utf8TextParser().parse(
        b"secret",
        timeout=Timeout.from_seconds(1),
    )

    assert tuple((target.id, target.text) for target in content.targets) == (
        ("body", "secret"),
    )
    assert (
        content.replace_text(
            (TextReplacement(target_id="body", text="safe"),),
            timeout=Timeout.from_seconds(1),
        )
        == b"safe"
    )


def test_utf8_parser_rejects_invalid_body_encoding() -> None:
    with pytest.raises(GateInputError, match="not valid UTF-8"):
        Utf8TextParser().parse(
            b"\xff",
            timeout=Timeout.from_seconds(1),
        )


def test_json_fields_parser_owns_source_preserving_replacement() -> None:
    parser = JsonFieldsParser(selectors=(_content_selector(),))
    body = b'{ "messages":[{"content":"secret"}], "number":1.00 }'

    content = parser.parse(body, timeout=Timeout.from_seconds(1))

    assert tuple(target.text for target in content.targets) == ("secret",)
    assert (
        content.replace_text(
            (TextReplacement(target_id=content.targets[0].id, text="safe"),),
            timeout=Timeout.from_seconds(1),
        )
        == b'{ "messages":[{"content":"safe"}], "number":1.00 }'
    )


def test_json_fields_parser_rejects_replacement_of_an_unselected_node() -> None:
    parser = JsonFieldsParser(selectors=(_content_selector(),))
    body = b'{"messages":[{"content":"selected"}],"ignored":"secret"}'
    content = parser.parse(body, timeout=Timeout.from_seconds(1))
    ignored_parser = JsonFieldsParser(
        selectors=(
            JsonSelector(segments=(JsonKeySegment(kind="key", value="ignored"),)),
        )
    )
    ignored = ignored_parser.parse(body, timeout=Timeout.from_seconds(1)).targets[0]

    with pytest.raises(ValueError, match="was not selected"):
        content.replace_text(
            (TextReplacement(target_id=ignored.id, text="exposed"),),
            timeout=Timeout.from_seconds(1),
        )


def test_message_blocks_parser_applies_mapping_and_filters() -> None:
    parser = MessageBlocksParser(
        extractor=JsonMessageBlockExtractor(
            JsonMessageMapConfig(
                kind="json-message-map",
                messages=JsonSelector(
                    segments=(JsonKeySegment(kind="key", value="messages"),)
                ),
                text_selectors=(
                    JsonSelector(
                        segments=(JsonKeySegment(kind="key", value="content"),)
                    ),
                ),
            )
        ),
        roles=(MessageRole.USER,),
    )

    content = parser.parse(
        (
            b'{"messages":['
            b'{"role":"assistant","content":"ignored"},'
            b'{"role":"user","content":"selected"}'
            b"]}"
        ),
        timeout=Timeout.from_seconds(1),
    )

    assert tuple(target.text for target in content.targets) == ("selected",)


def test_message_blocks_parser_deduplicates_shared_text_nodes() -> None:
    selector = JsonSelector(segments=(JsonKeySegment(kind="key", value="content"),))
    parser = MessageBlocksParser(
        extractor=JsonMessageBlockExtractor(
            JsonMessageMapConfig(
                kind="json-message-map",
                messages=JsonSelector(
                    segments=(JsonKeySegment(kind="key", value="messages"),)
                ),
                text_selectors=(selector,),
                tool_output_selectors=(selector,),
            )
        )
    )

    content = parser.parse(
        b'{"messages":[{"role":"tool","content":"once"}]}',
        timeout=Timeout.from_seconds(1),
    )

    assert tuple(target.text for target in content.targets) == ("once",)
