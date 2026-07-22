import json
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

import privacy_guard.request_body.json as json_module
from privacy_guard.config import PolicyConfig
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_body import (
    JsonHandler,
    RequestBody,
    TextBlock,
    select_format_handler,
)


def _assert_safe_error(
    error: PrivacyGuardError, expected_code: ErrorCode, sentinel: str
) -> None:
    assert error.code is expected_code
    assert error.__cause__ is None
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert all(sentinel not in str(argument) for argument in error.args)


def _json_state_value(request_body: RequestBody) -> object:
    assert type(request_body.parsed_value) is json_module._JsonBodyState
    return request_body.parsed_value.value


def test_body_domain_reprs_hide_sensitive_content() -> None:
    sensitive_text = "sensitive-value-8472"
    text_block = TextBlock(path="/message", text=sensitive_text)
    request_body = RequestBody(
        text_blocks=(text_block,),
        parsed_value={"message": sensitive_text},
        original_bytes=sensitive_text.encode(),
    )

    assert sensitive_text not in repr(text_block)
    assert sensitive_text not in repr(request_body)


def test_body_domain_reprs_hide_sensitive_json_pointer_tokens() -> None:
    sensitive_key = "secret-person@example.com"
    text_block = TextBlock(path=f"/{sensitive_key}", text="safe")
    request_body = RequestBody(
        text_blocks=(text_block,), parsed_value={}, original_bytes=b"{}"
    )

    assert sensitive_key not in repr(text_block)
    assert sensitive_key not in repr(request_body)


def test_json_body_state_repr_hides_recursive_value() -> None:
    sensitive_text = "distinctive-state-value-8472"
    request_body = JsonHandler().normalize(
        f'{{"message":"{sensitive_text}"}}'.encode(), PolicyConfig()
    )

    assert type(request_body.parsed_value) is json_module._JsonBodyState
    assert sensitive_text not in repr(request_body.parsed_value)


def test_normalized_json_state_stores_the_validated_json_value_directly() -> None:
    request_body = JsonHandler().normalize(
        b'{"items":[{"message":"original"}]}', PolicyConfig()
    )

    assert type(request_body.parsed_value) is json_module._JsonBodyState
    assert type(request_body.parsed_value.value) is dict
    with pytest.raises(FrozenInstanceError):
        setattr(request_body.parsed_value, "value", None)
    assert type(request_body.parsed_value.value["items"]) is list


@pytest.mark.parametrize(
    "invalid_value",
    [
        b"bytes-are-not-json-strings",
        ("tuples", "are", "not", "arrays"),
        {1: "object keys must be strings"},
        {"nested": object()},
        float("inf"),
    ],
)
def test_json_value_adapter_rejects_non_json_types_strictly(
    invalid_value: object,
) -> None:
    with pytest.raises(ValidationError):
        json_module._JSON_VALUE_ADAPTER.validate_python(invalid_value, strict=True)


@pytest.mark.parametrize(
    ("raw_body", "expected_value"),
    [
        (b"null", None),
        (b"true", True),
        (b"42", 42),
        (b"3.5", 3.5),
        (b'"root"', "root"),
        (b"[]", []),
        (b"{}", {}),
        (
            b'{"nested":[null,false,1,2.5,"text"]}',
            {"nested": [None, False, 1, 2.5, "text"]},
        ),
    ],
)
def test_normalize_stores_strict_recursive_json_roots(
    raw_body: bytes, expected_value: object
) -> None:
    request_body = JsonHandler().normalize(raw_body, PolicyConfig())

    assert type(request_body.parsed_value) is json_module._JsonBodyState
    assert _json_state_value(request_body) == expected_value
    if not isinstance(expected_value, dict | list):
        assert type(_json_state_value(request_body)) is type(expected_value)


def test_normalize_flat_object_collects_string_leaves() -> None:
    raw_body = b'{"message":"hello","model":"model-a","count":2}'

    request_body = JsonHandler().normalize(raw_body, PolicyConfig())

    assert request_body.original_bytes is raw_body
    assert type(request_body.parsed_value) is json_module._JsonBodyState
    assert _json_state_value(request_body) == {
        "message": "hello",
        "model": "model-a",
        "count": 2,
    }
    assert tuple(block for block in request_body.text_blocks if block.replaceable) == (
        TextBlock(path="/message", text="hello"),
        TextBlock(path="/model", text="model-a"),
    )


@pytest.mark.parametrize(
    ("raw_body", "expected_text_blocks"),
    [
        (
            b'{"outer":{"inner":"value"}}',
            (TextBlock(path="/outer/inner", text="value"),),
        ),
        (
            b'["first",["nested",3],{"last":"value"}]',
            (
                TextBlock(path="/0", text="first"),
                TextBlock(path="/1/0", text="nested"),
                TextBlock(path="/2/last", text="value"),
            ),
        ),
        (
            b'[null,true,false,3.5,"only-string"]',
            (TextBlock(path="/4", text="only-string"),),
        ),
        (b'"root string"', (TextBlock(path="", text="root string"),)),
        (b"{}", ()),
        (b"[]", ()),
        (
            b'{"z":"first","a":{"y":"second","x":"third"},"m":"fourth"}',
            (
                TextBlock(path="/z", text="first"),
                TextBlock(path="/a/y", text="second"),
                TextBlock(path="/a/x", text="third"),
                TextBlock(path="/m", text="fourth"),
            ),
        ),
        (
            b'{"a/b":"slash","til~de":"tilde","both~/":"both"}',
            (
                TextBlock(path="/a~1b", text="slash"),
                TextBlock(path="/til~0de", text="tilde"),
                TextBlock(path="/both~0~1", text="both"),
            ),
        ),
        (
            '{"greeting":"こんにちは","emoji":"🐍"}'.encode(),
            (
                TextBlock(path="/greeting", text="こんにちは"),
                TextBlock(path="/emoji", text="🐍"),
            ),
        ),
        (
            b'{"escaped_emoji":"\\ud83d\\udc0d"}',
            (TextBlock(path="/escaped_emoji", text="🐍"),),
        ),
    ],
)
def test_normalize_walks_arbitrary_json_in_stable_depth_first_order(
    raw_body: bytes, expected_text_blocks: tuple[TextBlock, ...]
) -> None:
    request_body = JsonHandler().normalize(raw_body, PolicyConfig())

    assert (
        tuple(block for block in request_body.text_blocks if block.replaceable)
        == expected_text_blocks
    )


def test_normalize_exposes_object_keys_as_nonreplaceable_blocks() -> None:
    blocks = (
        JsonHandler()
        .normalize(b'{"user@example.com":"safe"}', PolicyConfig())
        .text_blocks
    )

    assert blocks == (
        TextBlock(
            path="#key:/user@example.com",
            text="user@example.com",
            replaceable=False,
        ),
        TextBlock(path="/user@example.com", text="safe"),
    )


def test_normalize_rejects_invalid_utf8() -> None:
    sensitive_text = "distinctive-sensitive-utf8-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(
            f'"{sensitive_text}'.encode() + b'\xff"', PolicyConfig()
        )

    _assert_safe_error(
        exception_info.value, ErrorCode.BODY_ENCODING_INVALID, sensitive_text
    )


def test_normalize_rejects_invalid_json() -> None:
    sensitive_text = "distinctive-sensitive-json-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(
            f'{{"message":"{sensitive_text}",}}'.encode(), PolicyConfig()
        )

    _assert_safe_error(
        exception_info.value, ErrorCode.BODY_JSON_INVALID, sensitive_text
    )


def test_normalize_shape_failure_drops_sensitive_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.request_body.json as json_module

    sensitive_text = "distinctive-sensitive-shape-8472"
    monkeypatch.setattr(json_module, "MAX_SCANNED_CHARACTERS", len(sensitive_text) - 1)

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(f'"{sensitive_text}"'.encode(), PolicyConfig())

    _assert_safe_error(
        exception_info.value,
        ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED,
        sensitive_text,
    )


@pytest.mark.parametrize("non_finite_value", [b"NaN", b"Infinity", b"-Infinity"])
def test_normalize_rejects_non_standard_numeric_constants(
    non_finite_value: bytes,
) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(b'{"number":' + non_finite_value + b"}", PolicyConfig())

    assert exception_info.value.code is ErrorCode.BODY_JSON_INVALID


def test_normalize_rejects_finite_syntax_that_overflows_to_infinity() -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(b'{"number":1e400}', PolicyConfig())

    assert exception_info.value.code is ErrorCode.BODY_JSON_INVALID


def test_normalize_maps_python_huge_integer_limit_to_safe_json_error() -> None:
    digit_limit = sys.int_info.default_max_str_digits
    raw_body = b'{"number":' + (b"9" * (digit_limit + 1)) + b"}"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(raw_body, PolicyConfig())

    assert exception_info.value.code is ErrorCode.BODY_JSON_INVALID
    assert "999999" not in str(exception_info.value)


def test_normalize_rejects_duplicate_object_members_without_leaking_content() -> None:
    sensitive_text = "distinctive-duplicate-value-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(
            (f'{{"message":"{sensitive_text}","message":"benign"}}').encode(),
            PolicyConfig(),
        )

    assert exception_info.value.code is ErrorCode.BODY_JSON_INVALID
    assert sensitive_text not in str(exception_info.value)
    assert sensitive_text not in repr(exception_info.value)


def test_normalize_rejects_unpaired_surrogates_without_leaking_content() -> None:
    sensitive_text = "distinctive-surrogate-neighbor-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(
            f'{{"message":"{sensitive_text}","invalid":"\\ud800"}}'.encode(),
            PolicyConfig(),
        )

    _assert_safe_error(
        exception_info.value,
        ErrorCode.BODY_JSON_INVALID,
        sensitive_text,
    )


def test_normalize_rejects_unpaired_surrogate_object_key_without_leaking_content() -> (
    None
):
    sensitive_text = "distinctive-surrogate-key-neighbor-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(
            f'{{"{sensitive_text}":"safe","\\ud800":"invalid"}}'.encode(),
            PolicyConfig(),
        )

    _assert_safe_error(
        exception_info.value,
        ErrorCode.BODY_JSON_INVALID,
        sensitive_text,
    )


def test_reconstruct_without_replacements_returns_exact_original_bytes() -> None:
    raw_body = b'{\n  "message": "hello", "escaped": "\\u263a"\n}\n  '
    json_handler = JsonHandler()
    request_body = json_handler.normalize(raw_body, PolicyConfig())

    assert json_handler.reconstruct(request_body, {}) is raw_body


def test_reconstruct_replaces_one_string_leaf() -> None:
    json_handler = JsonHandler()
    request_body = json_handler.normalize(
        b'{"message":"hello","count":2}', PolicyConfig()
    )

    reconstructed_body = json_handler.reconstruct(request_body, {"/message": "goodbye"})

    assert json.loads(reconstructed_body) == {"message": "goodbye", "count": 2}
    assert reconstructed_body == b'{"message":"goodbye","count":2}'


def test_reconstruct_with_replacements_deepcopies_state_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copy_count = 0

    def counting_deepcopy(value: object) -> object:
        nonlocal copy_count
        copy_count += 1
        return deepcopy(value)

    json_handler = JsonHandler()
    request_body = json_handler.normalize(
        b'{"first":"one","nested":{"second":"two"}}', PolicyConfig()
    )
    monkeypatch.setattr(json_module, "deepcopy", counting_deepcopy)

    reconstructed_body = json_handler.reconstruct(
        request_body, {"/first": "ONE", "/nested/second": "TWO"}
    )

    assert json.loads(reconstructed_body) == {
        "first": "ONE",
        "nested": {"second": "TWO"},
    }
    assert copy_count == 1


@pytest.mark.parametrize(
    ("raw_body", "replacements_by_path", "expected_value"),
    [
        (
            b'{"first":"one","nested":{"second":"two"}}',
            {"/first": "ONE", "/nested/second": "TWO"},
            {"first": "ONE", "nested": {"second": "TWO"}},
        ),
        (
            b'{"message":"hello","other":"keep"}',
            {"/message": ""},
            {"message": "", "other": "keep"},
        ),
        (b'"root"', {"": "replaced"}, "replaced"),
        (
            b'{"a/b":"slash","til~de":"tilde"}',
            {"/a~1b": "new slash", "/til~0de": "new tilde"},
            {"a/b": "new slash", "til~de": "new tilde"},
        ),
        (
            '{"message":"こんにちは"}'.encode(),
            {"/message": "さようなら"},
            {"message": "さようなら"},
        ),
    ],
)
def test_reconstruct_applies_explicit_string_replacements(
    raw_body: bytes,
    replacements_by_path: dict[str, str],
    expected_value: object,
) -> None:
    json_handler = JsonHandler()
    request_body = json_handler.normalize(raw_body, PolicyConfig())

    reconstructed_body = json_handler.reconstruct(request_body, replacements_by_path)

    assert json.loads(reconstructed_body) == expected_value
    assert b"\\u3055" not in reconstructed_body


@pytest.mark.parametrize(
    ("raw_body", "json_pointer"),
    [
        (b'{"message":"secret-8472"}', "/unknown"),
        (b'{"message":"secret-8472"}', "message"),
        (b'{"~bad":"secret-8472"}', "/~2bad"),
        (b'["secret-8472"]', "/-"),
        (b'["secret-8472"]', "/01"),
        (b'["secret-8472"]', "/1"),
        (b'{"count":2}', "/count"),
        (b'{"message":"secret-8472"}', ""),
    ],
)
def test_reconstruct_rejects_invalid_or_non_string_targets_without_leaking_content(
    raw_body: bytes, json_pointer: str
) -> None:
    json_handler = JsonHandler()
    request_body = json_handler.normalize(raw_body, PolicyConfig())

    with pytest.raises(PrivacyGuardError) as exception_info:
        json_handler.reconstruct(request_body, {json_pointer: "replacement"})

    assert exception_info.value.code is ErrorCode.BODY_RECONSTRUCTION_INVALID
    assert "secret-8472" not in str(exception_info.value)
    assert "secret-8472" not in repr(exception_info.value)
    assert exception_info.value.__cause__ is None
    assert all(
        "secret-8472" not in str(argument) for argument in exception_info.value.args
    )


@pytest.mark.parametrize("parsed_value", [{"edit": "original"}, object()])
@pytest.mark.parametrize("replacements_by_path", [{}, {"/edit": "changed"}])
def test_reconstruct_rejects_foreign_state_without_leaking_content(
    parsed_value: object,
    replacements_by_path: dict[str, str],
) -> None:
    sensitive_text = "distinctive-sensitive-foreign-state-8472"
    request_body = RequestBody(
        text_blocks=(TextBlock(path="/edit", text="original"),),
        parsed_value=parsed_value,
        original_bytes=sensitive_text.encode(),
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().reconstruct(request_body, replacements_by_path)

    _assert_safe_error(
        exception_info.value,
        ErrorCode.BODY_RECONSTRUCTION_INVALID,
        sensitive_text,
    )


def test_reconstruct_copy_failure_drops_sensitive_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_text = "distinctive-sensitive-copy-8472"
    json_handler = JsonHandler()
    request_body = json_handler.normalize(b'{"edit":"original"}', PolicyConfig())

    def fail_copy(*values: object) -> None:
        raise RuntimeError(sensitive_text)

    monkeypatch.setattr(json_module, "deepcopy", fail_copy)

    with pytest.raises(PrivacyGuardError) as exception_info:
        json_handler.reconstruct(request_body, {"/edit": "changed"})

    _assert_safe_error(
        exception_info.value,
        ErrorCode.BODY_RECONSTRUCTION_INVALID,
        sensitive_text,
    )


def test_reconstruct_serialization_failure_drops_sensitive_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_text = "distinctive-sensitive-serialization-8472"
    json_handler = JsonHandler()
    request_body = json_handler.normalize(b'{"edit":"original"}', PolicyConfig())

    def fail_serialization(
        value: object,
        *,
        ensure_ascii: bool,
        separators: tuple[str, str],
        allow_nan: bool,
    ) -> str:
        raise TypeError(sensitive_text)

    monkeypatch.setattr(json_module.json, "dumps", fail_serialization)

    with pytest.raises(PrivacyGuardError) as exception_info:
        json_handler.reconstruct(request_body, {"/edit": "changed"})

    _assert_safe_error(
        exception_info.value,
        ErrorCode.BODY_RECONSTRUCTION_INVALID,
        sensitive_text,
    )


def test_reconstruct_rejects_unpaired_surrogate_replacement_without_leaking_body() -> (
    None
):
    sensitive_text = "distinctive-reconstruction-neighbor-8472"
    json_handler = JsonHandler()
    request_body = json_handler.normalize(
        f'{{"edit":"original","neighbor":"{sensitive_text}"}}'.encode(),
        PolicyConfig(),
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        json_handler.reconstruct(request_body, {"/edit": "\ud800"})

    assert exception_info.value.code is ErrorCode.BODY_RECONSTRUCTION_INVALID
    assert sensitive_text not in str(exception_info.value)
    assert sensitive_text not in repr(exception_info.value)


def test_reconstruct_does_not_mutate_original_parsed_value() -> None:
    json_handler = JsonHandler()
    request_body = json_handler.normalize(
        b'{"items":[{"message":"original"}],"other":"keep"}', PolicyConfig()
    )
    original_value = {"items": [{"message": "original"}], "other": "keep"}

    json_handler.reconstruct(request_body, {"/items/0/message": "changed"})

    assert type(request_body.parsed_value) is json_module._JsonBodyState
    assert _json_state_value(request_body) == original_value


def test_select_format_handler_returns_registered_json_singleton() -> None:
    json_handler = select_format_handler("json")

    assert isinstance(json_handler, JsonHandler)
    assert select_format_handler("json") is json_handler


def test_select_format_handler_rejects_unknown_kind_without_fallback() -> None:
    sentinel = "unknown-sensitive-format-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        select_format_handler(sentinel)

    assert exception_info.value.code is ErrorCode.BODY_FORMAT_UNSUPPORTED
    assert sentinel not in str(exception_info.value)
    assert sentinel not in repr(exception_info.value)
