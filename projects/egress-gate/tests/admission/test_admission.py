"""Conformance tests for rendered-prompt admission and attested egress."""

from __future__ import annotations

import json

from egress_gate.admission import (
    RECEIPT_HEADER,
    AdmissionDecision,
    AdmissionHook,
    AttestedEgressProcessor,
    HarnessAdmissionContext,
    HarnessAdmissionProcessor,
    HarnessAdmissionRequest,
    PiInputV1,
    PromptProvenance,
    ReceiptAuthority,
    canonical_json_bytes,
    create_pi_adapter_registry,
    create_provider_adapter_registry,
)
from egress_gate.gates import create_builtin_registry
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.timeout import Timeout

DENY_MARKER = "OPEN_SHELL_ADMISSION_DENY_TEST"
REPLACE_MARKER = "OPEN_SHELL_ADMISSION_REPLACE_TEST"


def _processors() -> tuple[HarnessAdmissionProcessor, AttestedEgressProcessor]:
    registry = create_builtin_registry()
    config = registry.validate_config(
        {
            "gates": [
                {
                    "name": "deny-marker",
                    "kind": "regex",
                    "scan": {"kind": "body", "action": {"kind": "deny"}},
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": "unsafe-marker",
                                "rules": [
                                    {
                                        "name": "exact-marker",
                                        "pattern": DENY_MARKER,
                                        "confidence": "high",
                                    }
                                ],
                            }
                        ]
                    },
                },
                {
                    "name": "replace-marker",
                    "kind": "regex",
                    "scan": {
                        "kind": "body",
                        "action": {"kind": "replace", "template": "[REDACTED]"},
                    },
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": "replacement-marker",
                                "rules": [
                                    {
                                        "name": "exact-marker",
                                        "pattern": REPLACE_MARKER,
                                        "confidence": "high",
                                    }
                                ],
                            }
                        ]
                    },
                },
            ],
            "default_decision": "allow",
        }
    )
    request_processor = registry.prepare_processor(
        config, timeout=Timeout.from_seconds(1)
    )
    authority = ReceiptAuthority(lifetime_seconds=30)
    return (
        HarnessAdmissionProcessor(
            request_processor, create_pi_adapter_registry(), authority
        ),
        AttestedEgressProcessor(
            request_processor,
            create_provider_adapter_registry(),
            authority,
            middleware_name="pi-egress",
            harness_version="extension-v1",
        ),
    )


def _target() -> HttpTarget:
    return HttpTarget(
        scheme="https",
        host="provider.test",
        port=443,
        method="POST",
        path="/v1/chat/completions",
        query="",
    )


def _admit(processor: HarnessAdmissionProcessor, text: str):
    body = canonical_json_bytes(
        PiInputV1(schema_version="openshell.pi-input.v1", text=text)
    )
    return body, _admit_body(processor, body)


def _admit_body(
    processor: HarnessAdmissionProcessor,
    body: bytes,
    *,
    timeout: Timeout | None = None,
):
    result = processor.process(
        HarnessAdmissionRequest(
            request_body=body,
            provenance=PromptProvenance(
                kind="rendered_prompt",
                session_id="session-1",
                submission_id="submission-1",
            ),
        ),
        HarnessAdmissionContext(
            request_id="admission-1",
            sandbox_id="sandbox-1",
            middleware_name="pi-egress",
            harness="pi",
            harness_version="extension-v1",
            hook=AdmissionHook.RENDERED_PROMPT,
            schema_version="openshell.pi-input.v1",
            provider_target=_target(),
            provider_adapter_schema="openai.chat-completions.v1",
        ),
        timeout=timeout or Timeout.from_seconds(1),
    )
    return result


def _provider_request(prompt: str, receipt: bytes | None) -> HttpRequest:
    body = json.dumps(
        {
            "model": "fixture-model",
            "messages": [
                {"role": "system", "content": "fixture system prompt"},
                {"role": "user", "content": prompt},
            ],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0,
            "max_completion_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
            "store": False,
            "prompt_cache_key": "session-1",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    headers = [HttpHeader(name="content-type", value="application/json")]
    if receipt is not None:
        headers.append(HttpHeader(name=RECEIPT_HEADER, value=receipt.decode("ascii")))
    return HttpRequest(
        context=RequestContext(request_id="network-1", sandbox_id="sandbox-1"),
        target=_target(),
        headers=tuple(headers),
        body=body,
    )


def test_safe_rendered_prompt_receipt_authorizes_first_request_and_is_stripped() -> (
    None
):
    admission, egress = _processors()
    _, admitted = _admit(admission, "safe rendered prompt")

    assert admitted.decision is AdmissionDecision.ALLOW
    assert admitted.receipt is not None
    result = egress.process(
        _provider_request("safe rendered prompt", admitted.receipt),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision.value == "allow"
    assert [
        mutation.name for mutation in result.request_mutations.header_mutations
    ] == [RECEIPT_HEADER]


def test_rendered_prompt_receipt_is_consumed_after_first_request() -> None:
    admission, egress = _processors()
    _, admitted = _admit(admission, "safe rendered prompt")
    assert admitted.receipt is not None
    request = _provider_request("safe rendered prompt", admitted.receipt)

    first = egress.process(request, timeout=Timeout.from_seconds(1))
    replay = egress.process(request, timeout=Timeout.from_seconds(1))

    assert first.decision.value == "allow"
    assert replay.decision.value == "deny"
    assert replay.reason_code == "receipt_replayed"


def test_denial_returns_no_receipt_or_replacement() -> None:
    admission, _ = _processors()
    _, denied = _admit(admission, f"do not persist {DENY_MARKER}")

    assert denied.decision is AdmissionDecision.DENY
    assert denied.receipt is None
    assert denied.replacement_body is None


def test_redaction_receipt_binds_only_the_replacement() -> None:
    admission, egress = _processors()
    original = f"hide {REPLACE_MARKER} please"
    _, admitted = _admit(admission, original)

    assert admitted.decision is AdmissionDecision.REPLACE
    assert admitted.receipt is not None
    assert admitted.replacement_body is not None
    replacement = PiInputV1.model_validate_json(
        admitted.replacement_body, strict=True
    ).text
    assert replacement == "hide [REDACTED] please"
    assert (
        egress.process(
            _provider_request(original, admitted.receipt),
            timeout=Timeout.from_seconds(1),
        ).reason_code
        == "receipt_context_mismatch"
    )
    assert (
        egress.process(
            _provider_request(replacement, admitted.receipt),
            timeout=Timeout.from_seconds(1),
        ).decision.value
        == "allow"
    )


def test_changed_prompt_and_unattested_continuation_fail_closed() -> None:
    admission, egress = _processors()
    _, admitted = _admit(admission, "safe rendered prompt")
    assert admitted.receipt is not None

    changed = egress.process(
        _provider_request("changed prompt", admitted.receipt),
        timeout=Timeout.from_seconds(1),
    )
    continuation = egress.process(
        _provider_request("safe rendered prompt", None),
        timeout=Timeout.from_seconds(1),
    )

    assert changed.reason_code == "receipt_context_mismatch"
    assert continuation.reason_code == "receipt_missing"


def test_malformed_and_duplicate_admission_json_are_contract_errors() -> None:
    admission, _ = _processors()

    malformed = _admit_body(admission, b"{")
    duplicate = _admit_body(
        admission,
        b'{"schema_version":"openshell.pi-input.v1",'
        b'"schema_version":"openshell.pi-input.v1","text":"safe"}',
    )

    assert malformed.reason_code == "admission_contract_invalid"
    assert duplicate.reason_code == "admission_contract_invalid"


def test_admission_json_limits_and_deadlines_remain_availability_errors() -> None:
    admission, _ = _processors()
    over_depth = b"[" * 129 + b"0" + b"]" * 129

    limited = _admit_body(admission, over_depth)
    expired = _admit_body(admission, b"{}", timeout=Timeout(deadline=0.0))

    assert limited.reason_code == "admission_unavailable"
    assert expired.reason_code == "admission_unavailable"


def test_provider_malformed_json_is_an_unsupported_shape() -> None:
    admission, egress = _processors()
    _, admitted = _admit(admission, "safe rendered prompt")
    assert admitted.receipt is not None
    malformed = _provider_request("safe rendered prompt", admitted.receipt).model_copy(
        update={"body": b"{"}
    )

    result = egress.process(malformed, timeout=Timeout.from_seconds(1))

    assert result.reason_code == "provider_shape_unsupported"


def test_direct_openai_reasoning_effort_is_supported() -> None:
    admission, egress = _processors()
    _, admitted = _admit(admission, "safe rendered prompt")
    assert admitted.receipt is not None
    request = _provider_request("safe rendered prompt", admitted.receipt)
    provider_body = json.loads(request.body)
    provider_body["reasoning_effort"] = "medium"
    request = request.model_copy(
        update={
            "body": json.dumps(
                provider_body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    result = egress.process(request, timeout=Timeout.from_seconds(1))

    assert result.decision.value == "allow"
