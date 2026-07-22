import json
import traceback
from collections.abc import Mapping
from typing import Never

import grpc
import pytest
from pydantic import ValidationError
from typing_extensions import override

import privacy_guard.service.servicer as servicer_module
from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.config import PolicyAction, PolicyConfig
from privacy_guard.constants import (
    BLOCK_REASON,
    BLOCK_REASON_CODE,
    MAX_BODY_BYTES,
    SERVICE_NAME,
    SERVICE_VERSION,
)
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.processor import RequestProcessor
from privacy_guard.scanners import Finding, RequestBodyFinding, Scanner, ScannerConfig
from privacy_guard.service.servicer import PrivacyGuardMiddleware

from ..scanner_helpers import DeterministicEmailScanner


class AbortedRpc(Exception):
    pass


class RecordingContext:
    def __init__(self) -> None:
        self.code: grpc.StatusCode | None = None
        self.details: str | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> Never:
        self.code = code
        self.details = details
        raise AbortedRpc


class FakeProcessor:
    def __init__(self, result: ProcessingResult | None = None) -> None:
        self.result = result or ProcessingResult(decision=ProcessingDecision.ALLOW)
        self.validated_configs: list[PolicyConfig] = []
        self.requests: list[InterceptedRequest] = []

    def __bool__(self) -> bool:
        return False

    def validate_policy_config(self, policy_config: PolicyConfig) -> None:
        self.validated_configs.append(policy_config)

    def process(self, request: InterceptedRequest) -> ProcessingResult:
        self.requests.append(request)
        return self.result


def _assert_safe_translation(error: PrivacyGuardError, sentinel: str) -> None:
    assert error.__cause__ is None
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in repr(error.args)
    assert sentinel not in "".join(traceback.format_exception(error))


def _evaluation(
    *,
    body: bytes = b'{"message":"hello"}',
    config: Mapping[str, object] | None = None,
    phase: pb2.SupervisorMiddlewarePhase = (
        pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    ),
    headers: list[pb2.HttpHeader] | None = None,
) -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=phase,
        config=config or {},
        headers=headers or [],
        body=body,
    )


def test_describe_advertises_one_pre_credentials_http_binding() -> None:
    manifest = PrivacyGuardMiddleware()._describe()

    assert manifest.name == SERVICE_NAME == "privacy-guard"
    assert manifest.service_version == SERVICE_VERSION == "0.1.0"
    assert len(manifest.bindings) == 1
    binding = manifest.bindings[0]
    assert binding.operation == pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST
    assert binding.phase == pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    assert binding.max_body_bytes == MAX_BODY_BYTES == 4 * 1024 * 1024
    assert binding.timeout == ""


@pytest.mark.parametrize("action", [None, *PolicyAction])
def test_validate_config_accepts_defaults_and_actions(
    action: PolicyAction | None,
) -> None:
    values: dict[str, object] = {}
    if action is not None:
        values["on_finding"] = {"action": action.value}

    response = PrivacyGuardMiddleware()._validate_config(
        pb2.ValidateConfigRequest(config=values)
    )

    assert response.valid is True
    assert response.reason == ""


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": "sensitive-config-value-8472"},
        {"on_finding": {"action": "invalid-sensitive-action-8472"}},
        {"debug_inject_text": "sensitive-injection-value-8472"},
        {"body_format": "unknown-sensitive-format-8472"},
        {
            "on_finding": {
                "action": "redact",
                "entity_types": ["sensitive-entity-typo-8472"],
            }
        },
    ],
)
def test_validate_config_rejects_malformed_values_without_echoing_them(
    config: Mapping[str, object],
) -> None:
    response = PrivacyGuardMiddleware()._validate_config(
        pb2.ValidateConfigRequest(config=config)
    )

    assert response.valid is False
    assert "Hint:" in response.reason
    assert "8472" not in response.reason


def test_proto_policy_translation_discards_sensitive_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "sensitive-proto-config-8472"

    def reject_proto(config: object) -> dict[str, object]:
        raise ValueError(sentinel)

    monkeypatch.setattr(servicer_module.json_format, "MessageToDict", reject_proto)

    with pytest.raises(PrivacyGuardError) as exception_info:
        servicer_module._policy_from_proto(pb2.ValidateConfigRequest().config)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID
    _assert_safe_translation(exception_info.value, sentinel)


def test_intercepted_request_translation_discards_sensitive_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "sensitive-intercepted-request-8472"
    validation_error = ValidationError.from_exception_data(
        "InterceptedRequest",
        [
            {
                "type": "value_error",
                "loc": ("raw_body",),
                "input": sentinel,
                "ctx": {"error": ValueError(sentinel)},
            }
        ],
    )

    def reject_request(**values: object) -> Never:
        raise validation_error

    monkeypatch.setattr(servicer_module, "InterceptedRequest", reject_request)

    with pytest.raises(PrivacyGuardError) as exception_info:
        servicer_module._request_from_proto(_evaluation())

    assert exception_info.value.code is ErrorCode.UNEXPECTED_SERVICE_FAILURE
    _assert_safe_translation(exception_info.value, sentinel)


def test_validate_config_uses_domain_parser_and_processor_validation() -> None:
    processor = FakeProcessor()
    servicer = PrivacyGuardMiddleware(processor)

    response = servicer._validate_config(
        pb2.ValidateConfigRequest(config={"on_finding": {"action": "observe"}})
    )

    assert response.valid is True
    assert processor.validated_configs[0].on_finding.action is PolicyAction.OBSERVE


@pytest.mark.asyncio
async def test_passthrough_and_bodyless_requests_allow_without_replacement() -> None:
    servicer = PrivacyGuardMiddleware()

    ordinary = await servicer._evaluate_http_request(_evaluation())
    bodyless = await servicer._evaluate_http_request(_evaluation(body=b""))

    for response in (ordinary, bodyless):
        assert response.decision == pb2.DECISION_ALLOW
        assert response.has_body is False
        assert response.body == b""
        assert response.reason == ""
        assert response.reason_code == ""


@pytest.mark.asyncio
async def test_body_larger_than_advertised_limit_is_rejected_before_processing() -> (
    None
):
    processor = FakeProcessor()
    context = RecordingContext()
    servicer = PrivacyGuardMiddleware(processor)

    with pytest.raises(AbortedRpc):
        await servicer._evaluate_rpc(
            _evaluation(body=b"x" * (MAX_BODY_BYTES + 1)),
            context,
        )

    assert context.code is grpc.StatusCode.INVALID_ARGUMENT
    assert context.details is not None
    assert ErrorCode.REQUEST_BODY_TOO_LARGE.value in context.details
    assert processor.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b'{"messages":[{"content":"user@example.com"}]}',
            {"messages": [{"content": "[email]"}]},
        ),
        (
            b'{"contents":[{"parts":[{"text":"user@example.com"}]}]}',
            {"contents": [{"parts": [{"text": "[email]"}]}]},
        ),
    ],
)
async def test_deterministic_finding_crosses_full_proto_domain_boundary(
    body: bytes, expected: object
) -> None:
    response = await PrivacyGuardMiddleware(
        RequestProcessor(
            [
                DeterministicEmailScanner(
                    ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
                )
            ]
        )
    )._evaluate_http_request(_evaluation(body=body))

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is True
    assert json.loads(response.body) == expected


@pytest.mark.asyncio
async def test_first_content_type_header_is_passed_to_processor() -> None:
    processor = FakeProcessor()
    servicer = PrivacyGuardMiddleware(processor)

    await servicer._evaluate_http_request(
        _evaluation(
            headers=[
                pb2.HttpHeader(name="content-type", value="first/type"),
                pb2.HttpHeader(name="content-type", value="second/type"),
            ]
        )
    )

    assert processor.requests[0].content_type == "first/type"


@pytest.mark.asyncio
async def test_explicit_empty_replacement_sets_has_body() -> None:
    processor = FakeProcessor(
        ProcessingResult(decision=ProcessingDecision.ALLOW, replacement_body=b"")
    )

    response = await PrivacyGuardMiddleware(processor)._evaluate_http_request(
        _evaluation()
    )

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is True
    assert response.body == b""


@pytest.mark.asyncio
async def test_deny_has_stable_reason_code_and_never_returns_a_body() -> None:
    processor = FakeProcessor(
        ProcessingResult(
            decision=ProcessingDecision.DENY,
            replacement_body=None,
            reason_code=BLOCK_REASON_CODE,
        )
    )

    response = await PrivacyGuardMiddleware(processor)._evaluate_http_request(
        _evaluation()
    )

    assert response.decision == pb2.DECISION_DENY
    assert response.reason == BLOCK_REASON
    assert response.reason_code == BLOCK_REASON_CODE
    assert response.has_body is False
    assert response.body == b""


@pytest.mark.asyncio
async def test_findings_aggregate_in_first_observed_group_order() -> None:
    findings = tuple(
        RequestBodyFinding(finding=finding, text_block_path=path)
        for finding, path in (
            (
                Finding(
                    entity="email",
                    scanner_name="scanner-a",
                    start_offset=0,
                    end_offset=1,
                ),
                "a",
            ),
            (
                Finding(
                    entity="token",
                    scanner_name="scanner-b",
                    start_offset=1,
                    end_offset=2,
                ),
                "a",
            ),
            (
                Finding(
                    entity="email",
                    scanner_name="scanner-a",
                    start_offset=2,
                    end_offset=3,
                ),
                "b",
            ),
        )
    )
    processor = FakeProcessor(
        ProcessingResult(decision=ProcessingDecision.ALLOW, findings=findings)
    )

    response = await PrivacyGuardMiddleware(processor)._evaluate_http_request(
        _evaluation()
    )

    assert [
        (finding.type, finding.label, finding.count) for finding in response.findings
    ] == [
        ("scanner-a", "email", 2),
        ("scanner-b", "token", 1),
    ]
    assert all(finding.confidence == "high" for finding in response.findings)
    assert all(not finding.severity for finding in response.findings)


@pytest.mark.asyncio
async def test_more_than_32_finding_groups_stably_denies() -> None:
    findings = tuple(
        RequestBodyFinding(
            finding=Finding(
                entity=f"entity-{index}",
                scanner_name="scanner",
                start_offset=0,
                end_offset=1,
            ),
            text_block_path="path",
        )
        for index in range(33)
    )
    processor = FakeProcessor(
        ProcessingResult(decision=ProcessingDecision.ALLOW, findings=findings)
    )
    response = await PrivacyGuardMiddleware(processor)._evaluate_http_request(
        _evaluation()
    )

    assert response.decision == pb2.DECISION_DENY
    assert response.reason_code == "privacy_guard_limit_exceeded"
    assert not response.findings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evaluation", "expected_code"),
    [
        (
            _evaluation(phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED),
            ErrorCode.REQUEST_PHASE_INVALID,
        ),
        (_evaluation(body=b'"bad\xff"'), ErrorCode.BODY_ENCODING_INVALID),
        (_evaluation(body=b'{"bad":}'), ErrorCode.BODY_JSON_INVALID),
        (_evaluation(config={"body_format": "xml"}), ErrorCode.BODY_FORMAT_UNSUPPORTED),
    ],
)
async def test_invalid_input_maps_to_invalid_argument_with_safe_catalog_details(
    evaluation: pb2.HttpRequestEvaluation, expected_code: ErrorCode
) -> None:
    context = RecordingContext()

    with pytest.raises(AbortedRpc):
        await PrivacyGuardMiddleware()._evaluate_rpc(evaluation, context)

    assert context.code is grpc.StatusCode.INVALID_ARGUMENT
    assert context.details is not None
    assert expected_code.value in context.details
    assert "Hint:" in context.details
    assert "8472" not in context.details


@pytest.mark.asyncio
async def test_processor_runtime_failure_maps_to_internal_catalog_error() -> None:
    class RaisingProcessor(FakeProcessor):
        @override
        def process(self, request: InterceptedRequest) -> ProcessingResult:
            raise RuntimeError("sensitive-runtime-failure-8472")

    context = RecordingContext()

    with pytest.raises(AbortedRpc):
        await PrivacyGuardMiddleware(RaisingProcessor())._evaluate_rpc(
            _evaluation(), context
        )

    assert context.code is grpc.StatusCode.INTERNAL
    assert context.details is not None
    assert ErrorCode.UNEXPECTED_SERVICE_FAILURE.value in context.details
    assert "8472" not in context.details


@pytest.mark.asyncio
async def test_cataloged_scanner_failure_maps_to_internal_status() -> None:
    class RaisingScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str) -> tuple[Finding, ...]:
            raise RuntimeError("sensitive-scanner-failure-8472")

    context = RecordingContext()
    scanner = RaisingScanner(ScannerConfig(name="raising", entity_types=frozenset()))
    servicer = PrivacyGuardMiddleware(RequestProcessor([scanner]))

    with pytest.raises(AbortedRpc):
        await servicer._evaluate_rpc(_evaluation(), context)

    assert context.code is grpc.StatusCode.INTERNAL
    assert context.details is not None
    assert ErrorCode.SCANNER_EXECUTION_FAILED.value in context.details
    assert "8472" not in context.details


def test_servicer_repr_does_not_retain_request_or_config_content() -> None:
    servicer = PrivacyGuardMiddleware()

    assert "sensitive-body-8472" not in repr(servicer)
    assert "sensitive-injection-8472" not in repr(servicer)
