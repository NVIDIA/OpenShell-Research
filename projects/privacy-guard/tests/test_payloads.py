import pytest
from pydantic import ValidationError

from privacy_guard.config import PolicyConfig
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.scanners import Finding, RequestBodyFinding


def test_processing_decision_values() -> None:
    assert ProcessingDecision.ALLOW.value == "allow"
    assert ProcessingDecision.DENY.value == "deny"


def test_payloads_are_frozen_and_hide_body_content_from_repr() -> None:
    sensitive_body = b"sensitive-body-8472"
    request = InterceptedRequest(
        raw_body=sensitive_body,
        content_type="application/json",
        policy_config=PolicyConfig(),
    )
    result = ProcessingResult(
        decision=ProcessingDecision.ALLOW,
        replacement_body=sensitive_body,
    )

    with pytest.raises(ValidationError):
        setattr(request, "raw_body", b"changed")
    with pytest.raises(ValidationError):
        setattr(result, "replacement_body", b"changed")
    assert repr(sensitive_body) not in repr(request)
    assert repr(sensitive_body) not in repr(result)


def test_processing_result_repr_hides_sensitive_structural_path() -> None:
    sensitive_key = "secret-person@example.com"
    finding = RequestBodyFinding(
        finding=Finding(
            entity="email",
            scanner_name="scanner",
            start_offset=0,
            end_offset=1,
        ),
        text_block_path=f"#key:/{sensitive_key}",
    )
    result = ProcessingResult(decision=ProcessingDecision.ALLOW, findings=(finding,))

    assert sensitive_key not in repr(finding)
    assert sensitive_key not in repr(result)


def test_empty_replacement_is_distinct_from_no_replacement() -> None:
    no_replacement = ProcessingResult(decision=ProcessingDecision.ALLOW)
    empty_replacement = ProcessingResult(
        decision=ProcessingDecision.ALLOW, replacement_body=b""
    )

    assert no_replacement.replacement_body is None
    assert empty_replacement.replacement_body == b""
    assert no_replacement != empty_replacement


def test_processing_result_defaults_to_an_empty_findings_tuple() -> None:
    result = ProcessingResult(decision=ProcessingDecision.ALLOW)

    assert result.findings == ()
    assert type(result.findings) is tuple
    assert result.reason_code is None


def test_intercepted_request_is_a_passive_record_without_body_method() -> None:
    assert not hasattr(InterceptedRequest, "body")


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            InterceptedRequest,
            {
                "raw_body": bytearray(b"body"),
                "policy_config": PolicyConfig(),
            },
        ),
        (
            InterceptedRequest,
            {
                "raw_body": b"body",
                "policy_config": {"on_finding": {"action": "redact"}},
            },
        ),
        (ProcessingResult, {"decision": "allow"}),
        (
            ProcessingResult,
            {"decision": ProcessingDecision.ALLOW, "findings": []},
        ),
    ],
)
def test_payloads_reject_dataclass_era_coercions(
    model: type[InterceptedRequest] | type[ProcessingResult],
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_payloads_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        InterceptedRequest.model_validate(
            {
                "raw_body": b"body",
                "policy_config": PolicyConfig(),
                "legacy_body": b"body",
            }
        )
