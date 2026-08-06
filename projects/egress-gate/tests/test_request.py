"""Focused boundary tests for the protobuf-free request domain models."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from egress_gate.constants import (
    MAX_BODY_BYTES,
    MAX_HEADER_MUTATION_DATA_BYTES,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
)
from egress_gate.request import (
    ExistingHeaderAction,
    HeaderMutation,
    HttpHeader,
    HttpRequest,
    HttpTarget,
    Process,
    RemoveHeaderMutation,
    RequestContext,
    RequestMutations,
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


def test_request_mutations_distinguish_no_replacement_from_empty_body() -> None:
    no_replacement = RequestMutations()
    empty_replacement = RequestMutations(replacement_body=b"")

    assert no_replacement.is_empty
    assert not empty_replacement.is_empty


def test_request_mutations_preserve_ordered_discriminated_header_mutations() -> None:
    adapter = TypeAdapter(HeaderMutation)
    discriminator = adapter.json_schema().get("discriminator")
    assert isinstance(discriminator, dict)
    assert discriminator.get("propertyName") == "kind"

    request_mutations = RequestMutations(
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x-test",
                value="one",
                on_existing=ExistingHeaderAction.APPEND,
            ),
            RemoveHeaderMutation(kind="remove", name="x-old"),
        )
    )

    assert request_mutations.header_mutations[0].kind == "write"
    assert request_mutations.header_mutations[1].kind == "remove"

    with pytest.raises(ValidationError):
        adapter.validate_python({"name": "x-test"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"operation": "remove", "name": "x-test"})


def test_request_mutations_reject_invalid_bounds() -> None:
    mutation = RemoveHeaderMutation(kind="remove", name="x-test")
    assert (
        len(
            RequestMutations(
                header_mutations=tuple(mutation for _ in range(MAX_HEADER_MUTATIONS))
            ).header_mutations
        )
        == MAX_HEADER_MUTATIONS
    )
    with pytest.raises(ValidationError):
        RequestMutations(
            header_mutations=tuple(mutation for _ in range(MAX_HEADER_MUTATIONS + 1))
        )

    exact_data = RequestMutations(
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x",
                value="x" * (MAX_HEADER_MUTATION_DATA_BYTES - 1),
                on_existing=ExistingHeaderAction.OVERWRITE,
            ),
        )
    )
    assert exact_data.header_mutations
    with pytest.raises(ValidationError):
        RequestMutations(
            header_mutations=(
                WriteHeaderMutation(
                    kind="write",
                    name="x",
                    value="x" * MAX_HEADER_MUTATION_DATA_BYTES,
                    on_existing=ExistingHeaderAction.OVERWRITE,
                ),
            )
        )

    assert (
        len(
            RequestMutations(replacement_body=b"x" * MAX_BODY_BYTES).replacement_body
            or b""
        )
        == MAX_BODY_BYTES
    )
    with pytest.raises(ValidationError):
        RequestMutations(replacement_body=b"x" * (MAX_BODY_BYTES + 1))


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


def test_request_context_string_aggregate_has_an_exact_boundary() -> None:
    exact = RequestContext(
        request_id="r" * (MAX_PROTO_CONTEXT_BYTES - 1),
        sandbox_id="s",
    )
    assert len(exact.request_id.encode()) + len(exact.sandbox_id.encode()) == (
        MAX_PROTO_CONTEXT_BYTES
    )

    with pytest.raises(ValidationError):
        RequestContext(
            request_id="r" * MAX_PROTO_CONTEXT_BYTES,
            sandbox_id="s",
        )


def test_http_target_string_aggregate_has_an_exact_boundary() -> None:
    exact = HttpTarget(
        scheme="s" * (MAX_PROTO_TARGET_BYTES - 4),
        host="h",
        port=443,
        method="m",
        path="p",
        query="q",
    )
    assert (
        sum(
            len(value.encode())
            for value in (
                exact.scheme,
                exact.host,
                exact.method,
                exact.path,
                exact.query,
            )
        )
        == MAX_PROTO_TARGET_BYTES
    )

    with pytest.raises(ValidationError):
        HttpTarget(
            scheme="s" * (MAX_PROTO_TARGET_BYTES - 3),
            host="h",
            port=443,
            method="m",
            path="p",
            query="q",
        )
