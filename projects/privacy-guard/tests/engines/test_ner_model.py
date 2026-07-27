"""Contract tests for NER model facade implementations."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from threading import Event, Thread

import pytest

from privacy_guard.engines import LocalNERModel, NERExtractEndpointModel
from privacy_guard.errors import (
    EngineExecutionError,
    EngineLimitExceededError,
    TimeoutExpiredError,
)
from privacy_guard.timeout import Timeout


class _EndpointHandler(BaseHTTPRequestHandler):
    response_body = b'{"entities":[]}'
    received_body = b""
    received_path = ""

    def do_POST(self) -> None:
        type(self).received_path = self.path
        content_length = int(self.headers.get("Content-Length", "0"))
        type(self).received_body = self.rfile.read(content_length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _endpoint(
    response_body: bytes,
) -> Iterator[tuple[str, type[_EndpointHandler]]]:
    handler = type("EndpointHandler", (_EndpointHandler,), {})
    handler.response_body = response_body
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}/v1/extract", handler
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_extract_endpoint_sends_exact_contract_and_normalizes_entities() -> None:
    response = json.dumps(
        {
            "entities": [
                {
                    "text": "Alice",
                    "label": "person",
                    "start": 0,
                    "end": 5,
                    "score": 0.75,
                    "unknown": "ignored",
                }
            ],
            "tagged_text": "ignored",
            "total_entities": 1,
        }
    ).encode()
    with _endpoint(response) as (endpoint, handler):
        model = NERExtractEndpointModel(
            endpoint=endpoint,
            model="nvidia/gliner-PII",
            chunk_length=384,
            overlap=128,
        )

        entities = model.predict_entities(
            "Alice",
            labels=("person",),
            threshold=0.3,
            flat_ner=False,
            timeout=Timeout.from_seconds(2),
        )

    assert handler.received_path == "/v1/extract"
    assert json.loads(handler.received_body) == {
        "text": "Alice",
        "labels": ["person"],
        "model": "nvidia/gliner-PII",
        "threshold": 0.3,
        "chunk_length": 384,
        "overlap": 128,
        "flat_ner": False,
    }
    assert [
        (entity.label, entity.start, entity.end, entity.score) for entity in entities
    ] == [("person", 0, 5, 0.75)]


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"{}",
        b'{"entities":{}}',
        b'{"entities":[{"label":"person","start":0,"end":1}]}',
        b'{"entities":[{"label":"person","start":false,"end":1,"score":0.5}]}',
        b'{"entities":[{"label":"person","start":1,"end":1,"score":0.5}]}',
        b'{"entities":[{"label":"person","start":0,"end":1,"score":2}]}',
    ],
)
def test_extract_endpoint_rejects_malformed_responses(body: bytes) -> None:
    with _endpoint(body) as (endpoint, _):
        model = NERExtractEndpointModel(endpoint=endpoint, model="model")

        with pytest.raises(EngineExecutionError):
            model.predict_entities(
                "x",
                labels=("person",),
                threshold=0.5,
                flat_ner=True,
                timeout=Timeout.from_seconds(2),
            )


def test_extract_endpoint_bounds_response_before_json_decoding() -> None:
    with _endpoint(b'{"entities":[]}' + b" " * 100) as (endpoint, _):
        model = NERExtractEndpointModel(
            endpoint=endpoint,
            model="model",
            max_response_bytes=16,
        )

        with pytest.raises(EngineLimitExceededError):
            model.predict_entities(
                "x",
                labels=("person",),
                threshold=0.5,
                flat_ner=True,
                timeout=Timeout.from_seconds(2),
            )


def test_extract_endpoint_translates_connection_failure_content_safely() -> None:
    model = NERExtractEndpointModel(
        endpoint="http://127.0.0.1:1/v1/extract",
        model="secret-model-name",
    )

    with pytest.raises(EngineExecutionError) as exception_info:
        model.predict_entities(
            "secret request text",
            labels=("person",),
            threshold=0.5,
            flat_ner=True,
            timeout=Timeout.from_seconds(1),
        )

    message = str(exception_info.value)
    assert "secret request text" not in message
    assert "secret-model-name" not in message
    assert "127.0.0.1" not in message
    assert "127.0.0.1" not in repr(model)
    assert "secret-model-name" not in repr(model)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://example.com/v1/extract",
        "http://user:password@example.com/v1/extract",
        "http://example.com/other",
        "http://example.com/v1/extract?token=secret",
    ],
)
def test_extract_endpoint_requires_an_explicit_safe_contract_url(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError):
        NERExtractEndpointModel(endpoint=endpoint, model="model")


class _LoadedModel:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        flat_ner: bool,
    ) -> object:
        self.calls.append((text, labels, threshold, flat_ner))
        return [
            {
                "text": "ignored",
                "label": "person",
                "start": 0,
                "end": 1,
                "score": 0.9,
            }
        ]


def test_local_model_passes_explicit_arguments_and_normalizes_output() -> None:
    loaded = _LoadedModel()
    model = LocalNERModel(model=loaded, chunk_length=512, overlap=64)

    entities = model.predict_entities(
        "x",
        labels=("person",),
        threshold=0.4,
        flat_ner=True,
        timeout=Timeout.from_seconds(1),
    )

    assert loaded.calls == [("x", ["person"], 0.4, True)]
    assert model.chunk_length == 512
    assert model.overlap == 64
    assert [
        (entity.label, entity.start, entity.end, entity.score) for entity in entities
    ] == [("person", 0, 1, 0.9)]


class _ChunkingLoadedModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        flat_ner: bool,
    ) -> object:
        del labels, threshold, flat_ner
        self.calls.append(text)
        entities: list[dict[str, object]] = []
        position = text.find("éx")
        if position >= 0:
            entities.append(
                {
                    "label": "token",
                    "start": position,
                    "end": position + 2,
                    "score": 0.8,
                }
            )
        return entities


def test_local_model_chunks_complete_input_rebases_unicode_and_deduplicates() -> None:
    loaded = _ChunkingLoadedModel()
    model = LocalNERModel(model=loaded, chunk_length=5, overlap=2)

    entities = model.predict_entities(
        "abcéxyz",
        labels=("token",),
        threshold=0.5,
        flat_ner=False,
        timeout=Timeout.from_seconds(1),
    )

    assert loaded.calls == ["abcéx", "éxyz"]
    assert [
        (entity.label, entity.start, entity.end, entity.score) for entity in entities
    ] == [("token", 3, 5, 0.8)]


class _BlockingLoadedModel:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        flat_ner: bool,
    ) -> object:
        del text, labels, threshold, flat_ner
        self.started.set()
        self.release.wait(timeout=2)
        return []


def test_local_model_bounds_serialized_lock_acquisition() -> None:
    loaded = _BlockingLoadedModel()
    model = LocalNERModel(model=loaded)
    first_error: list[BaseException] = []

    def first_call() -> None:
        try:
            model.predict_entities(
                "first",
                labels=("person",),
                threshold=0.5,
                flat_ner=True,
                timeout=Timeout.from_seconds(2),
            )
        except BaseException as error:
            first_error.append(error)

    thread = Thread(target=first_call)
    thread.start()
    assert loaded.started.wait(timeout=1)
    try:
        with pytest.raises(TimeoutExpiredError):
            model.predict_entities(
                "second",
                labels=("person",),
                threshold=0.5,
                flat_ner=True,
                timeout=Timeout.from_seconds(0.01),
            )
    finally:
        loaded.release.set()
        thread.join()

    assert first_error == []


class _FailingLoadedModel:
    def predict_entities(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        flat_ner: bool,
    ) -> object:
        del labels, threshold, flat_ner
        raise RuntimeError(text)


def test_local_model_translates_runtime_failure_without_content() -> None:
    model = LocalNERModel(model=_FailingLoadedModel())

    with pytest.raises(EngineExecutionError) as exception_info:
        model.predict_entities(
            "secret text",
            labels=("person",),
            threshold=0.5,
            flat_ner=False,
            timeout=Timeout.from_seconds(1),
        )

    assert "secret text" not in str(exception_info.value)


@pytest.mark.skipif(
    "PRIVACY_GUARD_DGX_NER_ENDPOINT" not in os.environ,
    reason="DGX NER smoke test is explicitly opt-in",
)
def test_opt_in_dgx_endpoint_detects_sample_entities() -> None:
    endpoint = os.environ["PRIVACY_GUARD_DGX_NER_ENDPOINT"]
    model_name = os.environ.get(
        "PRIVACY_GUARD_DGX_NER_MODEL",
        "nvidia/gliner-PII",
    )
    model = NERExtractEndpointModel(endpoint=endpoint, model=model_name)

    entities = model.predict_entities(
        "Contact Alice Example at alice@example.com or +1 202-555-0147.",
        labels=("person", "email", "phone_number"),
        threshold=0.3,
        flat_ner=False,
        timeout=Timeout.from_seconds(30),
    )

    assert {entity.label.casefold() for entity in entities} >= {
        "person",
        "email",
        "phone_number",
    }


@pytest.mark.skipif(
    "PRIVACY_GUARD_LOCAL_NER_MODEL_PATH" not in os.environ,
    reason="local GLiNER smoke test is explicitly opt-in",
)
def test_opt_in_local_gliner_detects_sample_entities_without_download() -> None:
    model_path = os.environ["PRIVACY_GUARD_LOCAL_NER_MODEL_PATH"]
    gliner_type = getattr(import_module("gliner"), "GLiNER")
    loaded_model = gliner_type.from_pretrained(
        model_path,
        local_files_only=True,
    )
    model = LocalNERModel(model=loaded_model)

    entities = model.predict_entities(
        "Contact Alice Example at alice@example.com.",
        labels=("person", "email"),
        threshold=0.3,
        flat_ner=False,
        timeout=Timeout.from_seconds(30),
    )

    assert {entity.label.casefold() for entity in entities} >= {
        "person",
        "email",
    }
