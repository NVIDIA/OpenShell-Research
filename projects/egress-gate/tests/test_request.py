"""Focused boundary tests for the protobuf-free request domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_HEADER_MUTATION_DATA_BYTES,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
)
from egress_gate.request import (
    ExistingHeaderAction,
    HttpHeader,
    HttpRequest,
    HttpTarget,
    Process,
    RemoveHeaderMutation,
    RequestContext,
    RequestPatch,
    WriteHeaderMutation,
)


def _request(
    *, body: bytes = b"payload", headers: tuple[HttpHeader, ...] = ()
) -> HttpRequest:
    return HttpRequest(
        context=RequestContext(
            request_id="request-1",
            sandbox_id="sandbox-1",
            originating_process=Process(
                binary="/usr/bin/python",
                pid=42,
                ancestors=("/usr/bin/init",),
            ),
        ),
        target=HttpTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/v1/items",
            query="page=1",
        ),
        headers=headers,
        body=body,
    )


def test_request_is_immutable_and_preserves_ordered_headers() -> None:
    headers = (
        HttpHeader(name="x-test", value="first"),
        HttpHeader(name="x-test", value="second"),
    )
    request = _request(headers=headers)

    assert request.body == b"payload"
    assert request.headers == headers
    assert request.context.originating_process is not None
    assert request.context.originating_process.ancestors == ("/usr/bin/init",)

    with pytest.raises(ValidationError):
        setattr(request, "body", b"changed")


@pytest.mark.parametrize(
    ("body", "valid"),
    [(b"x" * MAX_BODY_BYTES, True), (b"x" * (MAX_BODY_BYTES + 1), False)],
)
def test_request_body_boundary(body: bytes, valid: bool) -> None:
    if valid:
        assert _request(body=body).body == body
    else:
        with pytest.raises(ValidationError):
            _request(body=body)


def test_header_count_and_data_boundaries() -> None:
    headers = tuple(
        HttpHeader(name=f"x-{index}", value="v") for index in range(MAX_PROTO_HEADERS)
    )
    assert len(_request(headers=headers).headers) == MAX_PROTO_HEADERS

    with pytest.raises(ValidationError):
        _request(headers=headers + (HttpHeader(name="x-over", value="v"),))

    exact_data = (HttpHeader(name="x", value="x" * (MAX_PROTO_HEADERS_BYTES - 1)),)
    assert _request(headers=exact_data).headers == exact_data
    with pytest.raises(ValidationError):
        _request(headers=(HttpHeader(name="x", value="x" * MAX_PROTO_HEADERS_BYTES),))


def test_request_patch_distinguishes_no_replacement_from_empty_body() -> None:
    no_replacement = RequestPatch()
    empty_replacement = RequestPatch(replacement_body=b"")

    assert no_replacement.is_empty
    assert not empty_replacement.is_empty


def test_request_patch_preserves_ordered_discriminated_header_mutations() -> None:
    patch = RequestPatch(
        header_mutations=(
            WriteHeaderMutation(
                name="x-test",
                value="one",
                on_existing=ExistingHeaderAction.APPEND,
            ),
            RemoveHeaderMutation(name="x-old"),
        )
    )

    assert patch.header_mutations[0].operation == "write"
    assert patch.header_mutations[1].operation == "remove"


def test_request_patch_rejects_invalid_mutation_bounds() -> None:
    mutation = RemoveHeaderMutation(name="x-test")
    with pytest.raises(ValidationError):
        RequestPatch(
            header_mutations=tuple(mutation for _ in range(MAX_HEADER_MUTATIONS + 1))
        )

    with pytest.raises(ValidationError):
        RequestPatch(
            header_mutations=(
                WriteHeaderMutation(
                    name="x",
                    value="x" * MAX_HEADER_MUTATION_DATA_BYTES,
                    on_existing=ExistingHeaderAction.OVERWRITE,
                ),
            )
        )


def test_request_models_reject_non_tuple_sequences_and_extra_fields() -> None:
    values: dict[str, object] = {
        "context": RequestContext(request_id="id", sandbox_id="sandbox"),
        "target": HttpTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="GET",
            path="/",
            query="",
        ),
        "headers": [HttpHeader(name="x", value="y")],
        "body": b"payload",
    }
    with pytest.raises(ValidationError):
        HttpRequest.model_validate(values)

    with pytest.raises(ValidationError):
        HttpHeader(name="", value="value")

    with pytest.raises(ValidationError):
        HttpTarget.model_validate(
            {
                "scheme": "https",
                "host": "example.com",
                "port": 443,
                "method": "GET",
                "path": "/",
                "query": "",
                "extra": "forbidden",
            }
        )
