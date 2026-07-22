import pytest
from pydantic import ValidationError

from privacy_guard.config import (
    ActionConfig,
    BlockActionConfig,
    ObserveActionConfig,
    PolicyAction,
    PolicyActionConfig,
    PolicyConfig,
    RedactActionConfig,
)
from privacy_guard.constants import MAX_SCANNER_METADATA_BYTES
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_body import select_format_handler
from privacy_guard.scanners import Confidence
from privacy_guard.validation import (
    BoundedMetadataString,
    NonEmptyScalarString,
    ScalarString,
    StrictDomainModel,
)


class _ValidationFixture(StrictDomainModel):
    scalar: ScalarString
    non_empty: NonEmptyScalarString
    metadata: BoundedMetadataString


class _InvalidDefaultFixture(StrictDomainModel):
    value: NonEmptyScalarString = ""


def _wire_on_finding(action: str, **values: object) -> dict[str, object]:
    return {"on_finding": {"action": action, **values}}


def test_defaults_use_a_complete_redact_action() -> None:
    config = PolicyConfig()

    assert config.body_format == "json"
    assert type(config.on_finding) is RedactActionConfig
    assert config.on_finding.action is PolicyAction.REDACT
    assert config.on_finding.entity_types is None
    assert config.on_finding.minimum_confidence is None
    assert config.on_finding.template == "[{entity}]"
    assert [action.value for action in PolicyAction] == ["observe", "redact", "block"]


@pytest.mark.parametrize(
    ("action", "expected_type"),
    [
        (PolicyAction.OBSERVE, ObserveActionConfig),
        (PolicyAction.REDACT, RedactActionConfig),
        (PolicyAction.BLOCK, BlockActionConfig),
    ],
)
def test_wire_parser_selects_discriminated_action_model(
    action: PolicyAction, expected_type: type[ActionConfig]
) -> None:
    config = PolicyConfig.from_mapping(_wire_on_finding(action.value))

    assert type(config.on_finding) is expected_type
    assert config.on_finding.action is action


@pytest.mark.parametrize(
    "action",
    [
        ObserveActionConfig(),
        BlockActionConfig(
            entity_types=frozenset({"email"}),
            minimum_confidence=Confidence.HIGH,
        ),
        RedactActionConfig(template="[{entity}]"),
    ],
)
def test_discriminated_action_serialization_round_trips(
    action: PolicyActionConfig,
) -> None:
    serialized = PolicyConfig(on_finding=action).model_dump(mode="json")
    reparsed = PolicyConfig.from_mapping(serialized)

    assert serialized["on_finding"]["action"] == action.action.value
    assert type(reparsed.on_finding) is type(action)
    assert reparsed == PolicyConfig(on_finding=action)


def test_on_finding_schema_declares_action_discriminator() -> None:
    action_schema = PolicyConfig.model_json_schema()["properties"]["on_finding"]

    assert action_schema["discriminator"] == {
        "propertyName": "action",
        "mapping": {
            "observe": "#/$defs/ObserveActionConfig",
            "block": "#/$defs/BlockActionConfig",
            "redact": "#/$defs/RedactActionConfig",
        },
    }


@pytest.mark.parametrize("confidence", list(Confidence))
def test_wire_parser_accepts_confidence_strings(confidence: Confidence) -> None:
    config = PolicyConfig.from_mapping(
        _wire_on_finding("observe", minimum_confidence=confidence.value)
    )

    assert config.on_finding.minimum_confidence is confidence


def test_null_minimum_confidence_selects_every_confidence() -> None:
    config = PolicyConfig.from_mapping(
        _wire_on_finding("observe", minimum_confidence=None)
    )

    assert config.on_finding.minimum_confidence is None


@pytest.mark.parametrize(
    "values",
    [
        {"unknown": "value"},
        {"action": {"kind": "observe"}},
        {"on_finding": "observe"},
        {"on_finding": {"kind": "observe"}},
        {"on_finding": {"action": "audit"}},
        {"on_finding": {}},
        {"body_format": ""},
        {"debug_inject_path": "/message"},
        {"debug_inject_text": " suffix"},
        {"redaction_template": "[redacted]"},
        {"entity_types": ["email"]},
        {"minimum_confidence": "high"},
    ],
)
def test_rejects_invalid_or_misplaced_fields(values: dict[str, object]) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID
    assert exception_info.value.__cause__ is None


@pytest.mark.parametrize(
    "values",
    [
        {"body_format": True},
        _wire_on_finding("redact", template=True),
        _wire_on_finding("observe", minimum_confidence=True),
        {"on_finding": {"action": True}},
    ],
)
def test_does_not_coerce_wrong_scalar_types(values: dict[str, object]) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


@pytest.mark.parametrize(
    "values",
    [
        {"body_format": "safe\ud800sentinel"},
        {"on_finding": {"action": "safe\ud800sentinel"}},
        _wire_on_finding("redact", template="safe\ud800sentinel"),
        _wire_on_finding("observe", minimum_confidence="safe\ud800sentinel"),
    ],
)
def test_rejects_unpaired_unicode_surrogates(values: dict[str, object]) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_accepts_action_finding_criteria() -> None:
    config = PolicyConfig.from_mapping(
        _wire_on_finding(
            "block",
            entity_types=["email", "api_key"],
            minimum_confidence="high",
        )
    )

    assert config.on_finding.entity_types == frozenset({"email", "api_key"})
    assert config.on_finding.minimum_confidence is Confidence.HIGH


@pytest.mark.parametrize(
    "values",
    [
        {"on_finding": PolicyAction.BLOCK},
        _wire_on_finding("block", entity_types=("email",)),
        _wire_on_finding("block", entity_types={"email"}),
        _wire_on_finding("block", entity_types="email"),
        _wire_on_finding("block", entity_types=["email", 1]),
    ],
)
def test_wire_parser_rejects_non_wire_forms(values: dict[str, object]) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


@pytest.mark.parametrize(
    "values",
    [
        [("on_finding", {"action": "block"})],
        (("on_finding", {"action": "block"}),),
        iter([("on_finding", {"action": "block"})]),
    ],
)
def test_wire_parser_rejects_non_mapping_pair_iterables(values: object) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID
    assert exception_info.value.__cause__ is None


def test_direct_construction_is_strict_model_to_model_use() -> None:
    action = BlockActionConfig(
        entity_types=frozenset({"email"}),
        minimum_confidence=Confidence.HIGH,
    )
    config = PolicyConfig(on_finding=action)

    assert config.on_finding is action

    discriminated = PolicyConfig.model_validate({"on_finding": {"action": "block"}})
    assert type(discriminated.on_finding) is BlockActionConfig
    with pytest.raises(ValidationError):
        BlockActionConfig.model_validate({"entity_types": ["email"]})
    with pytest.raises(ValidationError):
        BlockActionConfig.model_validate({"minimum_confidence": "high"})


def test_template_is_valid_only_for_redact_action() -> None:
    redact = PolicyConfig.from_mapping(
        _wire_on_finding("redact", template="[redacted]")
    )

    assert type(redact.on_finding) is RedactActionConfig
    assert redact.on_finding.template == "[redacted]"
    for action in ("observe", "block"):
        with pytest.raises(PrivacyGuardError):
            PolicyConfig.from_mapping(_wire_on_finding(action, template="[redacted]"))


@pytest.mark.parametrize(
    "entities",
    [
        [""],
        ["email", "email"],
        ["safe\ud800sentinel"],
        ["x" * (MAX_SCANNER_METADATA_BYTES + 1)],
        ["😀" * (MAX_SCANNER_METADATA_BYTES // 4 + 1)],
    ],
)
def test_rejects_invalid_entity_names(entities: list[str]) -> None:
    with pytest.raises(PrivacyGuardError):
        PolicyConfig.from_mapping(_wire_on_finding("observe", entity_types=entities))


def test_none_selects_all_entities_and_empty_set_selects_none() -> None:
    all_entities = PolicyConfig.from_mapping(
        _wire_on_finding("observe", entity_types=None)
    )
    no_entities = PolicyConfig.from_mapping(
        _wire_on_finding("observe", entity_types=[])
    )

    assert all_entities.on_finding.entity_types is None
    assert no_entities.on_finding.entity_types == frozenset()


def test_accepts_entity_name_at_exact_utf8_metadata_limit() -> None:
    entity = "😀" * (MAX_SCANNER_METADATA_BYTES // 4)

    config = PolicyConfig.from_mapping(
        _wire_on_finding("observe", entity_types=[entity])
    )

    assert config.on_finding.entity_types == frozenset({entity})


def test_shared_validation_types_are_strict_scalar_safe_and_validate_defaults() -> None:
    fixture = _ValidationFixture(
        scalar="",
        non_empty="value",
        metadata="metadata",
    )

    assert fixture.scalar == ""
    with pytest.raises(ValidationError):
        _ValidationFixture.model_validate(
            {"scalar": 1, "non_empty": "value", "metadata": "metadata"}
        )
    with pytest.raises(ValidationError):
        _ValidationFixture(scalar="\ud800", non_empty="value", metadata="metadata")
    with pytest.raises(ValidationError):
        _InvalidDefaultFixture()


@pytest.mark.parametrize("template", ["[redacted]", "[{entity}]", "{{{entity}}}"])
def test_accepts_static_and_label_only_templates(template: str) -> None:
    config = PolicyConfig.from_mapping(_wire_on_finding("redact", template=template))

    assert type(config.on_finding) is RedactActionConfig
    assert config.on_finding.template == template


@pytest.mark.parametrize(
    "template",
    ["{unknown}", "{entity!r}", "{entity:>10}", "{", "}", "{}", "{0}"],
)
def test_rejects_unsafe_or_malformed_templates(template: str) -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping(_wire_on_finding("redact", template=template))

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_models_are_frozen_and_sensitive_fields_are_hidden_from_repr() -> None:
    sentinel = "sensitive-config-value-8472"
    config = PolicyConfig.from_mapping(
        {
            "body_format": sentinel,
            **_wire_on_finding(
                "redact",
                template=sentinel,
                entity_types=[sentinel],
            ),
        }
    )

    with pytest.raises(ValidationError):
        setattr(config, "body_format", "json")
    with pytest.raises(ValidationError):
        setattr(config.on_finding, "minimum_confidence", Confidence.HIGH)
    assert sentinel not in repr(config)
    assert sentinel not in repr(config.on_finding)


def test_validation_failure_does_not_leak_input_or_pydantic_error() -> None:
    sentinel = "sensitive-invalid-config-value-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        PolicyConfig.from_mapping({"body_format": sentinel + "\ud800"})

    error = exception_info.value
    assert error.code is ErrorCode.CONFIG_INVALID
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in repr(error.args)
    assert error.__cause__ is None


def test_select_format_handler_error_does_not_leak_unknown_format() -> None:
    sentinel = "sensitive-unknown-format-8472"

    with pytest.raises(PrivacyGuardError) as exception_info:
        select_format_handler(sentinel)

    assert exception_info.value.code is ErrorCode.BODY_FORMAT_UNSUPPORTED
    assert sentinel not in str(exception_info.value)
    assert sentinel not in repr(exception_info.value)
