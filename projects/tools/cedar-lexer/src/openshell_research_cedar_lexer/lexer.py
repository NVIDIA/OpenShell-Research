# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pygments lexer for Cedar policies, including Dogwood's temporal extension."""

from pygments.lexer import RegexLexer, bygroups, words
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Whitespace,
)

IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"


class CedarLexer(RegexLexer):
    """Highlight Cedar policies and the Dogwood keywords layered on them."""

    name = "Cedar"
    aliases = ["cedar", "dogwood"]
    filenames = ["*.cedar", "*.dw"]
    mimetypes = ["text/x-cedar"]
    url = "https://www.cedarpolicy.com/"

    tokens = {
        "root": [
            (r"\s+", Whitespace),
            (r"//.*?$", Comment.Single),
            (rf"@{IDENTIFIER}", Name.Decorator),
            (r'"', String.Double, "string"),
            (
                rf"(def)(\s+)(cedar|temporal)(\s+)({IDENTIFIER})",
                bygroups(Keyword, Whitespace, Keyword, Whitespace, Name.Function),
            ),
            (
                r"(within)(\s+)(-?\d+\s*[smhd])\b",
                bygroups(Keyword, Whitespace, Number.Integer),
            ),
            (rf"{IDENTIFIER}(?=\s*::)", Name.Namespace),
            (rf"(\.)({IDENTIFIER})(?=\s*\()", bygroups(Punctuation, Name.Function)),
            (rf"(\.)({IDENTIFIER})", bygroups(Punctuation, Name.Attribute)),
            (
                words(
                    ("permit", "forbid", "when", "unless", "if", "then", "else"),
                    suffix=r"\b",
                ),
                Keyword,
            ),
            (words(("in", "has", "like", "is"), suffix=r"\b"), Operator.Word),
            (words(("true", "false"), suffix=r"\b"), Keyword.Constant),
            (
                words(("principal", "action", "resource", "context"), suffix=r"\b"),
                Name.Builtin.Pseudo,
            ),
            (
                words(
                    (
                        "temporal",
                        "guardrails",
                        "formerly",
                        "previous",
                        "since",
                        "within",
                        "exists",
                        "count",
                        "sum",
                        "for",
                        "where",
                        "tp",
                    ),
                    suffix=r"\b",
                ),
                Keyword,
            ),
            (rf"[?$]{IDENTIFIER}", Name.Variable),
            (rf"{IDENTIFIER}(?=\s*\()", Name.Function),
            (IDENTIFIER, Name),
            (r"\d+", Number.Integer),
            (r"==|!=|<=|>=|&&|\|\||[<>!=+\-*/%]", Operator),
            (r"::|[(){}\[\],;:.]", Punctuation),
        ],
        "string": [
            (r"\\(u\{[0-9A-Fa-f]{1,6}\}|.)", String.Escape),
            (r'[^"\\]+', String.Double),
            (r'"', String.Double, "#pop"),
        ],
    }
