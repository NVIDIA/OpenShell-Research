# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openshell_agent_runner.prompt_templates import (
    render_prompt_template,
    validate_prompt_template,
)


def test_multiple_prompt_variables_are_rendered_literally() -> None:
    template = "Path: {{ oar.input_path }}\nFocus: {{ focus }}\nContext: {{context}}\n"
    values = {
        "oar.input_path": "/workspace/input/repository",
        "focus": "src/auth and tests/auth",
        "context": "Use $HOME and `rm` as literal text.",
    }

    assert render_prompt_template(template, values) == (
        "Path: /workspace/input/repository\n"
        "Focus: src/auth and tests/auth\n"
        "Context: Use $HOME and `rm` as literal text.\n"
    )


def test_prompt_template_validation_rejects_unknown_and_unused_variables() -> None:
    with pytest.raises(ValueError, match="unknown.*missing"):
        validate_prompt_template("{{ missing }}", set(), set())
    with pytest.raises(ValueError, match="unused.*context"):
        validate_prompt_template("{{ focus }}", {"focus", "context"}, set())


def test_prompt_template_rejects_missing_values_and_malformed_placeholders() -> None:
    with pytest.raises(ValueError, match="missing.*focus"):
        render_prompt_template("{{ focus }}", {})
    with pytest.raises(ValueError, match="malformed"):
        render_prompt_template("{{ focus-name }}", {"focus-name": "value"})
