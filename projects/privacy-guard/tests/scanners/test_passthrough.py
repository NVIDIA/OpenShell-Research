import pytest
from pydantic import ValidationError

from privacy_guard.scanners import (
    Finding,
    PassthroughScanner,
    RequestBodyFinding,
    ScannerConfig,
)


def test_finding_names_the_scanner_that_produced_it() -> None:
    finding = Finding(
        entity="email",
        scanner_name="example-scanner",
        start_offset=4,
        end_offset=18,
    )

    assert finding.scanner_name == "example-scanner"


def test_passthrough_scanner_has_stable_name() -> None:
    scanner = PassthroughScanner()

    assert scanner.scanner_name == "passthrough"
    assert scanner.supported_entity_types == frozenset()


def test_scanner_config_owns_identity_and_entity_types() -> None:
    config = ScannerConfig(name="s" * 2048, entity_types=frozenset({"email", "phone"}))

    assert config.name == "s" * 2048
    assert config.entity_types == frozenset({"email", "phone"})
    with pytest.raises(ValidationError):
        setattr(config, "name", "changed")


def test_passthrough_uses_minimal_config_with_fixed_empty_catalog() -> None:
    scanner = PassthroughScanner()

    assert type(scanner.config) is ScannerConfig
    assert scanner.supported_entity_types == frozenset()


@pytest.mark.parametrize(
    "values",
    [
        {"name": "", "entity_types": frozenset()},
        {"name": 3, "entity_types": frozenset()},
        {"name": "scanner"},
        {"name": "scanner", "entity_types": "email"},
        {"name": "scanner", "entity_types": frozenset({3})},
    ],
)
def test_scanner_config_rejects_invalid_metadata(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScannerConfig.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [
        {"entity": "", "scanner_name": "scanner", "start_offset": 0, "end_offset": 1},
        {"entity": "email", "scanner_name": "", "start_offset": 0, "end_offset": 1},
        {
            "entity": "\U0001f4a3" * 257,
            "scanner_name": "scanner",
            "start_offset": 0,
            "end_offset": 1,
        },
        {
            "entity": "email",
            "scanner_name": "\U0001f4a3" * 257,
            "start_offset": 0,
            "end_offset": 1,
        },
        {
            "entity": "email",
            "scanner_name": "scanner",
            "start_offset": True,
            "end_offset": 1,
        },
        {
            "entity": "email",
            "scanner_name": "scanner",
            "start_offset": -1,
            "end_offset": 1,
        },
        {
            "entity": "email",
            "scanner_name": "scanner",
            "start_offset": 1,
            "end_offset": 1,
        },
        {
            "entity": "email",
            "scanner_name": "scanner",
            "start_offset": 0,
            "end_offset": 1,
            "confidence": "high",
        },
        {
            "entity": "email",
            "scanner_name": "scanner",
            "start_offset": 0,
            "end_offset": 1,
            "text_block_path": "path",
        },
    ],
)
def test_finding_rejects_invalid_or_extra_fields(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Finding.model_validate(values)


def test_request_body_finding_requires_hidden_path_and_models_are_frozen() -> None:
    finding = Finding(
        entity="email", scanner_name="scanner", start_offset=0, end_offset=1
    )
    request_body_finding = RequestBodyFinding(
        finding=finding, text_block_path="sensitive/path"
    )

    assert request_body_finding.text_block_path == "sensitive/path"
    assert "sensitive/path" not in repr(request_body_finding)
    assert not hasattr(finding, "text_block_path")
    with pytest.raises(ValidationError):
        setattr(finding, "entity", "token")
    with pytest.raises(ValidationError):
        RequestBodyFinding.model_validate(
            {
                "finding": finding,
            }
        )


@pytest.mark.parametrize(
    "text_block",
    [
        "",
        "ordinary ASCII text",
        "Unicode: café 🐍",
        "first line\nsecond line",
        '{"looks": "like JSON"}',
        "Contact alice@example.com or call 555-0100",
    ],
)
def test_passthrough_scanner_always_returns_immutable_empty_tuple(
    text_block: str,
) -> None:
    findings = PassthroughScanner().scan(text_block)

    assert findings == ()
    assert type(findings) is tuple
