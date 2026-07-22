import inspect

from privacy_guard.errors import (
    ErrorCode,
    ErrorComponent,
    ErrorKind,
    PrivacyGuardError,
)


def test_every_error_code_has_one_safe_complete_specification() -> None:
    sentinel = "sensitive-request-value-8472"

    assert len({code.value for code in ErrorCode}) == len(ErrorCode)
    for code in ErrorCode:
        error = PrivacyGuardError(code)
        message = str(error)

        assert f"[{code.value}]" in message
        assert error.component.value in message
        assert error.operation in message
        assert error.summary in message
        assert error.hint in message
        assert sentinel not in message
        assert repr(error) == f"PrivacyGuardError({message!r})"


def test_error_kinds_distinguish_invalid_input_from_internal_failures() -> None:
    assert PrivacyGuardError(ErrorCode.CONFIG_INVALID).kind is ErrorKind.INVALID_INPUT
    assert (
        PrivacyGuardError(ErrorCode.SCANNER_EXECUTION_FAILED).kind is ErrorKind.INTERNAL
    )
    assert (
        PrivacyGuardError(ErrorCode.CONFIG_INVALID).component is ErrorComponent.CONFIG
    )


def test_privacy_guard_error_exposes_only_a_catalog_code_parameter() -> None:
    assert list(inspect.signature(PrivacyGuardError).parameters) == ["code"]
