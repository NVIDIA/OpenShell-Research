"""Contract tests for trusted request-level gates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Literal

import pytest

from egress_gate.errors import GateContractError, GateInputError
from egress_gate.gates import (
    Gate,
    GateCapabilities,
    GateConfig,
    GateResources,
    RegexBodyConfig,
    RegexBodyGate,
)
from egress_gate.request import HttpRequest, HttpTarget, RequestContext
from egress_gate.result import Finding, GateEvaluation
from egress_gate.timeout import Timeout


class _RequestConfig(GateConfig):
    gate: Literal["test-request"] = "test-request"


class _RequestGate(Gate[_RequestConfig, None]):
    capabilities = GateCapabilities(reads_target=True, may_deny=True)
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if request.target.host == "blocked.example":
            return GateEvaluation.deny("egress_gate_test_denied")
        return GateEvaluation.proceed()


class _CounterResources(GateResources):
    __slots__ = ("lock", "calls")

    def __init__(self) -> None:
        self.lock = Lock()
        self.calls = 0


class _CounterConfig(GateConfig):
    gate: Literal["test-counter"] = "test-counter"


class _CounterGate(Gate[_CounterConfig, _CounterResources]):
    capabilities = GateCapabilities(uses_resources=True, reads_body=True)
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        del request
        timeout.raise_if_expired()
        resources = self.resources
        with resources.lock:
            resources.calls += 1
        return GateEvaluation.proceed()


class _UndeclaredOutputGate(Gate[_RequestConfig, None]):
    capabilities = GateCapabilities()
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        del request, timeout
        return GateEvaluation.proceed(
            findings=(Finding(type="undeclared", label="test"),)
        )


def _request(*, body: bytes = b"payload", host: str = "example.com") -> HttpRequest:
    return HttpRequest(
        context=RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        target=HttpTarget(
            scheme="https",
            host=host,
            port=443,
            method="POST",
            path="/v1/items",
            query="",
        ),
        headers=(),
        body=body,
    )


def test_gate_uses_exact_config_and_resource_types() -> None:
    config = _RequestConfig()
    gate = _RequestGate(config, None)

    assert gate.config is config
    assert gate.resources is None
    assert _RequestGate.get_config_type() is _RequestConfig
    assert _RequestGate.get_resources_type() is None
    assert (
        gate.evaluate(
            _request(host="blocked.example"), timeout=Timeout.from_seconds(1)
        ).control.value
        == "deny"
    )


def test_gate_public_wrapper_enforces_declared_output_capabilities() -> None:
    with pytest.raises(GateContractError, match="undeclared finding"):
        _UndeclaredOutputGate(_RequestConfig(), None).evaluate(
            _request(), timeout=Timeout.from_seconds(1)
        )


def test_gate_rejects_invalid_utf8_as_gate_input() -> None:
    config = RegexBodyConfig.model_validate(
        {
            "pattern_catalog": {
                "entities": [
                    {
                        "name": "token",
                        "rules": [{"pattern": "secret", "confidence": "high"}],
                    }
                ]
            },
            "mode": "detect",
        }
    )

    with pytest.raises(GateInputError, match="valid UTF-8"):
        RegexBodyGate(config, None).evaluate(
            _request(body=b"\xff"), timeout=Timeout.from_seconds(1)
        )


def test_resource_backed_gate_is_safe_for_concurrent_evaluations() -> None:
    resources = _CounterResources()
    gate = _CounterGate(_CounterConfig(), resources)

    def evaluate(_: int) -> GateEvaluation:
        return gate.evaluate(_request(), timeout=Timeout.from_seconds(1))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(evaluate, range(32)))

    assert all(result.control.value == "proceed" for result in results)
    assert resources.calls == 32
