# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable JSON encoding for admission messages and receipt claims."""

from __future__ import annotations

import json

from egress_gate.base import StrictDomainModel


def canonical_json_bytes(value: StrictDomainModel) -> bytes:
    """Encode a validated model with stable UTF-8 JSON semantics."""
    return json.dumps(
        value.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
