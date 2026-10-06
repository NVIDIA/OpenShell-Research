# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Caller-owned ordered review workflow."""

from collections.abc import Callable
from typing import Any


def run_ordered_review(
    candidate_policy: str,
    prover_call: Callable[[str], dict[str, Any]],
    jev_call: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Run JEV only after a conclusive pass and combine only matching hashes."""

    prover = prover_call(candidate_policy)
    if prover.get("status") != "complete" or not prover.get("within_boundary"):
        return {"prover": prover, "jev": {"status": "not_assessed"}, "combined": False}
    jev = jev_call(candidate_policy)
    if prover.get("candidate_sha256") != jev.get("candidate_sha256"):
        return {
            "prover": prover,
            "jev": jev,
            "combined": False,
            "reason": "candidate_fingerprint_mismatch",
        }
    return {"prover": prover, "jev": jev, "combined": True}
