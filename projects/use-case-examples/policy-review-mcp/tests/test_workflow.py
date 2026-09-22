# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib

from policy_review_mcp.workflow import run_ordered_review


def _sha(candidate: str) -> str:
    return hashlib.sha256(candidate.encode()).hexdigest()


def test_failed_boundary_skips_jev() -> None:
    calls = 0

    def jev(candidate):
        nonlocal calls
        calls += 1
        return {}

    report = run_ordered_review(
        "version: 1\n",
        lambda candidate: {"status": "complete", "within_boundary": False},
        jev,
    )
    assert calls == 0
    assert report["jev"]["status"] == "not_assessed"


def test_matching_candidate_reports_combine() -> None:
    candidate = "version: 1\n"
    fingerprint = _sha(candidate)
    report = run_ordered_review(
        candidate,
        lambda value: {
            "status": "complete",
            "within_boundary": True,
            "candidate_sha256": fingerprint,
        },
        lambda value: {"status": "complete", "candidate_sha256": fingerprint},
    )
    assert report["combined"] is True


def test_mismatched_candidate_reports_do_not_combine() -> None:
    report = run_ordered_review(
        "version: 1\n",
        lambda value: {
            "status": "complete",
            "within_boundary": True,
            "candidate_sha256": "first",
        },
        lambda value: {"status": "complete", "candidate_sha256": "second"},
    )
    assert report["combined"] is False
    assert report["reason"] == "candidate_fingerprint_mismatch"
