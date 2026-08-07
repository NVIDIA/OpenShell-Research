"""Contracts for strict JSON selection and source-preserving replacement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from egress_gate.errors import BodyFormatError, GateInputError, GateLimitExceededError
from egress_gate.request_content import (
    JsonDocument,
    JsonEachSegment,
    JsonIndexSegment,
    JsonKeySegment,
    JsonPathSegment,
    JsonSelector,
)
from egress_gate.timeout import Timeout


def _selector(*segments: JsonPathSegment) -> JsonSelector:
    return JsonSelector(segments=segments)


def _parse(body: bytes) -> JsonDocument:
    return JsonDocument.parse(body, timeout=Timeout.from_seconds(1))


def test_select_text_uses_typed_paths_and_deduplicates_overlapping_selectors() -> None:
    document = _parse(
        b'{"messages":[{"content":"first"},{"content":"second"}],"ignored":"x"}'
    )
    all_content = _selector(
        JsonKeySegment(kind="key", value="messages"),
        JsonEachSegment(kind="each"),
        JsonKeySegment(kind="key", value="content"),
    )
    second_content = _selector(
        JsonKeySegment(kind="key", value="messages"),
        JsonIndexSegment(kind="index", value=1),
        JsonKeySegment(kind="key", value="content"),
    )

    nodes = document.select_text(
        (all_content, second_content),
        timeout=Timeout.from_seconds(1),
    )

    assert tuple(node.text for node in nodes) == ("first", "second")
    assert tuple(node.path for node in nodes) == (
        ("messages", 0, "content"),
        ("messages", 1, "content"),
    )
    assert len({node.id for node in nodes}) == 2


def test_missing_paths_and_non_string_terminals_produce_no_text_nodes() -> None:
    document = _parse(b'{"message":{"count":2}}')

    nodes = document.select_text(
        (
            _selector(JsonKeySegment(kind="key", value="missing")),
            _selector(
                JsonKeySegment(kind="key", value="message"),
                JsonKeySegment(kind="key", value="count"),
            ),
        ),
        timeout=Timeout.from_seconds(1),
    )

    assert nodes == ()


def test_replace_text_preserves_every_unselected_source_byte() -> None:
    body = (
        b'{  "messages" : [ {"content":"secret\\nvalue", "count":1.00} ], '
        b'"unchanged":"\\u0078" }'
    )
    document = _parse(body)
    selector = _selector(
        JsonKeySegment(kind="key", value="messages"),
        JsonEachSegment(kind="each"),
        JsonKeySegment(kind="key", value="content"),
    )
    node = document.select_text((selector,), timeout=Timeout.from_seconds(1))[0]

    replacement = document.replace_text(
        ((node.id, 'safe\n"value"'),),
        timeout=Timeout.from_seconds(1),
    )

    assert replacement == (
        b'{  "messages" : [ {"content":"safe\\n\\"value\\"", "count":1.00} ], '
        b'"unchanged":"\\u0078" }'
    )


def test_replace_text_rejects_duplicate_or_unknown_node_ids() -> None:
    document = _parse(b'{"value":"one"}')
    node = document.select_text(
        (_selector(JsonKeySegment(kind="key", value="value")),),
        timeout=Timeout.from_seconds(1),
    )[0]

    with pytest.raises(ValueError, match="unique"):
        document.replace_text(
            ((node.id, "two"), (node.id, "three")),
            timeout=Timeout.from_seconds(1),
        )
    with pytest.raises(ValueError, match="unknown"):
        document.replace_text(
            (("unknown", "two"),),
            timeout=Timeout.from_seconds(1),
        )


def test_replace_text_handles_the_maximum_selected_nodes_in_linear_time() -> None:
    from egress_gate.constants import MAX_JSON_SELECTED_NODES

    values = ",".join(
        f'"{index}":"{"x" * 900}"' for index in range(MAX_JSON_SELECTED_NODES)
    )
    document = _parse(f"{{{values}}}".encode())
    nodes = document.select_text(
        (_selector(JsonEachSegment(kind="each")),),
        timeout=Timeout.from_seconds(1),
    )

    replaced = document.replace_text(
        tuple((node.id, "safe") for node in nodes),
        timeout=Timeout.from_seconds(1),
    )

    assert replaced.count(b'"safe"') == MAX_JSON_SELECTED_NODES


def test_node_references_are_bound_to_the_document_that_created_them() -> None:
    first = _parse(b'{"value":"one"}')
    second = _parse(b'{"value":"one"}')
    selector = _selector(JsonKeySegment(kind="key", value="value"))
    first_node = first.select_nodes(
        (selector,),
        timeout=Timeout.from_seconds(1),
    )[0]

    with pytest.raises(ValueError, match="does not belong"):
        second.select_from(
            first_node,
            (selector,),
            timeout=Timeout.from_seconds(1),
        )


def test_long_strings_and_whitespace_check_the_shared_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = 0

    def record_check(_timeout: Timeout) -> None:
        nonlocal checks
        checks += 1

    monkeypatch.setattr(Timeout, "raise_if_expired", record_check)

    _parse((" " * 10_000 + '"' + "x" * 10_000 + '"').encode())

    assert checks >= 7


def test_array_item_materialization_checks_the_shared_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _parse(
        ('{"items":[' + ",".join("0" for _ in range(1000)) + "]}").encode()
    )
    root = document.select_nodes(
        (_selector(JsonKeySegment(kind="key", value="items")),),
        timeout=Timeout.from_seconds(1),
    )[0]
    checks = 0

    def record_check(_timeout: Timeout) -> None:
        nonlocal checks
        checks += 1

    monkeypatch.setattr(Timeout, "raise_if_expired", record_check)

    document.array_items(root, timeout=Timeout.from_seconds(1))

    assert checks >= 4


@pytest.mark.parametrize(
    "body",
    [
        b'{"duplicate":1,"duplicate":2}',
        b'{"constant":NaN}',
        b'{"trailing":true,}',
        b'"\\ud800"',
    ],
)
def test_parse_rejects_non_strict_json(body: bytes) -> None:
    with pytest.raises(BodyFormatError):
        _parse(body)


def test_parse_distinguishes_invalid_utf8_from_invalid_json() -> None:
    with pytest.raises(GateInputError):
        _parse(b'"\xff"')


def test_selector_models_are_strict_bounded_and_discriminated() -> None:
    with pytest.raises(ValidationError):
        JsonSelector.model_validate({"segments": []})
    with pytest.raises(ValidationError):
        JsonSelector.model_validate({"segments": [{"kind": "unknown"}]})
    with pytest.raises(ValidationError):
        JsonIndexSegment.model_validate({"kind": "index", "value": -1})
    with pytest.raises(ValidationError):
        JsonKeySegment.model_validate({"kind": "key", "value": 1})


def test_document_depth_is_bounded() -> None:
    from egress_gate.constants import MAX_JSON_DEPTH

    body = ("[" * (MAX_JSON_DEPTH + 1) + "0" + "]" * (MAX_JSON_DEPTH + 1)).encode()

    with pytest.raises(GateLimitExceededError):
        _parse(body)
