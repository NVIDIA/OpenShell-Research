# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate and render the deliberately small OAR prompt template syntax."""

from __future__ import annotations

import re
from collections.abc import Mapping, Set

BUILTIN_PROMPT_VARIABLES = frozenset(
    {
        "oar.input_name",
        "oar.input_path",
    }
)
PROMPT_VARIABLE_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
_PLACEHOLDER_PATTERN = re.compile(r"{{\s*((?:oar\.)?[a-z][a-z0-9_]{0,62})\s*}}")


def validate_prompt_template(
    template: str,
    declared_variables: Set[str],
    available_builtins: Set[str],
) -> None:
    placeholders = _prompt_placeholders(template)
    unknown = sorted(placeholders - declared_variables - available_builtins)
    if unknown:
        raise ValueError(f"unknown prompt template variables: {unknown}")
    unused = sorted(declared_variables - placeholders)
    if unused:
        raise ValueError(f"unused prompt variable declarations: {unused}")


def render_prompt_template(template: str, values: Mapping[str, str]) -> str:
    placeholders = _prompt_placeholders(template)
    missing = sorted(placeholders - values.keys())
    if missing:
        raise ValueError(f"missing prompt template variables: {missing}")
    return _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)


def _prompt_placeholders(template: str) -> set[str]:
    placeholders = {match.group(1) for match in _PLACEHOLDER_PATTERN.finditer(template)}
    unmatched = _PLACEHOLDER_PATTERN.sub("", template)
    if "{{" in unmatched or "}}" in unmatched:
        raise ValueError("malformed prompt template placeholder")
    return placeholders
