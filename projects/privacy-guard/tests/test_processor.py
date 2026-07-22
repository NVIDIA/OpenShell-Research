import math
from collections.abc import Mapping

import pytest
from pydantic import ValidationError
from typing_extensions import override

from privacy_guard.config import PolicyAction, PolicyConfig
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.processor import RequestProcessor
from privacy_guard.request_body import FormatHandler, RequestBody, TextBlock
from privacy_guard.scanners import (
    Finding,
    RequestBodyFinding,
    ScanBudget,
    ScanBudgetExceeded,
    Scanner,
    ScannerConfig,
    ScannerContractError,
    ScannerFindingLimitExceeded,
    parse_scanner_output,
)


class RecordingScanner(Scanner[ScannerConfig]):
    def __init__(
        self, findings_by_text: Mapping[str, tuple[Finding, ...]] | None = None
    ) -> None:
        self.findings_by_text = findings_by_text or {}
        entity_types = frozenset(
            finding.entity
            for findings in self.findings_by_text.values()
            for finding in findings
        )
        super().__init__(ScannerConfig(name="recording", entity_types=entity_types))
        self.calls: list[str] = []

    @override
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        self.calls.append(text_block)
        return self.findings_by_text.get(text_block, ())


class RecordingHandler(FormatHandler):
    def __init__(
        self,
        text_blocks: tuple[TextBlock, ...] = (),
        reconstructed: bytes | None = None,
    ) -> None:
        super().__init__(format_name="opaque")
        self.text_blocks = text_blocks
        self.reconstructed = reconstructed
        self.normalize_calls = 0
        self.reconstruct_calls = 0
        self.replacements: dict[str, str] | None = None

    @override
    def _normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
        self.normalize_calls += 1
        return RequestBody(
            text_blocks=self.text_blocks,
            parsed_value=None,
            original_bytes=raw_body,
        )

    @override
    def _reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        self.reconstruct_calls += 1
        self.replacements = dict(replacements_by_path)
        return (
            request_body.original_bytes
            if self.reconstructed is None
            else self.reconstructed
        )


def _config(
    *,
    body_format: str = "opaque",
    action_kind: str = "redact",
    entity_types: list[str] | None = None,
    minimum_confidence: str | None = None,
    template: str = "[{entity}]",
) -> PolicyConfig:
    on_finding: dict[str, object] = {
        "action": action_kind,
        "entity_types": entity_types,
        "minimum_confidence": minimum_confidence,
    }
    if action_kind == PolicyAction.REDACT.value:
        on_finding["template"] = template
    return PolicyConfig.from_mapping(
        {"body_format": body_format, "on_finding": on_finding}
    )


def _request(
    raw_body: bytes = b"body", policy_config: PolicyConfig | None = None
) -> InterceptedRequest:
    return InterceptedRequest(
        raw_body=raw_body,
        content_type="application/test",
        policy_config=policy_config or _config(),
    )


def _processor(
    scanner: Scanner[ScannerConfig], handler: FormatHandler
) -> RequestProcessor:
    return RequestProcessor([scanner], {handler.format_name: handler})


def _finding(
    start: int,
    end: int,
    *,
    entity: str = "secret",
    scanner_name: str = "recording",
) -> Finding:
    return Finding(
        entity=entity,
        scanner_name=scanner_name,
        start_offset=start,
        end_offset=end,
    )


def _request_body_finding(
    start: int,
    end: int,
    *,
    entity: str = "secret",
    scanner_name: str = "recording",
    text_block_path: str,
) -> RequestBodyFinding:
    return RequestBodyFinding(
        finding=_finding(start, end, entity=entity, scanner_name=scanner_name),
        text_block_path=text_block_path,
    )


def test_scanner_visits_each_text_block_and_reconstructs_once() -> None:
    scanner = RecordingScanner()
    handler = RecordingHandler(
        (
            TextBlock(path="text_block::alpha", text="first"),
            TextBlock(path="text_block::beta", text="second"),
        )
    )

    result = _processor(scanner, handler).process(_request())

    assert result == ProcessingResult(decision=ProcessingDecision.ALLOW)
    assert scanner.calls == ["first", "second"]
    assert handler.normalize_calls == 1
    assert handler.reconstruct_calls == 1
    assert handler.replacements == {}


def test_empty_scanner_sequence_is_rejected_at_construction() -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        RequestProcessor([])

    assert exception_info.value.code is ErrorCode.SCANNER_OUTPUT_INVALID


@pytest.mark.parametrize(
    "timeout",
    [True, False, 0, -1, math.inf, math.nan, 31],
)
def test_processor_rejects_invalid_scan_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        RequestProcessor([RecordingScanner()], scan_timeout_seconds=timeout)


def test_one_scan_budget_is_shared_across_blocks_and_exhaustion_discards_findings() -> (
    None
):
    class BudgetScanner(Scanner[ScannerConfig]):
        def __init__(self) -> None:
            super().__init__(
                ScannerConfig(name="budget", entity_types=frozenset({"secret"}))
            )
            self.budgets: list[ScanBudget] = []

        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            self.budgets.append(budget)
            if text_block == "second":
                raise ScanBudgetExceeded
            return (
                Finding(
                    entity="secret",
                    scanner_name=self.scanner_name,
                    start_offset=0,
                    end_offset=1,
                ),
            )

    scanner = BudgetScanner()
    handler = RecordingHandler(
        (
            TextBlock(path="first", text="first"),
            TextBlock(path="second", text="second"),
        )
    )

    result = _processor(scanner, handler).process(
        _request(policy_config=_config(action_kind=PolicyAction.OBSERVE.value))
    )

    assert result.decision is ProcessingDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert result.findings == ()
    assert len(scanner.budgets) == 2
    assert scanner.budgets[0] is scanner.budgets[1]


def test_duplicate_scanner_names_are_rejected_at_construction() -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        RequestProcessor([RecordingScanner(), RecordingScanner()])

    assert exception_info.value.code is ErrorCode.SCANNER_OUTPUT_INVALID


def test_scanner_infers_validates_and_uses_concrete_config_type() -> None:
    class PrefixScannerConfig(ScannerConfig):
        finding_entity: str

    class PrefixScanner(Scanner[PrefixScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                Finding(
                    entity=self.config.finding_entity,
                    scanner_name=self.scanner_name,
                    start_offset=0,
                    end_offset=1,
                ),
            )

    config = PrefixScannerConfig(
        name="prefix",
        entity_types=frozenset({"custom"}),
        finding_entity="custom",
    )
    scanner = PrefixScanner(config)
    RequestProcessor([scanner])

    assert PrefixScanner.get_config_type() is PrefixScannerConfig
    assert scanner.config is config
    assert scanner.config.finding_entity == "custom"
    assert scanner.supported_entity_types is scanner.config.entity_types
    assert scanner.scan("x")[0].entity == "custom"
    pytest.raises(
        ScannerContractError,
        PrefixScanner,
        ScannerConfig(name="wrong-config-type", entity_types=frozenset()),
    )


def test_empty_supported_entity_catalog_rejects_enabled_filter() -> None:
    processor = RequestProcessor([RecordingScanner()])

    with pytest.raises(PrivacyGuardError) as exception_info:
        processor.validate_policy_config(
            PolicyConfig.from_mapping(
                {"on_finding": {"action": "observe", "entity_types": ["email"]}}
            )
        )

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_handler_registry_key_must_match_immutable_identity() -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        RequestProcessor([RecordingScanner()], {"wrong": RecordingHandler()})

    assert exception_info.value.code is ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID


def test_malformed_normalize_output_maps_to_handler_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = RecordingHandler()
    monkeypatch.setattr(handler, "_normalize", lambda raw_body, policy: object())

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(RecordingScanner(), handler).process(_request())

    assert exception_info.value.code is ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID


def test_non_bytes_reconstruct_output_maps_to_handler_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = RecordingHandler()
    monkeypatch.setattr(
        handler,
        "_reconstruct",
        lambda request_body, replacements: bytearray(b"body"),
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(RecordingScanner(), handler).process(_request())

    assert exception_info.value.code is ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID


def test_validate_policy_config_selects_and_validates_without_processing() -> None:
    handler = RecordingHandler()
    processor = _processor(RecordingScanner(), handler)

    processor.validate_policy_config(_config())

    assert handler.normalize_calls == 0
    assert handler.reconstruct_calls == 0


def test_bodyless_request_skips_normalization_scanning_and_reconstruction() -> None:
    scanner = RecordingScanner()
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="not used"),))

    result = _processor(scanner, handler).process(_request(b""))

    assert result == ProcessingResult(decision=ProcessingDecision.ALLOW)
    assert handler.normalize_calls == 0
    assert scanner.calls == []
    assert handler.reconstruct_calls == 0


def test_empty_json_object_scans_nothing_and_reconstructs_once() -> None:
    scanner = RecordingScanner()
    processor = RequestProcessor([scanner])
    request = InterceptedRequest(
        raw_body=b"{}",
        content_type="application/json",
        policy_config=PolicyConfig(),
    )

    result = processor.process(request)

    assert result == ProcessingResult(decision=ProcessingDecision.ALLOW)
    assert scanner.calls == []


def test_observe_attaches_paths_without_mutating_body() -> None:
    original = _finding(1, 3)
    scanner = RecordingScanner({"abcd": (original,)})
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="abcd"),))

    result = _processor(scanner, handler).process(
        _request(policy_config=_config(action_kind=PolicyAction.OBSERVE.value))
    )

    assert result.decision is ProcessingDecision.ALLOW
    assert result.replacement_body is None
    assert result.findings == (
        _request_body_finding(1, 3, text_block_path="text_block::alpha"),
    )
    assert not hasattr(original, "text_block_path")
    assert handler.replacements == {}


@pytest.mark.parametrize(
    ("text", "findings", "template", "expected"),
    [
        ("abcde", (_finding(1, 3),), "X", "aXde"),
        (
            "abcdef",
            (_finding(3, 5, entity="b"), _finding(0, 2, entity="a")),
            "[{entity}]",
            "[a]c[b]f",
        ),
        ("a🐍éz", (_finding(1, 3),), "X", "aXz"),
    ],
)
def test_redact_replaces_sorted_non_overlapping_character_spans(
    text: str, findings: tuple[Finding, ...], template: str, expected: str
) -> None:
    scanner = RecordingScanner({text: findings})
    handler = RecordingHandler(
        (TextBlock(path="text_block::alpha", text=text),), b"changed"
    )

    result = _processor(scanner, handler).process(
        _request(policy_config=_config(template=template))
    )

    assert result.replacement_body == b"changed"
    assert handler.replacements == {"text_block::alpha": expected}


def test_static_redaction_template_is_supported() -> None:
    scanner = RecordingScanner({"secret": (_finding(0, 6),)})
    handler = RecordingHandler(
        (TextBlock(path="text_block::alpha", text="secret"),), b"x"
    )

    _processor(scanner, handler).process(
        _request(policy_config=_config(template="[redacted]"))
    )

    assert handler.replacements == {"text_block::alpha": "[redacted]"}


def test_redaction_expansion_is_denied_before_render_or_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.processor as processor_module

    scanner = RecordingScanner({"ab": (_finding(0, 1), _finding(1, 2))})
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="ab"),))
    monkeypatch.setattr(processor_module, "MAX_BODY_BYTES", 8)

    def unexpected_render(*args: object) -> str:
        raise AssertionError("redaction must not be rendered after preflight denial")

    monkeypatch.setattr(processor_module, "_redact_text", unexpected_render)
    result = _processor(scanner, handler).process(
        _request(policy_config=_config(template="x" * 64))
    )

    assert result.decision is ProcessingDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert handler.reconstruct_calls == 0


def test_serialized_redaction_expansion_is_denied_after_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.processor as processor_module

    scanner = RecordingScanner({"x": (_finding(0, 1),)})
    monkeypatch.setattr(processor_module, "MAX_BODY_BYTES", 8)

    result = RequestProcessor([scanner]).process(
        InterceptedRequest(
            raw_body=b'"x"',
            policy_config=_config(body_format="json", template='"' * 6),
        )
    )

    assert result.decision is ProcessingDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert result.replacement_body is None


def test_block_denies_with_findings_and_suppresses_reconstruction() -> None:
    scanner = RecordingScanner({"secret": (_finding(0, 6),)})
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="secret"),))
    config = _config(action_kind=PolicyAction.BLOCK.value)

    result = _processor(scanner, handler).process(_request(policy_config=config))

    assert result.decision is ProcessingDecision.DENY
    assert result.replacement_body is None
    assert result.reason_code == "privacy_guard_blocked"
    assert len(result.findings) == 1
    assert handler.reconstruct_calls == 0


def test_block_allows_and_reconstructs_when_no_findings_exist() -> None:
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="safe"),))

    result = _processor(RecordingScanner(), handler).process(
        _request(policy_config=_config(action_kind=PolicyAction.BLOCK.value))
    )

    assert result == ProcessingResult(decision=ProcessingDecision.ALLOW)
    assert handler.reconstruct_calls == 1


def test_findings_keep_scanner_identity_and_text_block_order_then_span_order() -> None:
    scanner = RecordingScanner(
        {
            "first": (
                _finding(3, 5, entity="late"),
                _finding(0, 2, entity="early"),
            ),
            "second": (_finding(1, 3, entity="second"),),
        }
    )
    handler = RecordingHandler(
        (
            TextBlock(path="text_block::one", text="first"),
            TextBlock(path="text_block::two", text="second"),
        )
    )

    result = _processor(scanner, handler).process(
        _request(policy_config=_config(action_kind=PolicyAction.OBSERVE.value))
    )

    assert [
        (
            request_body_finding.finding.entity,
            request_body_finding.finding.scanner_name,
            request_body_finding.text_block_path,
        )
        for request_body_finding in result.findings
    ] == [
        ("early", "recording", "text_block::one"),
        ("late", "recording", "text_block::one"),
        ("second", "recording", "text_block::two"),
    ]


@pytest.mark.parametrize(
    "normalized",
    [
        RequestBody(
            text_blocks=(
                TextBlock(path="same", text="a"),
                TextBlock(path="same", text="b"),
            ),
            parsed_value=None,
            original_bytes=b"body",
        ),
        RequestBody(text_blocks=(), parsed_value=None, original_bytes=b"different"),
    ],
)
def test_contextually_invalid_normalized_body_is_rejected_before_scanning(
    normalized: RequestBody,
) -> None:
    scanner = RecordingScanner()

    class ReturningHandler(RecordingHandler):
        @override
        def _normalize(
            self, raw_body: bytes, policy_config: PolicyConfig
        ) -> RequestBody:
            self.normalize_calls += 1
            return normalized

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(scanner, ReturningHandler()).process(_request())

    assert exception_info.value.code is ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID
    assert scanner.calls == []


@pytest.mark.parametrize("scanner_result", [[], (object(),)])
def test_invalid_scanner_output_shape_is_rejected(scanner_result: object) -> None:
    with pytest.raises(ScannerContractError):
        parse_scanner_output(scanner_result)


def test_request_body_finding_is_not_valid_scanner_output() -> None:
    finding = _finding(0, 1)
    request_body_finding = RequestBodyFinding(finding=finding, text_block_path="path")

    with pytest.raises(ScannerContractError):
        parse_scanner_output((request_body_finding,))


def test_exact_finding_instance_is_reused_by_output_validation() -> None:
    finding = _finding(0, 1)

    parsed = parse_scanner_output((finding,))

    assert parsed[0] is finding


def test_scanner_output_limit_is_checked_before_element_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_guard.scanners.base as scanner_module

    monkeypatch.setattr(scanner_module, "MAX_FINDINGS_PER_BLOCK", 1)

    with pytest.raises(ScannerFindingLimitExceeded):
        parse_scanner_output((object(), object()))


@pytest.mark.parametrize(
    "overrides",
    [
        {"entity": ""},
        {"scanner_name": ""},
        {"entity": "bad\ud800"},
        {"scanner_name": "recording\ud800"},
        {"start_offset": True},
        {"start_offset": -1},
        {"start_offset": 1, "end_offset": 1},
        {"confidence": "high"},
    ],
)
def test_invalid_finding_fields_fail_at_model_construction(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "entity": "secret",
        "scanner_name": "recording",
        "start_offset": 0,
        "end_offset": 1,
        **overrides,
    }
    with pytest.raises(ValidationError):
        Finding.model_validate(values)


@pytest.mark.parametrize(
    "finding",
    [
        _finding(0, 1, scanner_name="other"),
        _finding(1, 5),
    ],
)
def test_contextually_invalid_finding_is_rejected(finding: Finding) -> None:
    scanner = RecordingScanner({"abcd": (finding,)})
    handler = RecordingHandler((TextBlock(path="text_block::alpha", text="abcd"),))

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(scanner, handler).process(_request())

    assert exception_info.value.code is ErrorCode.SCANNER_OUTPUT_INVALID
    assert handler.reconstruct_calls == 0


def test_finding_entity_must_be_declared_by_scanner_config() -> None:
    class UndeclaredEntityScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return (
                _finding(0, 1, entity="undeclared", scanner_name=self.scanner_name),
            )

    scanner = UndeclaredEntityScanner(
        ScannerConfig(name="declared", entity_types=frozenset({"declared"}))
    )
    handler = RecordingHandler((TextBlock(path="path", text="x"),))

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(scanner, handler).process(_request())

    assert exception_info.value.code is ErrorCode.SCANNER_OUTPUT_INVALID


def test_adjacent_scanner_findings_are_accepted() -> None:
    scanner = RecordingScanner({"abcd": (_finding(0, 2), _finding(2, 4))})
    handler = RecordingHandler(
        (TextBlock(path="text_block::alpha", text="abcd"),), b"x"
    )

    result = _processor(scanner, handler).process(_request())

    assert result.decision is ProcessingDecision.ALLOW
    assert len(result.findings) == 2


@pytest.mark.parametrize(
    ("collaborator", "expected_code"),
    [
        ("scanner", ErrorCode.SCANNER_EXECUTION_FAILED),
        ("handler", ErrorCode.FORMAT_HANDLER_EXECUTION_FAILED),
    ],
)
def test_collaborator_exceptions_are_replaced_without_partial_result_or_content(
    collaborator: str, expected_code: ErrorCode
) -> None:
    sentinel = "sensitive-collaborator-exception-8472"

    class RaisingScanner(Scanner[ScannerConfig]):
        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            raise RuntimeError(sentinel)

    class RaisingHandler(RecordingHandler):
        @override
        def _normalize(
            self, raw_body: bytes, policy_config: PolicyConfig
        ) -> RequestBody:
            raise RuntimeError(sentinel)

    scanner: Scanner[ScannerConfig] = (
        RaisingScanner(ScannerConfig(name="raising", entity_types=frozenset()))
        if collaborator == "scanner"
        else RecordingScanner()
    )
    handler: FormatHandler = (
        RaisingHandler()
        if collaborator == "handler"
        else RecordingHandler((TextBlock(path="text_block::alpha", text="text"),))
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(scanner, handler).process(_request())

    assert exception_info.value.code is expected_code
    assert exception_info.value.__cause__ is None
    assert sentinel not in str(exception_info.value)
    assert sentinel not in repr(exception_info.value)


def test_cataloged_handler_error_propagates_unchanged() -> None:
    expected = PrivacyGuardError(ErrorCode.BODY_JSON_INVALID)

    class CatalogRaisingHandler(RecordingHandler):
        @override
        def _normalize(
            self, raw_body: bytes, policy_config: PolicyConfig
        ) -> RequestBody:
            raise expected

    with pytest.raises(PrivacyGuardError) as exception_info:
        _processor(RecordingScanner(), CatalogRaisingHandler()).process(_request())

    assert exception_info.value is expected


def test_reconstruction_equal_to_original_returns_no_replacement() -> None:
    handler = RecordingHandler(
        (TextBlock(path="text_block::alpha", text="text"),), reconstructed=b"body"
    )

    result = _processor(RecordingScanner(), handler).process(_request(b"body"))

    assert result.replacement_body is None


def test_processing_result_is_immutable() -> None:
    result = ProcessingResult(decision=ProcessingDecision.ALLOW)

    with pytest.raises(ValidationError):
        setattr(result, "decision", ProcessingDecision.DENY)
