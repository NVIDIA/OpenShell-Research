# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conformance tests for managed Pi context admission and attested egress."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Literal

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
    PiAssistantMessageV1,
    PiAssistantToolCallV1,
    PiMessageV1,
    PiProviderContextV1,
    PiTextContentV1,
    PiToolResultV1,
    ReceiptAuthority,
    canonical_json_bytes,
    create_pi_adapter_registry,
    extract_provider_entries,
)
from egress_gate.gates import create_builtin_registry
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.timeout import Timeout

DENY_TEXT = "DENY_THIS"
REDACT_TEXT = "REDACT_THIS"
# Captured serializer payloads remain useful regression fixtures; live serializer
# coverage is exercised by the cross-language service test.
_PI_CHAT_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/pi-openai-completions.json").read_text()
)


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
    schema = {
        AdmissionHook.SYSTEM_CONTEXT: "openshell.pi-message.v1",
        AdmissionHook.USER_MESSAGE: "openshell.pi-message.v1",
        AdmissionHook.COMPACTION_SUMMARY: "openshell.pi-message.v1",
        AdmissionHook.TOOL_RESULT: "openshell.pi-tool-result.v1",
        AdmissionHook.ASSISTANT_MESSAGE: "openshell.pi-assistant-message.v1",
        AdmissionHook.PROVIDER_CONTEXT: "openshell.pi-provider-context.v1",
    }[hook]
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
    value: (PiMessageV1 | PiToolResultV1 | PiAssistantMessageV1 | PiProviderContextV1),
    *,
    target: HttpTarget | None = None,
    timeout: Timeout | None = None,
):
    if isinstance(value, PiMessageV1):
        hook = {
            "user": AdmissionHook.USER_MESSAGE,
            "system": AdmissionHook.SYSTEM_CONTEXT,
            "compaction_summary": AdmissionHook.COMPACTION_SUMMARY,
        }[value.origin]
    elif isinstance(value, PiToolResultV1):
        hook = AdmissionHook.TOOL_RESULT
    elif isinstance(value, PiAssistantMessageV1):
        hook = AdmissionHook.ASSISTANT_MESSAGE
    else:
        hook = AdmissionHook.PROVIDER_CONTEXT
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


def _message(
    text: str,
    *,
    origin: Literal["user", "system", "compaction_summary"] = "user",
) -> PiMessageV1:
    return PiMessageV1(
        schema_version="openshell.pi-message.v1", origin=origin, text=text
    )


def _user(text: str) -> PiMessageV1:
    return _message(text)


def _assistant(
    text: str, *, arguments: dict[str, object] | None = None
) -> PiAssistantMessageV1:
    return PiAssistantMessageV1(
        schema_version="openshell.pi-assistant-message.v1",
        text=text,
        tool_calls=(
            PiAssistantToolCallV1(
                id="call-1", name="read", arguments=arguments or {"path": "safe"}
            ),
        ),
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


def _egress(
    processor: AttestedEgressProcessor,
    request: HttpRequest,
    attestation: bytes | None,
):
    if attestation:
        request = request.model_copy(
            update={
                "headers": (
                    *request.headers,
                    HttpHeader(
                        name=RECEIPT_HEADER,
                        value=base64.urlsafe_b64encode(attestation).decode(),
                    ),
                )
            }
        )
    return processor.process(request, timeout=Timeout.from_seconds(1))


def _admit_provider_request(
    admission: HarnessAdmissionProcessor,
    request: HttpRequest,
):
    return _admit(
        admission,
        PiProviderContextV1(
            schema_version="openshell.pi-provider-context.v1",
            entries=extract_provider_entries(request, Timeout.from_seconds(1)),
        ),
        target=request.target,
    )


@pytest.mark.parametrize("fixture_name", ["opus", "qwen", "compaction_summary"])
def test_complete_pi_chat_context_is_attested(fixture_name: str) -> None:
    admission, egress, _ = _processors()
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
    admitted = _admit_provider_request(admission, request)

    result = _egress(egress, request, admitted.attestation)

    assert admitted.attestation is not None
    assert admitted.attestation.startswith(b"ag2.")
    assert result.decision.value == "allow"
    assert _egress(egress, request, admitted.attestation).decision.value == "allow"


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (
            lambda body: body["messages"][1].update({"content": "changed prompt"}),
            "context_hash_mismatch",
        ),
        (
            lambda body: body["messages"].append({"role": "user", "content": "extra"}),
            "entry_count_mismatch",
        ),
        (lambda body: body["messages"].pop(), "entry_count_mismatch"),
    ],
)
def test_chat_context_tampering_is_denied(mutation, reason_code: str) -> None:
    admission, egress, _ = _processors()
    request = _provider_request("use the tool", tool_result="safe tool output")
    admitted = _admit_provider_request(admission, request)
    body = json.loads(request.body)
    mutation(body)
    changed = request.model_copy(
        update={
            "body": json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        }
    )

    result = _egress(egress, changed, admitted.attestation)

    assert result.reason_code == reason_code


def test_provider_context_redaction_binds_only_the_replacement() -> None:
    admission, egress, _ = _processors()
    original = _provider_request(f"hide {REDACT_TEXT} please")
    admitted = _admit_provider_request(admission, original)

    assert admitted.decision is AdmissionDecision.REPLACE
    assert admitted.attestation is not None
    assert admitted.replacement_body is not None
    replacement = PiProviderContextV1.model_validate_json(
        admitted.replacement_body, strict=True
    )
    replaced = _provider_request(replacement.entries[0].text)

    assert _egress(egress, replaced, admitted.attestation).decision.value == "allow"
    assert (
        _egress(egress, original, admitted.attestation).reason_code
        == "context_hash_mismatch"
    )


def test_restored_context_with_denied_text_is_blocked_at_send_time() -> None:
    admission, _, _ = _processors()

    denied = _admit_provider_request(admission, _provider_request(DENY_TEXT))

    assert denied.decision is AdmissionDecision.DENY
    assert denied.replacement_body is None
    assert denied.reason_code == "egress_gate_regex_denied"


def test_attestation_uses_stable_destination_across_tls_proxy_normalization() -> None:
    admission, egress, _ = _processors()
    normalized = HttpTarget(
        scheme="http",
        host="provider.test",
        port=443,
        method="POST",
        path="/v1/chat/completions",
        query="",
    )
    request = _provider_request("safe", target=normalized)
    admitted = _admit_provider_request(admission, request)

    allowed = _egress(egress, request, admitted.attestation)
    wrong_host = _egress(
        egress,
        _provider_request(
            "safe", target=normalized.model_copy(update={"host": "other.test"})
        ),
        admitted.attestation,
    )

    assert allowed.decision.value == "allow"
    assert wrong_host.reason_code == "attestation_context_mismatch"


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


@pytest.mark.parametrize(
    "origin",
    ["user", "system", "compaction_summary"],
)
def test_text_message_origins_allow_replace_and_deny(origin) -> None:
    admission, _, _ = _processors()

    allowed = _admit(admission, _message("safe", origin=origin))
    redacted = _admit(admission, _message(REDACT_TEXT, origin=origin))
    denied = _admit(admission, _message(DENY_TEXT, origin=origin))

    assert allowed.decision is AdmissionDecision.ALLOW
    assert allowed.attestation is None
    assert redacted.decision is AdmissionDecision.REPLACE
    assert redacted.replacement_body is not None
    replacement = PiMessageV1.model_validate_json(
        redacted.replacement_body, strict=True
    )
    assert replacement.origin == origin
    assert replacement.text == "[REDACTED]"
    assert denied.decision is AdmissionDecision.DENY


def test_text_message_binding_rejects_a_different_origin() -> None:
    admission, _, _ = _processors()
    value = _message("safe", origin="user")

    result = admission.process(
        HarnessAdmissionRequest(
            request_body=canonical_json_bytes(value),
            provenance=AdmissionProvenance(
                session_id="session-1", submission_id="submission-1"
            ),
        ),
        _context(AdmissionHook.COMPACTION_SUMMARY),
        timeout=Timeout.from_seconds(1),
    )

    assert result.reason_code == "admission_contract_invalid"


def test_assistant_message_allows_text_replacement_and_denial() -> None:
    admission, _, _ = _processors()

    allowed = _admit(admission, _assistant("safe"))
    redacted = _admit(admission, _assistant(REDACT_TEXT))
    denied = _admit(admission, _assistant(DENY_TEXT))

    assert allowed.decision is AdmissionDecision.ALLOW
    assert redacted.decision is AdmissionDecision.REPLACE
    assert redacted.replacement_body is not None
    replacement = PiAssistantMessageV1.model_validate_json(
        redacted.replacement_body, strict=True
    )
    assert replacement.text == "[REDACTED]"
    assert replacement.tool_calls == _assistant("safe").tool_calls
    assert denied.decision is AdmissionDecision.DENY


def test_assistant_message_accepts_javascript_number_serialization() -> None:
    admission, _, _ = _processors()
    body = (
        b'{"schema_version":"openshell.pi-assistant-message.v1","text":"safe",'
        b'"tool_calls":[{"arguments":{"threshold":1e-7},"id":"call-1",'
        b'"name":"read"}]}'
    )

    result = admission.process(
        HarnessAdmissionRequest(
            request_body=body,
            provenance=AdmissionProvenance(
                session_id="session-1", submission_id="submission-1"
            ),
        ),
        _context(AdmissionHook.ASSISTANT_MESSAGE),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision is AdmissionDecision.ALLOW


def test_assistant_message_rejects_tool_call_mutation() -> None:
    admission, _, _ = _processors()

    result = _admit(admission, _assistant("safe", arguments={"path": REDACT_TEXT}))

    assert result.decision is AdmissionDecision.DENY
    assert result.reason_code == "admission_contract_invalid"


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
    request = _provider_request("safe")
    admitted = _admit_provider_request(admission, request)
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
        lambda body: body.update({"stream_options": None}),
        lambda body: body.update({"stream_options": {"include_usage": "true"}}),
        lambda body: body["tools"][0].update({"cache_control": {"type": "persistent"}}),
        lambda body: body["tools"][0].update(
            {"cache_control": {"type": "ephemeral", "ttl": "forever"}}
        ),
        lambda body: body["tools"][0]["function"].update({"strict": None}),
        lambda body: body.update({"input": []}),
        lambda body: body["messages"][1].update({"tool_call_id": "wrong-role"}),
        lambda body: body["messages"][1].update({"role": "tool"}),
        lambda body: body["tools"][0]["function"].update(
            {"parameters": {"limit": float("inf")}}
        ),
    ],
)
def test_unsupported_chat_shapes_fail_closed(mutation) -> None:
    admission, egress, _ = _processors()
    request = _provider_request("safe")
    admitted = _admit_provider_request(admission, request)
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
    body = json.loads(json.dumps(_PI_CHAT_FIXTURES["qwen"]))
    original = _provider_request("unused").model_copy(
        update={"body": json.dumps(body, separators=(",", ":")).encode()}
    )
    admitted = _admit_provider_request(admission, original)
    mutation(body)
    request = _provider_request("unused").model_copy(
        update={"body": json.dumps(body, separators=(",", ":")).encode()}
    )

    result = _egress(egress, request, admitted.attestation)

    assert result.reason_code == "provider_shape_unsupported"
