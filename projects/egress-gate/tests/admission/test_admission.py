# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conformance tests for managed Pi context admission and attested egress."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from egress_gate.admission import (
    MAX_ADMISSION_BODY_BYTES,
    RECEIPT_HEADER,
    AdmissionDecision,
    AdmissionHook,
    AdmissionProvenance,
    AttestedEgressProcessor,
    HarnessAdmissionContext,
    HarnessAdmissionProcessor,
    HarnessAdmissionRequest,
    PiMessageV1,
    PiTextContentV1,
    PiToolResultV1,
    ReceiptAuthority,
    canonical_json_bytes,
    create_pi_adapter_registry,
    create_provider_adapter_registry,
)
from egress_gate.gates import create_builtin_registry
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.timeout import Timeout

DENY_TEXT = "DENY_THIS"
REDACT_TEXT = "REDACT_THIS"
_PI_RESPONSES_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/pi-openai-responses.json").read_text()
)
_PI_CHAT_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/pi-openai-completions.json").read_text()
)
# These payloads were captured at Pi's fake-fetch boundary from its native
# openai-responses and openai-completions stream functions. They intentionally
# preserve the serializer output rather than restating it through test builders.


def _processors(
    *, replacement_template: str = "[REDACTED]"
) -> tuple[HarnessAdmissionProcessor, AttestedEgressProcessor, ReceiptAuthority]:
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
                                        "pattern": DENY_TEXT,
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
                        "action": {
                            "kind": "replace",
                            "template": replacement_template,
                        },
                    },
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": "replacement-marker",
                                "rules": [
                                    {
                                        "name": "exact-marker",
                                        "pattern": REDACT_TEXT,
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
    authority = ReceiptAuthority()
    return (
        HarnessAdmissionProcessor(
            request_processor, create_pi_adapter_registry(), authority
        ),
        AttestedEgressProcessor(
            request_processor,
            create_provider_adapter_registry(),
            authority,
            middleware_name="pi-egress",
            harness_version="sdk-v1",
        ),
        authority,
    )


def _target(*, host: str = "provider.test") -> HttpTarget:
    return HttpTarget(
        scheme="https",
        host=host,
        port=443,
        method="POST",
        path="/v1/chat/completions",
        query="",
    )


def _context(
    hook: AdmissionHook,
    *,
    target: HttpTarget | None = None,
) -> HarnessAdmissionContext:
    schema = (
        "openshell.pi-message.v1"
        if hook is AdmissionHook.USER_MESSAGE
        else "openshell.pi-tool-result.v1"
    )
    return HarnessAdmissionContext(
        request_id="admission-1",
        sandbox_id="sandbox-1",
        middleware_name="pi-egress",
        harness="pi",
        harness_version="sdk-v1",
        hook=hook,
        schema_version=schema,
        provider_target=target or _target(),
        provider_adapter_schema="openai.request.v1",
    )


def _admit(
    processor: HarnessAdmissionProcessor,
    value: PiMessageV1 | PiToolResultV1,
    *,
    target: HttpTarget | None = None,
    timeout: Timeout | None = None,
):
    hook = (
        AdmissionHook.USER_MESSAGE
        if isinstance(value, PiMessageV1)
        else AdmissionHook.TOOL_RESULT
    )
    return processor.process(
        HarnessAdmissionRequest(
            request_body=canonical_json_bytes(value),
            provenance=AdmissionProvenance(
                session_id="session-1", submission_id="submission-1"
            ),
        ),
        _context(hook, target=target),
        timeout=timeout or Timeout.from_seconds(1),
    )


def _user(text: str) -> PiMessageV1:
    return PiMessageV1(
        schema_version="openshell.pi-message.v1", origin="user", text=text
    )


def _tool_result(
    text: str, *, image: bool = False, tool_call_id: str = "call-1"
) -> PiToolResultV1:
    content: list[dict[str, object]] = (
        [{"type": "image", "data": "AA==", "mimeType": "image/png"}]
        if image
        else [{"type": "text", "text": text}]
    )
    return PiToolResultV1.model_validate(
        {
            "schema_version": "openshell.pi-tool-result.v1",
            "tool_call_id": tool_call_id,
            "tool_name": "read",
            "content": content,
            "is_error": False,
        },
        strict=True,
    )


def _provider_request(
    prompt: str,
    *,
    tool_result: str | None = None,
    headers: tuple[HttpHeader, ...] = (),
    target: HttpTarget | None = None,
) -> HttpRequest:
    provider_body = json.loads(json.dumps(_PI_CHAT_FIXTURES["user_request"]))
    provider_body["messages"][1]["content"] = prompt
    if tool_result is not None:
        provider_body["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": tool_result,
                    "tool_call_id": "call-1",
                },
            ]
        )
    body = json.dumps(
        provider_body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return HttpRequest(
        context=RequestContext(request_id="network-1", sandbox_id="sandbox-1"),
        target=target or _target(),
        headers=(HttpHeader(name="content-type", value="application/json"),) + headers,
        body=body,
    )


def _responses_request(
    prompt: str,
    *,
    tool_result: str | None = None,
) -> HttpRequest:
    fixture_name = "tool_result_request" if tool_result is not None else "user_request"
    provider_body = json.loads(json.dumps(_PI_RESPONSES_FIXTURES[fixture_name]))
    provider_body["input"][1]["content"][0]["text"] = prompt
    if tool_result is not None:
        provider_body["input"][-1]["output"] = tool_result
    body = json.dumps(
        provider_body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return HttpRequest(
        context=RequestContext(request_id="network-1", sandbox_id="sandbox-1"),
        target=_target().model_copy(update={"path": "/v1/responses"}),
        headers=(HttpHeader(name="content-type", value="application/json"),),
        body=body,
    )


def _egress(
    processor: AttestedEgressProcessor,
    request: HttpRequest,
    attestation: bytes | None,
):
    return processor.process(
        request,
        agent_attestation=attestation or b"",
        timeout=Timeout.from_seconds(1),
    )


def test_user_attestation_authorizes_retries_without_entering_request_headers() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe rendered prompt"))

    assert admitted.decision is AdmissionDecision.ALLOW
    assert admitted.attestation is not None
    request = _provider_request("safe rendered prompt")
    first = _egress(egress, request, admitted.attestation)
    retry = _egress(egress, request, admitted.attestation)

    assert first.decision.value == "allow"
    assert retry.decision.value == "allow"
    assert first.request_mutations.header_mutations == ()


@pytest.mark.parametrize(
    ("fixture_name", "candidate"),
    [
        ("opus", _tool_result("safe tool output")),
        ("qwen", _tool_result("safe tool output")),
        ("compaction_summary", _user("Summary:\ncompacted context")),
    ],
)
def test_configured_pi_chat_serializations_are_attested(
    fixture_name: str, candidate: PiMessageV1 | PiToolResultV1
) -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, candidate)
    assert admitted.attestation is not None
    request = _provider_request("safe").model_copy(
        update={
            "body": json.dumps(
                _PI_CHAT_FIXTURES[fixture_name],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    result = _egress(egress, request, admitted.attestation)

    assert result.decision.value == "allow"


def test_user_attestation_authorizes_responses_requests_and_retries() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None
    request = _responses_request("safe")

    first = _egress(egress, request, admitted.attestation)
    retry = _egress(egress, request, admitted.attestation)

    assert first.decision.value == "allow"
    assert retry.decision.value == "allow"


def test_changed_responses_context_fails_closed() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None

    result = _egress(egress, _responses_request("changed prompt"), admitted.attestation)

    assert result.reason_code == "attestation_context_mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update({"messages": []}),
        lambda body: body.update({"unknown_replay_field": "value"}),
        lambda body: body["input"].append(
            {"role": "user", "content": [{"type": "input_image"}]}
        ),
        lambda body: body.pop("max_output_tokens"),
    ],
)
def test_mixed_or_malformed_responses_shapes_fail_closed(mutation) -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None
    request = _responses_request("safe")
    body = json.loads(request.body)
    mutation(body)
    malformed = request.model_copy(
        update={
            "body": json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    result = _egress(egress, malformed, admitted.attestation)

    assert result.reason_code == "provider_shape_unsupported"


def test_tool_result_attestation_authorizes_responses_call_id_projection() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(
        admission,
        _tool_result("safe tool output", tool_call_id="call-1|fc-1"),
    )
    assert admitted.attestation is not None

    result = _egress(
        egress,
        _responses_request("use the tool", tool_result="safe tool output"),
        admitted.attestation,
    )

    assert result.decision.value == "allow"


def test_changed_or_unattested_user_context_fails_closed() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe rendered prompt"))
    assert admitted.attestation is not None

    changed = _egress(egress, _provider_request("changed prompt"), admitted.attestation)
    missing = _egress(egress, _provider_request("safe rendered prompt"), None)

    assert changed.reason_code == "attestation_context_mismatch"
    assert missing.reason_code == "attestation_missing"


def test_attestation_uses_stable_destination_across_tls_proxy_normalization() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(
        admission,
        _user("safe"),
        target=HttpTarget(
            scheme="https",
            host="provider.test",
            port=443,
            method="POST",
            path="",
            query="",
        ),
    )
    assert admitted.attestation is not None
    normalized = HttpTarget(
        scheme="http",
        host="provider.test",
        port=443,
        method="POST",
        path="/v1/chat/completions",
        query="",
    )

    allowed = _egress(
        egress,
        _provider_request("safe", target=normalized),
        admitted.attestation,
    )
    wrong_host = _egress(
        egress,
        _provider_request(
            "safe", target=normalized.model_copy(update={"host": "other.test"})
        ),
        admitted.attestation,
    )

    assert allowed.decision.value == "allow"
    assert wrong_host.reason_code == "attestation_context_mismatch"


def test_user_redaction_attests_only_the_replacement() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user(f"hide {REDACT_TEXT} please"))

    assert admitted.decision is AdmissionDecision.REPLACE
    assert admitted.attestation is not None
    assert admitted.replacement_body is not None
    replacement = PiMessageV1.model_validate_json(
        admitted.replacement_body, strict=True
    ).text

    assert replacement == "hide [REDACTED] please"
    assert (
        _egress(
            egress, _provider_request(replacement), admitted.attestation
        ).decision.value
        == "allow"
    )
    assert (
        _egress(
            egress,
            _provider_request(f"hide {REDACT_TEXT} please"),
            admitted.attestation,
        ).reason_code
        == "attestation_context_mismatch"
    )


def test_tool_result_is_admitted_before_persistence_and_attested_at_egress() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _tool_result("safe tool output"))

    assert admitted.decision is AdmissionDecision.ALLOW
    assert admitted.attestation is not None
    matching = _egress(
        egress,
        _provider_request("inspect", tool_result="safe tool output"),
        admitted.attestation,
    )
    changed = _egress(
        egress,
        _provider_request("inspect", tool_result="changed tool output"),
        admitted.attestation,
    )

    assert matching.decision.value == "allow"
    assert changed.reason_code == "attestation_context_mismatch"


def test_tool_result_denial_redaction_and_images_fail_closed() -> None:
    admission, _, _ = _processors()

    denied = _admit(admission, _tool_result(DENY_TEXT))
    redacted = _admit(admission, _tool_result(REDACT_TEXT))
    image = _admit(admission, _tool_result("", image=True))

    assert denied.decision is AdmissionDecision.DENY
    assert denied.attestation is None
    assert redacted.decision is AdmissionDecision.REPLACE
    assert redacted.replacement_body is not None
    redacted_tool_result = PiToolResultV1.model_validate_json(
        redacted.replacement_body, strict=True
    )
    assert isinstance(redacted_tool_result.content[0], PiTextContentV1)
    assert redacted_tool_result.content[0].text == "[REDACTED]"
    assert image.decision is AdmissionDecision.DENY
    assert image.reason_code == "admission_contract_invalid"


def test_denial_returns_no_attestation_or_replacement() -> None:
    admission, _, _ = _processors()

    denied = _admit(admission, _user(f"do not persist {DENY_TEXT}"))

    assert denied.decision is AdmissionDecision.DENY
    assert denied.attestation is None
    assert denied.replacement_body is None


@pytest.mark.parametrize(
    "context_update",
    [{"harness": "unknown"}, {"schema_version": "openshell.unknown.v1"}],
)
def test_unknown_harness_binding_fails_closed(context_update: dict[str, str]) -> None:
    admission, _, _ = _processors()
    result = admission.process(
        HarnessAdmissionRequest(
            request_body=canonical_json_bytes(_user("safe")),
            provenance=AdmissionProvenance(
                session_id="session-1", submission_id="submission-1"
            ),
        ),
        _context(AdmissionHook.USER_MESSAGE).model_copy(update=context_update),
        timeout=Timeout.from_seconds(1),
    )

    assert result.reason_code == "admission_contract_invalid"


def test_oversized_redaction_attempt_fails_before_attestation_issuance() -> None:
    admission, _, _ = _processors(replacement_template="x" * 1024)

    denied = _admit(
        admission,
        _user(REDACT_TEXT * (MAX_ADMISSION_BODY_BYTES // 1024 + 1)),
    )

    assert denied.decision is AdmissionDecision.DENY
    assert denied.reason_code == "egress_gate_limit_exceeded"
    assert denied.attestation is None


def test_malformed_duplicate_and_expired_admission_fail_closed() -> None:
    admission, _, _ = _processors()
    provenance = AdmissionProvenance(
        session_id="session-1", submission_id="submission-1"
    )

    def admit_body(body: bytes, timeout: Timeout | None = None):
        return admission.process(
            HarnessAdmissionRequest(request_body=body, provenance=provenance),
            _context(AdmissionHook.USER_MESSAGE),
            timeout=timeout or Timeout.from_seconds(1),
        )

    malformed = admit_body(b"{")
    missing_origin = admit_body(
        b'{"schema_version":"openshell.pi-message.v1","text":"safe"}'
    )
    duplicate = admit_body(
        b'{"origin":"user","schema_version":"openshell.pi-message.v1",'
        b'"schema_version":"openshell.pi-message.v1","text":"safe"}'
    )
    over_depth = admit_body(b"[" * 129 + b"0" + b"]" * 129)
    expired = admit_body(b"{}", Timeout(deadline=0.0))

    assert malformed.reason_code == "admission_contract_invalid"
    assert missing_origin.reason_code == "admission_contract_invalid"
    assert duplicate.reason_code == "admission_contract_invalid"
    assert over_depth.reason_code == "admission_unavailable"
    assert expired.reason_code == "admission_unavailable"


def test_provider_shape_validation_and_optional_reasoning_field_are_preserved() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None
    request = _provider_request("safe")
    malformed = request.model_copy(update={"body": b"{"})
    provider_body = json.loads(request.body)
    provider_body["reasoning_effort"] = "medium"
    with_reasoning = request.model_copy(
        update={
            "body": json.dumps(
                provider_body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    malformed_result = _egress(egress, malformed, admitted.attestation)
    reasoning_result = _egress(egress, with_reasoning, admitted.attestation)

    assert malformed_result.reason_code == "provider_shape_unsupported"
    assert reasoning_result.decision.value == "allow"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update({"max_completion_tokens": 128}),
        lambda body: body.update({"store": None}),
        lambda body: body["tools"][0]["function"].update({"strict": None}),
    ],
)
def test_mixed_or_null_chat_compatibility_fields_fail_closed(mutation) -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None
    request = _provider_request("safe")
    body = json.loads(request.body)
    mutation(body)
    malformed = request.model_copy(
        update={
            "body": json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    result = _egress(egress, malformed, admitted.attestation)

    assert result.reason_code == "provider_shape_unsupported"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["messages"][2].update({"reasoning_content": None}),
        lambda body: body["messages"][2].update({"unknown_replay_field": "value"}),
    ],
)
def test_qwen_replay_fields_fail_closed_unless_explicitly_supported(mutation) -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _tool_result("safe tool output"))
    assert admitted.attestation is not None
    body = json.loads(json.dumps(_PI_CHAT_FIXTURES["qwen"]))
    mutation(body)
    request = _provider_request("unused").model_copy(
        update={"body": json.dumps(body, separators=(",", ":")).encode()}
    )

    result = _egress(egress, request, admitted.attestation)

    assert result.reason_code == "provider_shape_unsupported"


def test_workload_receipt_header_is_reserved_in_managed_flow() -> None:
    admission, egress, _ = _processors()
    admitted = _admit(admission, _user("safe"))
    assert admitted.attestation is not None
    request = _provider_request(
        "safe",
        headers=(HttpHeader(name=RECEIPT_HEADER, value="eg1.untrusted"),),
    )

    result = _egress(egress, request, admitted.attestation)

    assert result.reason_code == "reserved_header_present"
