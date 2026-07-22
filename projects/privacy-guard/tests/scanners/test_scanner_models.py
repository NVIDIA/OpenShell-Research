import math
from types import MappingProxyType

import pytest
from pydantic import ValidationError
from typing_extensions import override

from privacy_guard.scanners import (
    Finding,
    RequestBodyFinding,
    ScanBudget,
    Scanner,
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


def test_finding_accepts_general_immutable_string_metadata() -> None:
    finding = Finding(
        entity="email",
        scanner_name="scanner",
        start_offset=0,
        end_offset=1,
        metadata={"rule": "common-email", "source": "customer-catalog"},
    )

    assert finding.metadata == {
        "rule": "common-email",
        "source": "customer-catalog",
    }
    assert isinstance(finding.metadata, MappingProxyType)
    assert "customer-catalog" not in repr(finding)


def test_scanner_config_owns_identity_and_entity_types() -> None:
    config = ScannerConfig(name="s" * 2048, entity_types=frozenset({"email", "phone"}))

    assert config.name == "s" * 2048
    assert config.entity_types == frozenset({"email", "phone"})
    with pytest.raises(ValidationError):
        setattr(config, "name", "changed")


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
            "metadata": {"key": 3},
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


def test_scanner_initialize_runs_after_validated_config_is_available() -> None:
    class InitializingScanner(Scanner[ScannerConfig]):
        initialized_name: str | None = None

        @override
        def _initialize(self) -> None:
            self.initialized_name = self.scanner_name

        @override
        def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
            return ()

    scanner = InitializingScanner(
        ScannerConfig(name="initialized", entity_types=frozenset())
    )

    assert scanner.initialized_name == "initialized"


@pytest.mark.parametrize("deadline", [True, math.inf, math.nan, "soon"])
def test_scan_budget_uses_strict_pydantic_validation(deadline: object) -> None:
    with pytest.raises(ValidationError):
        ScanBudget.model_validate({"deadline": deadline})
