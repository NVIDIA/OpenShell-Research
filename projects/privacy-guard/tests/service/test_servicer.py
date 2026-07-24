"""Service boundary tests over the canonical OpenShell-owned protobuf."""

from __future__ import annotations

import asyncio

import pytest
from google.protobuf import json_format
from google.protobuf.message import Message

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.service.server import create_builtin_registry
from privacy_guard.service.servicer import PrivacyGuardMiddleware


def _values(action: str = "replace") -> dict[str, object]:
    return {
        "entity_processing": {
            "stages": [
                {
                    "config": {
                        "engine": "regex",
                        "pattern_catalog": {
                            "entities": [
                                {
                                    "name": "email",
                                    "patterns": [
                                        {
                                            "pattern": r"[a-z]+@[a-z]+\.[a-z]+",
                                            "confidence": "high",
                                        }
                                    ],
                                }
                            ]
                        },
                        "replacement": {
                            "strategy": "template",
                            "template": "[{entity}]",
                        },
                    }
                }
            ]
        },
        "on_detection": {"action": action},
    }


def _proto_config(values: dict[str, object]) -> Message:
    result = pb2.ValidateConfigRequest().config
    json_format.ParseDict(values, result)
    return result


def _request(body: bytes, *, action: str = "replace") -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        config=_proto_config(_values(action)),
        body=body,
    )


def test_copied_proto_remains_the_current_openshell_contract() -> None:
    evaluation = pb2.HttpRequestEvaluation()
    finding = pb2.Finding()

    assert isinstance(evaluation.config, Message)
    assert not hasattr(evaluation, "config_fingerprint")
    assert not hasattr(finding, "source")


def test_validate_config_is_pure_and_reports_invalid_config() -> None:
    middleware = PrivacyGuardMiddleware(create_builtin_registry())

    valid = middleware._validate_config(
        pb2.ValidateConfigRequest(config=_proto_config(_values()))
    )
    invalid = middleware._validate_config(
        pb2.ValidateConfigRequest(config=_proto_config({"on_detection": {}}))
    )

    assert valid.valid is True
    assert invalid.valid is False
    assert "config_invalid" in invalid.reason


def test_evaluation_decodes_one_utf8_text_and_encodes_replacement() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            return await middleware._evaluate_http_request(_request(b"email a@b.com"))
        finally:
            await middleware.close()

    result = asyncio.run(evaluate())

    assert result.decision == pb2.DECISION_ALLOW
    assert result.has_body is True
    assert result.body == b"email [email]"
    assert len(result.findings) == 1
    assert result.findings[0].type == "detected_entity"
    assert result.findings[0].label == "email (regex[1])"


def test_invalid_utf8_fails_before_invoking_an_engine() -> None:
    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            with pytest.raises(PrivacyGuardError) as captured:
                await middleware._evaluate_http_request(_request(b"\xff"))
            assert captured.value.code is ErrorCode.BODY_ENCODING_INVALID
        finally:
            await middleware.close()

    asyncio.run(evaluate())


def test_detect_returns_no_body_mutation() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            return await middleware._evaluate_http_request(
                _request(b"a@b.com", action="detect")
            )
        finally:
            await middleware.close()

    result = asyncio.run(evaluate())

    assert result.decision == pb2.DECISION_ALLOW
    assert result.has_body is False
    assert result.body == b""
