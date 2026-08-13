# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared immutable values for parsed request text and its replacements."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextTarget:
    """One independently inspected text value and its content-local identity."""

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class TextReplacement:
    """The complete replacement text for one parsed target."""

    target_id: str
    text: str


__all__ = ["TextReplacement", "TextTarget"]
