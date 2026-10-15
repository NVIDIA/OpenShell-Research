# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

from twobox_watchdog.ledger import Budget, Ledger


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_budget_from_config_reads_struct_floats() -> None:
    budget = Budget.from_config({"budget": {"bytes_per_minute": 1024.0, "on_exceed": "finding"}})
    assert budget == Budget(bytes_per_minute=1024, on_exceed="finding")


def test_no_budget_block_means_accounting_only() -> None:
    assert Budget.from_config({}) is None


@pytest.mark.parametrize(
    "config",
    [
        {"budgets": {}},
        {"budget": {"bytes_per_minute": 0}},
        {"budget": {"bytes_per_minute": 1.5}},
        {"budget": {"bytes_per_minute": 10, "on_exceed": "stop"}},
        {"budget": {"bytes_per_minute": 10, "requests_per_minute": 5}},
    ],
)
def test_invalid_config_is_rejected(config: dict) -> None:
    with pytest.raises(ValueError):
        Budget.from_config(config)


def test_deny_budget_quarantines_the_sandbox() -> None:
    ledger = Ledger(clock=FakeClock())
    budget = Budget(bytes_per_minute=100)

    assert ledger.record("sb-1", "httpbin.org", 60, budget).allow
    over = ledger.record("sb-1", "httpbin.org", 60, budget)
    assert not over.allow
    assert over.reason_code == "egress_budget_exceeded"
    assert over.finding

    # Stage one: every later request is refused, even to other hosts and with no budget.
    later = ledger.record("sb-1", "pypi.org", 1, None)
    assert not later.allow
    assert later.reason_code == "sandbox_quarantined"
    assert ledger.record("sb-2", "httpbin.org", 60, budget).allow


def test_window_rolls_after_a_minute() -> None:
    clock = FakeClock()
    ledger = Ledger(clock=clock)
    budget = Budget(bytes_per_minute=100)

    assert ledger.record("sb-1", "httpbin.org", 90, budget).allow
    clock.now += 61
    verdict = ledger.record("sb-1", "httpbin.org", 90, budget)
    assert verdict.allow
    assert verdict.window_bytes == 90


def test_finding_budget_allows_and_flags() -> None:
    ledger = Ledger(clock=FakeClock())
    budget = Budget(bytes_per_minute=100, on_exceed="finding")

    ledger.record("sb-1", "httpbin.org", 90, budget)
    verdict = ledger.record("sb-1", "httpbin.org", 90, budget)
    assert verdict.allow
    assert verdict.finding
    assert not ledger.is_quarantined("sb-1")
