from __future__ import annotations

import asyncio
import logging
import threading
import tracemalloc
from typing import Never

import grpc
import pytest
from pydantic import ValidationError
from typing_extensions import override

import privacy_guard.service.servicer as servicer_module
from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.config import PolicyConfig
from privacy_guard.constants import MAX_BODY_BYTES
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.processor import RequestProcessor
from privacy_guard.request_body import JsonHandler
from privacy_guard.scanners import (
    Confidence,
    Finding,
    RequestBodyFinding,
    ScanBudget,
    Scanner,
    ScannerConfig,
)
from privacy_guard.service.servicer import PrivacyGuardMiddleware

from .scanner_helpers import DeterministicEmailScanner


def _request(
    body: bytes,
    *,
    action_kind: str = "redact",
    entity_types: list[str] | None = None,
    minimum_confidence: str | None = None,
) -> InterceptedRequest:
    return InterceptedRequest(
        raw_body=body,
        policy_config=PolicyConfig.from_mapping(
            {
                "on_finding": {
                    "action": action_kind,
                    "entity_types": entity_types,
                    "minimum_confidence": minimum_confidence,
                }
            }
        ),
    )


class UnexpectedAbortContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> Never:
        raise AssertionError("successful evaluation must not abort")


@pytest.mark.parametrize(
    ("depth", "allowed"),
    [(64, True), (65, False)],
)
def test_json_nesting_limit_exact_boundary(depth: int, allowed: bool) -> None:
    body = (b"[" * depth) + b'"safe"' + (b"]" * depth)
    if allowed:
        assert (
            JsonHandler().normalize(body, PolicyConfig()).text_blocks[-1].text == "safe"
        )
    else:
        with pytest.raises(PrivacyGuardError) as exception_info:
            JsonHandler().normalize(body, PolicyConfig())
        assert exception_info.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED


@pytest.mark.parametrize("depth", [255, 300, 500, 900])
def test_json_nesting_beyond_adapter_recursion_limit_is_shape_error(depth: int) -> None:
    body = (b"[" * depth) + b'"safe"' + (b"]" * depth)

    with pytest.raises(PrivacyGuardError) as exception_info:
        JsonHandler().normalize(body, PolicyConfig())

    assert exception_info.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED
    assert exception_info.value.__cause__ is None


def test_json_walker_bounds_allocation_at_exact_aggregate_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.request_body.json as json_module

    monkeypatch.setattr(json_module, "MAX_TEXT_BLOCKS", 2)
    monkeypatch.setattr(json_module, "MAX_SCANNED_CHARACTERS", 2)
    assert len(JsonHandler().normalize(b'{"a":"b"}', PolicyConfig()).text_blocks) == 2

    monkeypatch.setattr(json_module, "MAX_TEXT_BLOCKS", 1)
    with pytest.raises(PrivacyGuardError) as block_error:
        JsonHandler().normalize(b'{"a":"b"}', PolicyConfig())
    assert block_error.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED

    monkeypatch.setattr(json_module, "MAX_TEXT_BLOCKS", 2)
    monkeypatch.setattr(json_module, "MAX_SCANNED_CHARACTERS", 1)
    with pytest.raises(PrivacyGuardError) as text_error:
        JsonHandler().normalize(b'{"a":"b"}', PolicyConfig())
    assert text_error.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED


def test_json_walker_keeps_wide_container_traversal_allocation_bounded() -> None:
    import privacy_guard.request_body.json as json_module

    raw_body = b"[" + (b"0," * 99_999) + b"0]"
    handler = JsonHandler()
    policy = PolicyConfig()

    tracemalloc.start()
    try:
        request_body = handler.normalize(raw_body, policy)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert request_body.text_blocks == ()
    # Parsing and the one strict typed boundary peaked near 1.8 MiB locally.
    assert peak_bytes < 8 * 1024 * 1024

    assert type(request_body.parsed_value) is json_module._JsonBodyState
    tracemalloc.start()
    try:
        assert tuple(handler._iter_text_blocks(request_body.parsed_value.value)) == ()
        _, walker_peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # The incremental walker retains iterator state only for nesting depth.
    assert walker_peak_bytes < 64 * 1024


def test_processor_checks_handler_aggregate_boundaries_contextually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.processor as processor_module

    processor = RequestProcessor(
        [
            DeterministicEmailScanner(
                ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
            )
        ]
    )
    monkeypatch.setattr(processor_module, "MAX_TEXT_BLOCKS", 2)
    monkeypatch.setattr(processor_module, "MAX_SCANNED_CHARACTERS", 2)
    assert (
        processor.process(_request(b'{"a":"b"}')).decision is ProcessingDecision.ALLOW
    )

    monkeypatch.setattr(processor_module, "MAX_TEXT_BLOCKS", 1)
    with pytest.raises(PrivacyGuardError) as block_error:
        processor.process(_request(b'{"a":"b"}'))
    assert block_error.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED

    monkeypatch.setattr(processor_module, "MAX_TEXT_BLOCKS", 2)
    monkeypatch.setattr(processor_module, "MAX_SCANNED_CHARACTERS", 1)
    with pytest.raises(PrivacyGuardError) as text_error:
        processor.process(_request(b'{"a":"b"}'))
    assert text_error.value.code is ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("observe", ProcessingDecision.ALLOW),
        ("block", ProcessingDecision.DENY),
        ("redact", ProcessingDecision.DENY),
    ],
)
def test_json_key_findings_are_observed_blocked_and_never_rewritten(
    action: str, expected: ProcessingDecision
) -> None:
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )
    result = RequestProcessor([scanner]).process(
        _request(b'{"user@example.com":"safe","model":"safe"}', action_kind=action)
    )

    assert result.decision is expected
    assert len(result.findings) == 1
    assert result.findings[0].text_block_path == "#key:/user@example.com"
    assert result.replacement_body is None


@pytest.mark.parametrize("action_kind", ["observe", "block", "redact"])
def test_finding_criteria_are_owned_by_every_action(action_kind: str) -> None:
    class MixedScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                Finding(
                    entity="email",
                    scanner_name=self.scanner_name,
                    start_offset=0,
                    end_offset=1,
                    confidence=Confidence.LOW,
                ),
                Finding(
                    entity="token",
                    scanner_name=self.scanner_name,
                    start_offset=1,
                    end_offset=2,
                    confidence=Confidence.HIGH,
                ),
            )

    result = RequestProcessor(
        [
            MixedScanner(
                ScannerConfig(name="mixed", entity_types=frozenset({"email", "token"}))
            )
        ]
    ).process(
        _request(
            b'"ab"',
            action_kind=action_kind,
            entity_types=["token"],
            minimum_confidence="medium",
        )
    )

    assert [
        request_body_finding.finding.entity for request_body_finding in result.findings
    ] == ["token"]


def test_unknown_entity_filter_is_content_safe_and_multi_scanner_union_is_valid() -> (
    None
):
    class EmailScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return ()

    class TokenScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return ()

    processor = RequestProcessor(
        (
            EmailScanner(
                ScannerConfig(name="email", entity_types=frozenset({"email"}))
            ),
            TokenScanner(
                ScannerConfig(name="token", entity_types=frozenset({"token"}))
            ),
        )
    )
    processor.validate_policy_config(
        PolicyConfig.from_mapping(
            {
                "on_finding": {
                    "action": "redact",
                    "entity_types": ["email", "token"],
                }
            }
        )
    )
    typo = "sensitive-email-typo-8472"
    with pytest.raises(PrivacyGuardError) as exception_info:
        processor.validate_policy_config(
            PolicyConfig.from_mapping(
                {
                    "on_finding": {
                        "action": "redact",
                        "entity_types": [typo],
                    }
                }
            )
        )
    assert exception_info.value.code is ErrorCode.CONFIG_INVALID
    assert typo not in str(exception_info.value)
    with pytest.raises(PrivacyGuardError) as process_error:
        processor.process(_request(b'"safe"', entity_types=[typo]))
    assert process_error.value.code is ErrorCode.CONFIG_INVALID
    assert typo not in str(process_error.value)


def test_multi_scanner_overlap_is_retained_for_observe_and_resolved_for_redact() -> (
    None
):
    class FirstScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                Finding(
                    entity="short",
                    scanner_name=self.scanner_name,
                    start_offset=1,
                    end_offset=3,
                ),
            )

    class SecondScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                Finding(
                    entity="long",
                    scanner_name=self.scanner_name,
                    start_offset=0,
                    end_offset=4,
                ),
            )

    processor = RequestProcessor(
        (
            FirstScanner(
                ScannerConfig(name="first", entity_types=frozenset({"short"}))
            ),
            SecondScanner(
                ScannerConfig(name="second", entity_types=frozenset({"long"}))
            ),
        )
    )
    observed = processor.process(_request(b'"abcd"', action_kind="observe"))
    redacted = processor.process(_request(b'"abcd"', action_kind="redact"))
    blocked = processor.process(_request(b'"abcd"', action_kind="block"))

    assert len(observed.findings) == 2
    assert redacted.replacement_body == b'"[long]"'
    assert {
        request_body_finding.finding.scanner_name
        for request_body_finding in redacted.findings
    } == {
        "first",
        "second",
    }
    assert blocked.decision is ProcessingDecision.DENY
    assert len(blocked.findings) == 2


def test_finding_excess_stably_denies_without_returning_partial_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.scanners.base as scanner_module

    class NoisyScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                Finding(
                    entity="a",
                    scanner_name=self.scanner_name,
                    start_offset=0,
                    end_offset=1,
                ),
                Finding(
                    entity="b",
                    scanner_name=self.scanner_name,
                    start_offset=1,
                    end_offset=2,
                ),
            )

    monkeypatch.setattr(scanner_module, "MAX_FINDINGS_PER_BLOCK", 1)
    scanner = NoisyScanner(
        ScannerConfig(name="noisy", entity_types=frozenset({"a", "b"}))
    )
    result = RequestProcessor([scanner]).process(_request(b'"ab"'))

    assert result.decision is ProcessingDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert result.findings == ()


def test_scanner_identity_is_non_empty_and_finding_entities_are_bounded() -> None:
    with pytest.raises(ValidationError):
        ScannerConfig(name="", entity_types=frozenset())

    with pytest.raises(ValidationError):
        Finding(
            entity="x" * 1025,
            scanner_name="bounded",
            start_offset=0,
            end_offset=1,
        )


@pytest.mark.parametrize(("block_count", "allowed"), [(16, True), (17, False)])
def test_request_finding_limit_exact_boundary(block_count: int, allowed: bool) -> None:
    class DenseScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return tuple(
                Finding(
                    entity="unit",
                    scanner_name=self.scanner_name,
                    start_offset=index,
                    end_offset=index + 1,
                )
                for index in range(256)
            )

    body = (
        "[" + ",".join('"' + ("x" * 256) + '"' for _ in range(block_count)) + "]"
    ).encode()
    result = RequestProcessor(
        [DenseScanner(ScannerConfig(name="dense", entity_types=frozenset({"unit"})))]
    ).process(_request(body, action_kind="observe"))

    assert result.decision is (
        ProcessingDecision.ALLOW if allowed else ProcessingDecision.DENY
    )
    assert len(result.findings) == (4096 if allowed else 0)


@pytest.mark.asyncio
async def test_slow_scan_does_not_stall_unrelated_rpc() -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            started.set()
            release.wait(timeout=2)
            return ()

    servicer = PrivacyGuardMiddleware(
        RequestProcessor(
            [SlowScanner(ScannerConfig(name="slow", entity_types=frozenset()))]
        )
    )
    evaluation = pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        body=b'"safe"',
    )
    scan_task = asyncio.create_task(servicer._evaluate_http_request(evaluation))
    assert await asyncio.to_thread(started.wait, 1)
    try:
        manifest = await asyncio.wait_for(
            asyncio.to_thread(servicer._describe), timeout=0.1
        )
        assert manifest.name == "privacy-guard"
    finally:
        release.set()
    await scan_task
    await servicer.close()


@pytest.mark.asyncio
async def test_cancelled_rpc_holds_scan_slot_until_worker_really_finishes() -> None:
    entered = 0
    first_started = threading.Event()
    release = threading.Event()

    class BlockingScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            nonlocal entered
            entered += 1
            first_started.set()
            release.wait(timeout=2)
            return ()

    servicer = PrivacyGuardMiddleware(
        RequestProcessor(
            [BlockingScanner(ScannerConfig(name="blocking", entity_types=frozenset()))]
        )
    )
    servicer._scan_slots = asyncio.Semaphore(1)
    evaluation = pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS, body=b'"safe"'
    )
    first = asyncio.create_task(servicer._evaluate_http_request(evaluation))
    assert await asyncio.to_thread(first_started.wait, 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(servicer._evaluate_http_request(evaluation))
    await asyncio.sleep(0.05)
    assert entered == 1
    release.set()
    await asyncio.wait_for(second, 1)
    assert entered == 2
    await servicer.close()


@pytest.mark.asyncio
async def test_operational_log_never_contains_body_or_match(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sentinel@example.com"
    evaluation = pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        context=pb2.RequestContext(request_id="request-42"),
        config={"on_finding": {"action": "observe"}},
        body=(f'{{"message":"{sentinel}"}}').encode(),
    )
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )
    servicer = PrivacyGuardMiddleware(RequestProcessor([scanner]))
    with caplog.at_level(logging.INFO, logger="privacy_guard.service.servicer"):
        await servicer._evaluate_rpc(evaluation, UnexpectedAbortContext())
    await servicer.close()

    assert "privacy_guard_evaluation" in caplog.text
    assert sentinel not in caplog.text


def test_operational_log_fields_use_typed_construction() -> None:
    extra = servicer_module._evaluation_log_extra(
        request_id="request-42",
        started=0.0,
        action="allow",
        finding_count=1,
        failure=None,
    )

    request_id: str = extra["request_id"]
    duration_ms: float = extra["duration_ms"]
    action: str = extra["action"]
    finding_count: int = extra["finding_count"]
    error_code: str | None = extra["error_code"]
    assert request_id == "request-42"
    assert duration_ms >= 0
    assert action == "allow"
    assert finding_count == 1
    assert error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MAX_BODY_BYTES, MAX_BODY_BYTES + 1])
async def test_replacement_body_limit_exact_boundary(size: int) -> None:
    class ReplacementProcessor:
        def validate_policy_config(self, policy_config: PolicyConfig) -> None:
            pass

        def process(self, request: InterceptedRequest) -> ProcessingResult:
            return ProcessingResult(
                decision=ProcessingDecision.ALLOW, replacement_body=b"x" * size
            )

    response = await PrivacyGuardMiddleware(
        ReplacementProcessor()
    )._evaluate_http_request(
        pb2.HttpRequestEvaluation(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS, body=b"{}"
        )
    )
    assert response.decision == (
        pb2.DECISION_ALLOW if size == MAX_BODY_BYTES else pb2.DECISION_DENY
    )
    assert response.has_body is (size == MAX_BODY_BYTES)


@pytest.mark.asyncio
@pytest.mark.parametrize(("extra_byte", "allowed"), [(False, True), (True, False)])
async def test_aggregate_finding_encoded_limit_exact_boundary(
    monkeypatch: pytest.MonkeyPatch, extra_byte: bool, allowed: bool
) -> None:
    entity = "x" * (900 + int(extra_byte))
    exact_size = pb2.Finding(
        type="scanner", label="x" * 900, confidence="high", count=1
    ).ByteSize()
    monkeypatch.setattr(servicer_module, "MAX_PROTO_FINDING_BYTES", exact_size)

    class FindingProcessor:
        def validate_policy_config(self, policy_config: PolicyConfig) -> None:
            pass

        def process(self, request: InterceptedRequest) -> ProcessingResult:
            return ProcessingResult(
                decision=ProcessingDecision.ALLOW,
                findings=(
                    RequestBodyFinding(
                        finding=Finding(
                            entity=entity,
                            scanner_name="scanner",
                            start_offset=0,
                            end_offset=1,
                        ),
                        text_block_path="path",
                    ),
                ),
            )

    response = await PrivacyGuardMiddleware(FindingProcessor())._evaluate_http_request(
        pb2.HttpRequestEvaluation(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS, body=b"{}"
        )
    )
    assert response.decision == (pb2.DECISION_ALLOW if allowed else pb2.DECISION_DENY)
    assert len(response.findings) == int(allowed)
