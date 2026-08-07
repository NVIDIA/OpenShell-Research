"""Contracts for normalized message blocks backed by JSON text nodes."""

from __future__ import annotations

from egress_gate.request_content import (
    JsonDocument,
    JsonEachSegment,
    JsonKeySegment,
    JsonMessageMapConfig,
    JsonMessageMapParser,
    JsonPathSegment,
    JsonSelector,
    MessageBlockKind,
    MessageRole,
)
from egress_gate.timeout import Timeout


def _selector(*segments: JsonPathSegment) -> JsonSelector:
    return JsonSelector(segments=segments)


def test_json_message_map_normalizes_roles_and_content_nodes() -> None:
    document = JsonDocument.parse(
        (
            b'{"request":{"messages":['
            b'{"role":"user","content":"hello"},'
            b'{"role":"tool","content":[{"text":"result"}]},'
            b'{"role":"future","content":"unknown"}'
            b"]}}"
        ),
        timeout=Timeout.from_seconds(1),
    )
    config = JsonMessageMapConfig(
        kind="json-message-map",
        messages=_selector(
            JsonKeySegment(kind="key", value="request"),
            JsonKeySegment(kind="key", value="messages"),
        ),
        role_key="role",
        text_selectors=(
            _selector(JsonKeySegment(kind="key", value="content")),
            _selector(
                JsonKeySegment(kind="key", value="content"),
                JsonEachSegment(kind="each"),
                JsonKeySegment(kind="key", value="text"),
            ),
        ),
    )

    messages = JsonMessageMapParser(config).parse(
        document,
        timeout=Timeout.from_seconds(1),
    )

    assert tuple(block.text for block in messages.blocks) == (
        "hello",
        "result",
        "unknown",
    )
    assert tuple(block.role for block in messages.blocks) == (
        MessageRole.USER,
        MessageRole.TOOL,
        MessageRole.UNKNOWN,
    )
    assert tuple(block.kind for block in messages.blocks) == (
        MessageBlockKind.TEXT,
        MessageBlockKind.TOOL_OUTPUT,
        MessageBlockKind.TEXT,
    )
    assert len({block.node_id for block in messages.blocks}) == 3


def test_json_message_map_can_classify_explicit_tool_input_and_output_nodes() -> None:
    document = JsonDocument.parse(
        (
            b'{"messages":['
            b'{"role":"assistant","tool_call":{"arguments":"input"}},'
            b'{"role":"tool","content":"output"}'
            b"]}"
        ),
        timeout=Timeout.from_seconds(1),
    )
    config = JsonMessageMapConfig(
        kind="json-message-map",
        messages=_selector(JsonKeySegment(kind="key", value="messages")),
        tool_input_selectors=(
            _selector(
                JsonKeySegment(kind="key", value="tool_call"),
                JsonKeySegment(kind="key", value="arguments"),
            ),
        ),
        tool_output_selectors=(_selector(JsonKeySegment(kind="key", value="content")),),
    )

    messages = JsonMessageMapParser(config).parse(
        document,
        timeout=Timeout.from_seconds(1),
    )

    assert tuple((block.kind, block.text) for block in messages.blocks) == (
        (MessageBlockKind.TOOL_INPUT, "input"),
        (MessageBlockKind.TOOL_OUTPUT, "output"),
    )
