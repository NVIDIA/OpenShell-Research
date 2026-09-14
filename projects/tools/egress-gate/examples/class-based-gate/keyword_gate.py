# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A minimal class-based Egress Gate implementation."""

from typing import Literal

from egress_gate.gates import Gate, GateCapability, GateConfig, GateRegistry
from egress_gate.request import HttpRequest
from egress_gate.result import GateEvaluation
from egress_gate.timeout import Timeout


class KeywordDenyConfig(GateConfig):
    """Policy fields accepted by the custom gate."""

    kind: Literal["keyword-deny"]
    keyword: str


class KeywordDenyGate(Gate[KeywordDenyConfig, None]):
    """Deny requests whose body contains the configured UTF-8 keyword."""

    capabilities = frozenset({GateCapability.READ_BODY, GateCapability.DENY})
    finding_types = ()

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode("utf-8") in request.body:
            return GateEvaluation.deny("keyword_denied")
        return GateEvaluation.proceed()


registry = GateRegistry(include_builtin_gates=True)
registry.register(KeywordDenyGate)
