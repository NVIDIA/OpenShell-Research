from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError
from typing_extensions import override

from privacy_guard.config import PolicyConfig
from privacy_guard.request_body import (
    FormatHandler,
    FormatHandlerContractError,
    RequestBody,
    TextBlock,
    parse_normalized_body,
)


class OpaqueHandler(FormatHandler):
    def __init__(self, format_name: str = "opaque") -> None:
        super().__init__(format_name=format_name)

    @override
    def _normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
        return RequestBody(text_blocks=(), parsed_value=None, original_bytes=raw_body)

    @override
    def _reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        return request_body.original_bytes


@pytest.mark.parametrize(
    "values",
    [
        {"path": 3, "text": "text"},
        {"path": "path", "text": 3},
        {"path": "path", "text": "text", "replaceable": 1},
        {"path": "bad\ud800", "text": "text"},
        {"path": "path", "text": "bad\ud800"},
        {"path": "path", "text": "text", "extra": "forbidden"},
    ],
)
def test_text_block_rejects_invalid_fields(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TextBlock.model_validate(values)


def test_text_block_is_frozen_and_hides_sensitive_fields() -> None:
    sentinel = "sensitive-block-8472"
    block = TextBlock(path=f"/{sentinel}", text=sentinel)

    with pytest.raises((FrozenInstanceError, ValidationError)):
        setattr(block, "text", "changed")

    assert sentinel not in repr(block)


@pytest.mark.parametrize(
    "values",
    [
        {"text_blocks": [], "parsed_value": None, "original_bytes": b"body"},
        {"text_blocks": (object(),), "parsed_value": None, "original_bytes": b"body"},
        {"text_blocks": (), "parsed_value": None, "original_bytes": bytearray(b"body")},
        {
            "text_blocks": (),
            "parsed_value": None,
            "original_bytes": b"body",
            "extra": "forbidden",
        },
    ],
)
def test_request_body_rejects_invalid_fields(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RequestBody.model_validate(values)


def test_request_body_is_frozen_and_hides_sensitive_fields() -> None:
    sentinel = "sensitive-body-8472"
    body = RequestBody(
        text_blocks=(TextBlock(path=f"/{sentinel}", text=sentinel),),
        parsed_value={"value": sentinel},
        original_bytes=sentinel.encode(),
    )

    with pytest.raises((FrozenInstanceError, ValidationError)):
        setattr(body, "original_bytes", b"changed")

    assert sentinel not in repr(body)


def test_parse_normalized_body_reuses_exact_instance() -> None:
    body = RequestBody(text_blocks=(), parsed_value=None, original_bytes=b"body")

    assert parse_normalized_body(body) is body


@pytest.mark.parametrize(
    "result",
    [
        object(),
        {"text_blocks": (), "parsed_value": None, "original_bytes": b"body"},
    ],
)
def test_parse_normalized_body_rejects_non_models(result: object) -> None:
    with pytest.raises(FormatHandlerContractError):
        parse_normalized_body(result)


@pytest.mark.parametrize("format_name", ["", "bad\ud800"])
def test_handler_rejects_invalid_format_identity(format_name: str) -> None:
    with pytest.raises(FormatHandlerContractError):
        OpaqueHandler(format_name)


def test_handler_identity_is_read_only() -> None:
    handler = OpaqueHandler()

    with pytest.raises(AttributeError):
        setattr(handler, "format_name", "changed")
