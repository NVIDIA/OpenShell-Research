from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2 as pb2
from __PACKAGE_NAME__.server import (
    MAX_BODY_BYTES,
    SERVICE_NAME,
    build_manifest,
    evaluate_http_request,
    validate_config,
)


def test_manifest_advertises_pre_credentials_http() -> None:
    manifest = build_manifest()

    assert manifest.name == SERVICE_NAME
    assert len(manifest.bindings) == 1
    assert manifest.bindings[0].operation == pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST
    assert manifest.bindings[0].phase == pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    assert manifest.bindings[0].max_body_bytes == MAX_BODY_BYTES


def test_default_config_is_valid() -> None:
    response = validate_config(pb2.ValidateConfigRequest())

    assert response.valid is True
    assert response.reason == ""


def test_valid_request_is_allowed_without_mutation() -> None:
    response = evaluate_http_request(
        pb2.HttpRequestEvaluation(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        )
    )

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is False
    assert response.header_mutations == []


def test_unsupported_phase_is_denied() -> None:
    response = evaluate_http_request(pb2.HttpRequestEvaluation())

    assert response.decision == pb2.DECISION_DENY
    assert response.reason_code == "unsupported_phase"
