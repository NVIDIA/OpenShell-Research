# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Accounting of allowed egress per sandbox and host, and the byte budget."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

WINDOW_SECONDS = 60.0
ON_EXCEED_ACTIONS = ("deny", "finding")


@dataclass(frozen=True)
class Budget:
    """Request bytes one sandbox may send to one host in a rolling minute."""

    bytes_per_minute: int
    on_exceed: str = "deny"

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> Budget | None:
        """Read the `budget` block of a policy's middleware config, if any."""
        unknown = set(config) - {"budget"}
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        block = config.get("budget")
        if block is None:
            return None
        if not isinstance(block, Mapping):
            raise ValueError("budget must be a mapping")
        unknown = set(block) - {"bytes_per_minute", "on_exceed"}
        if unknown:
            raise ValueError(f"unknown budget keys: {sorted(unknown)}")
        limit = block.get("bytes_per_minute")
        # Struct numbers arrive as floats.
        if not isinstance(limit, int | float) or limit != int(limit) or limit <= 0:
            raise ValueError("budget.bytes_per_minute must be a positive integer")
        on_exceed = block.get("on_exceed", "deny")
        if on_exceed not in ON_EXCEED_ACTIONS:
            raise ValueError(f"budget.on_exceed must be one of {ON_EXCEED_ACTIONS}")
        return cls(bytes_per_minute=int(limit), on_exceed=str(on_exceed))


@dataclass(frozen=True)
class Verdict:
    """What the watchdog decided for one request."""

    allow: bool
    window_bytes: int
    reason_code: str = ""
    reason: str = ""
    finding: bool = False


class Ledger:
    """Rolling per-sandbox, per-host byte counts and the quarantine set.

    A sandbox that exceeds a deny budget is quarantined: every later request it
    makes through the watchdog is refused without further accounting. This is
    stage one of containment and needs no gateway call.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._sends: dict[tuple[str, str], deque[tuple[float, int]]] = defaultdict(deque)
        self._quarantined: dict[str, str] = {}

    def record(
        self, sandbox_id: str, host: str, request_bytes: int, budget: Budget | None
    ) -> Verdict:
        if sandbox_id in self._quarantined:
            return Verdict(
                allow=False,
                window_bytes=self.window_bytes(sandbox_id, host),
                reason_code="sandbox_quarantined",
                reason=f"sandbox quarantined: {self._quarantined[sandbox_id]}",
            )
        sends = self._sends[(sandbox_id, host)]
        now = self._clock()
        while sends and sends[0][0] <= now - WINDOW_SECONDS:
            sends.popleft()
        projected = sum(size for _, size in sends) + request_bytes

        if budget is not None and projected > budget.bytes_per_minute:
            reason = f"{host} byte budget exceeded: {projected} > {budget.bytes_per_minute}/min"
            if budget.on_exceed == "deny":
                self._quarantined[sandbox_id] = reason
                return Verdict(
                    allow=False,
                    window_bytes=projected - request_bytes,
                    reason_code="egress_budget_exceeded",
                    reason=reason,
                    finding=True,
                )
            sends.append((now, request_bytes))
            return Verdict(
                allow=True,
                window_bytes=projected,
                reason_code="egress_budget_exceeded",
                reason=reason,
                finding=True,
            )

        sends.append((now, request_bytes))
        return Verdict(allow=True, window_bytes=projected)

    def window_bytes(self, sandbox_id: str, host: str) -> int:
        return sum(size for _, size in self._sends.get((sandbox_id, host), ()))

    def is_quarantined(self, sandbox_id: str) -> bool:
        return sandbox_id in self._quarantined
