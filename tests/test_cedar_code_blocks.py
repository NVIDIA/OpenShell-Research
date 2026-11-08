# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

import pytest
from pygments.lexers import get_lexer_by_name
from pygments.token import Error

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
CEDAR_FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[ \t]*(?:\{[ \t]*\.)?(?:cedar|dogwood)(?![\w-])[^\n]*\n(.*?)^[ \t]*\1",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
PAGES_WITH_CEDAR = sorted(
    path for path in DOCS_DIR.rglob("*.md") if CEDAR_FENCE.search(path.read_text(encoding="utf-8"))
)


@pytest.mark.parametrize("path", PAGES_WITH_CEDAR, ids=lambda path: path.name)
def test_cedar_code_blocks_have_no_lexing_errors(path):
    lexer = get_lexer_by_name("cedar")
    for _, block in CEDAR_FENCE.findall(path.read_text(encoding="utf-8")):
        errors = [value for token, value in lexer.get_tokens(block) if token in Error]
        assert errors == [], errors
