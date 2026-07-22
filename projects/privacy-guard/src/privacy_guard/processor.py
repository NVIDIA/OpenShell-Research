"""Proto-free processing for one complete intercepted request."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from string import Formatter

from privacy_guard.config import (
    ActionConfig,
    BlockActionConfig,
    PolicyConfig,
    RedactActionConfig,
)
from privacy_guard.constants import (
    BLOCK_REASON_CODE,
    CONFIDENCE_RANK,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_FINDINGS_PER_BLOCK,
    MAX_FINDINGS_PER_REQUEST,
    MAX_SCANNED_CHARACTERS,
    MAX_TEXT_BLOCKS,
)
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.request_body import (
    DEFAULT_FORMAT_HANDLERS,
    FormatHandler,
    FormatHandlerContractError,
    RequestBody,
    TextBlock,
)
from privacy_guard.scanners import (
    Finding,
    RequestBodyFinding,
    Scanner,
    ScannerConfig,
    ScannerContractError,
    ScannerFindingLimitExceeded,
)


class RequestProcessor:
    """Coordinate normalization, scanners, policy, and reconstruction per request.

    Processor instances may be invoked concurrently by the service. Every configured
    scanner must therefore make ``scan`` thread-safe and retain no request content.
    """

    def __init__(
        self,
        scanners: Sequence[Scanner[ScannerConfig]],
        format_handlers: Mapping[str, FormatHandler] = DEFAULT_FORMAT_HANDLERS,
    ) -> None:
        scanner_tuple = tuple(scanners)
        if not scanner_tuple:
            raise PrivacyGuardError(ErrorCode.SCANNER_OUTPUT_INVALID)
        names: set[str] = set()
        supported_entities: set[str] = set()
        for item in scanner_tuple:
            if not isinstance(item, Scanner):
                raise PrivacyGuardError(ErrorCode.SCANNER_OUTPUT_INVALID)
            scanner_name = item.scanner_name
            if scanner_name in names:
                raise PrivacyGuardError(ErrorCode.SCANNER_OUTPUT_INVALID)
            names.add(scanner_name)
            supported_entities.update(item.supported_entity_types)
        self._scanners: tuple[Scanner[ScannerConfig], ...] = scanner_tuple
        self._supported_entities = frozenset(supported_entities)
        self._format_handlers: dict[str, FormatHandler] = {}
        for format_name, handler in format_handlers.items():
            if not isinstance(handler, FormatHandler):
                raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID)
            if handler.format_name != format_name:
                raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID)
            self._format_handlers[format_name] = handler

    def validate_policy_config(self, policy_config: PolicyConfig) -> None:
        self._select_handler(policy_config.body_format)
        self._validate_entity_filter(policy_config.on_finding)

    def process(self, request: InterceptedRequest) -> ProcessingResult:
        policy = request.policy_config
        action = policy.on_finding
        handler = self._select_handler(policy.body_format)
        self._validate_entity_filter(action)
        if request.raw_body == b"":
            return ProcessingResult(decision=ProcessingDecision.ALLOW)

        request_body = _normalize_request_body(handler, request)
        verified_body = _validate_normalized_body(request_body, request.raw_body)
        scan_result = self._scan_text_blocks(verified_body.text_blocks, action)
        if scan_result.limit_exceeded:
            return ProcessingResult(
                decision=ProcessingDecision.DENY, reason_code=LIMIT_REASON_CODE
            )
        if isinstance(action, BlockActionConfig) and scan_result.findings:
            return ProcessingResult(
                decision=ProcessingDecision.DENY,
                findings=scan_result.findings,
                reason_code=BLOCK_REASON_CODE,
            )
        if isinstance(action, RedactActionConfig) and any(
            not block.replaceable
            and scan_result.findings_by_text_block_path[block.path]
            for block in verified_body.text_blocks
        ):
            # JSON keys are scanned, but never rewritten: key mutation could collide.
            return ProcessingResult(
                decision=ProcessingDecision.DENY,
                findings=scan_result.findings,
                reason_code=BLOCK_REASON_CODE,
            )

        replacements: dict[str, str] = {}
        redacted_text_bytes = 0
        if isinstance(action, RedactActionConfig):
            for block in verified_body.text_blocks:
                findings = scan_result.findings_by_text_block_path[block.path]
                if findings:
                    redaction = _build_bounded_redaction(
                        block.text,
                        findings,
                        action.template,
                        MAX_BODY_BYTES - redacted_text_bytes,
                    )
                    if redaction is None:
                        return ProcessingResult(
                            decision=ProcessingDecision.DENY,
                            reason_code=LIMIT_REASON_CODE,
                        )
                    replacement, projected_size = redaction
                    redacted_text_bytes += projected_size
                    replacements[block.path] = replacement
        reconstructed = _reconstruct_body(handler, verified_body.source, replacements)
        if len(reconstructed) > MAX_BODY_BYTES:
            return ProcessingResult(
                decision=ProcessingDecision.DENY, reason_code=LIMIT_REASON_CODE
            )
        return ProcessingResult(
            decision=ProcessingDecision.ALLOW,
            replacement_body=reconstructed
            if reconstructed != request.raw_body
            else None,
            findings=scan_result.findings,
        )

    def _select_handler(self, requested_name: str) -> FormatHandler:
        try:
            handler = self._format_handlers[requested_name]
        except KeyError:
            raise PrivacyGuardError(ErrorCode.BODY_FORMAT_UNSUPPORTED) from None
        return handler

    def _validate_entity_filter(self, action: ActionConfig) -> None:
        if not action.entity_types:
            return
        if not action.entity_types.issubset(self._supported_entities):
            raise PrivacyGuardError(ErrorCode.CONFIG_INVALID)

    def _scan_and_validate(
        self, scanner: Scanner[ScannerConfig], text_block: TextBlock
    ) -> tuple[Finding, ...]:
        try:
            result = scanner.scan(text_block.text)
        except ScannerFindingLimitExceeded:
            raise PrivacyGuardError(ErrorCode.FINDING_LIMIT_EXCEEDED) from None
        except ScannerContractError:
            raise PrivacyGuardError(ErrorCode.SCANNER_OUTPUT_INVALID) from None
        except Exception:
            raise PrivacyGuardError(ErrorCode.SCANNER_EXECUTION_FAILED) from None
        for finding in result:
            if (
                finding.scanner_name != scanner.scanner_name
                or finding.entity not in scanner.supported_entity_types
                or finding.end_offset > len(text_block.text)
            ):
                raise PrivacyGuardError(ErrorCode.SCANNER_OUTPUT_INVALID)
        return tuple(
            sorted(result, key=lambda item: (item.start_offset, item.end_offset))
        )

    def _scan_text_blocks(
        self, text_blocks: tuple[TextBlock, ...], action: ActionConfig
    ) -> _ScanResult:
        by_path: dict[str, tuple[Finding, ...]] = {}
        all_findings: list[RequestBodyFinding] = []
        enabled = action.entity_types
        threshold = (
            None
            if action.minimum_confidence is None
            else CONFIDENCE_RANK[action.minimum_confidence]
        )
        for block in text_blocks:
            block_findings: list[Finding] = []
            try:
                for scanner in self._scanners:
                    for finding in self._scan_and_validate(scanner, block):
                        if (enabled is None or finding.entity in enabled) and (
                            threshold is None
                            or CONFIDENCE_RANK[finding.confidence] >= threshold
                        ):
                            block_findings.append(finding)
                            if (
                                len(block_findings) > MAX_FINDINGS_PER_BLOCK
                                or len(all_findings) + len(block_findings)
                                > MAX_FINDINGS_PER_REQUEST
                            ):
                                return _ScanResult((), {}, True)
            except PrivacyGuardError as error:
                if error.code is ErrorCode.FINDING_LIMIT_EXCEEDED:
                    return _ScanResult((), {}, True)
                raise
            ordered = tuple(
                sorted(
                    block_findings,
                    key=lambda item: (
                        item.start_offset,
                        item.end_offset,
                        item.scanner_name,
                        item.entity,
                    ),
                )
            )
            by_path[block.path] = ordered
            all_findings.extend(
                RequestBodyFinding(finding=finding, text_block_path=block.path)
                for finding in ordered
            )
        return _ScanResult(tuple(all_findings), by_path)


@dataclass(frozen=True)
class _ScanResult:
    findings: tuple[RequestBodyFinding, ...]
    findings_by_text_block_path: Mapping[str, tuple[Finding, ...]]
    limit_exceeded: bool = False


@dataclass(frozen=True)
class _VerifiedRequestBody:
    """A body paired with the processor-validated request-relative view."""

    source: RequestBody
    text_blocks: tuple[TextBlock, ...]


def _normalize_request_body(
    handler: FormatHandler, request: InterceptedRequest
) -> RequestBody:
    try:
        return handler.normalize(request.raw_body, request.policy_config)
    except FormatHandlerContractError:
        raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID) from None
    except PrivacyGuardError:
        raise
    except Exception:
        raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_EXECUTION_FAILED) from None


def _validate_normalized_body(
    request_body: RequestBody, original_body: bytes
) -> _VerifiedRequestBody:
    if request_body.original_bytes != original_body:
        raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID)
    text_blocks = request_body.text_blocks
    if len(text_blocks) > MAX_TEXT_BLOCKS:
        raise PrivacyGuardError(ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED)

    seen_paths: set[str] = set()
    total_characters = 0
    for text_block in text_blocks:
        if text_block.path in seen_paths:
            raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID)
        seen_paths.add(text_block.path)
        total_characters += len(text_block.text)
        if total_characters > MAX_SCANNED_CHARACTERS:
            raise PrivacyGuardError(ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED)
    return _VerifiedRequestBody(source=request_body, text_blocks=text_blocks)


def _resolve_overlaps(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Choose deterministic redaction winners; observation retains all findings."""
    winners: list[Finding] = []
    ranked = sorted(
        findings,
        key=lambda item: (
            -CONFIDENCE_RANK[item.confidence],
            -(item.end_offset - item.start_offset),
            item.start_offset,
            item.end_offset,
            item.scanner_name,
            item.entity,
        ),
    )
    for candidate in ranked:
        if all(
            candidate.end_offset <= winner.start_offset
            or candidate.start_offset >= winner.end_offset
            for winner in winners
        ):
            winners.append(candidate)
    return tuple(sorted(winners, key=lambda item: (item.start_offset, item.end_offset)))


def _redact_text(
    original_text: str, findings: tuple[Finding, ...], template: str
) -> str:
    parts: list[str] = []
    cursor = 0
    for finding in findings:
        parts.append(original_text[cursor : finding.start_offset])
        parts.append(template.format(entity=finding.entity))
        cursor = finding.end_offset
    parts.append(original_text[cursor:])
    return "".join(parts)


def _formatted_template_size(template: str, entity: str) -> int:
    """Return UTF-8 size without constructing a potentially large rendering."""
    size = 0
    entity_size = len(entity.encode("utf-8"))
    for literal, field_name, _, _ in Formatter().parse(template):
        size += len(literal.encode("utf-8"))
        if field_name is not None:
            size += entity_size
    return size


def _project_redacted_text_size(
    original_text: str,
    findings: tuple[Finding, ...],
    template: str,
    remaining_bytes: int,
) -> int | None:
    """Bound rendered text before allocating it; serialization is checked later."""
    size = 0
    cursor = 0
    for finding in findings:
        size += len(original_text[cursor : finding.start_offset].encode("utf-8"))
        size += _formatted_template_size(template, finding.entity)
        if size > remaining_bytes:
            return None
        cursor = finding.end_offset
    size += len(original_text[cursor:].encode("utf-8"))
    return size if size <= remaining_bytes else None


def _build_bounded_redaction(
    original_text: str,
    findings: tuple[Finding, ...],
    template: str,
    remaining_bytes: int,
) -> tuple[str, int] | None:
    """Resolve overlaps once, then render only bounded intermediate text."""
    resolved = _resolve_overlaps(findings)
    projected_size = _project_redacted_text_size(
        original_text, resolved, template, remaining_bytes
    )
    if projected_size is None:
        return None
    return _redact_text(original_text, resolved, template), projected_size


def _reconstruct_body(
    handler: FormatHandler, request_body: RequestBody, replacements: Mapping[str, str]
) -> bytes:
    try:
        return handler.reconstruct(request_body, replacements)
    except FormatHandlerContractError:
        raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID) from None
    except PrivacyGuardError:
        raise
    except Exception:
        raise PrivacyGuardError(ErrorCode.FORMAT_HANDLER_EXECUTION_FAILED) from None
