import inspect

from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    ErrorComponent,
    ErrorKind,
)


def test_every_error_code_has_one_safe_complete_specification() -> None:
    sentinel = "sensitive-request-value-8472"

    assert len({code.value for code in ErrorCode}) == len(ErrorCode)
    for code in ErrorCode:
        error = EgressGateError(code)
        message = str(error)

        assert f"[{code.value}]" in message
        assert error.component.value in message
        assert error.operation in message
        assert error.summary in message
        assert error.hint in message
        assert sentinel not in message
        assert repr(error) == f"EgressGateError({message!r})"


def test_error_kinds_distinguish_invalid_input_from_internal_failures() -> None:
    assert EgressGateError(ErrorCode.CONFIG_INVALID).kind is ErrorKind.INVALID_INPUT
    assert EgressGateError(ErrorCode.ENGINE_EXECUTION_FAILED).kind is ErrorKind.INTERNAL
    assert EgressGateError(ErrorCode.CONFIG_INVALID).component is ErrorComponent.CONFIG


def test_config_error_explains_the_transport_size_limit() -> None:
    error = EgressGateError(ErrorCode.CONFIG_INVALID)

    assert "encoded configuration at or below 64 KiB" in error.hint


def test_egress_gate_error_exposes_only_a_catalog_code_parameter() -> None:
    assert list(inspect.signature(EgressGateError).parameters) == ["code"]
