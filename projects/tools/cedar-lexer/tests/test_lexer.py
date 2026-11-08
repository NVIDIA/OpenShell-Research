# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Error, Keyword, Name, Number, Operator, String

from openshell_research_cedar_lexer import CedarLexer


def test_lexer_is_registered_with_pygments():
    assert get_lexer_by_name("cedar").__class__ is CedarLexer
    assert get_lexer_by_name("dogwood").__class__ is CedarLexer
    assert get_lexer_for_filename("policy.cedar").__class__ is CedarLexer
    assert get_lexer_for_filename("policy.dw").__class__ is CedarLexer


def test_unrecognized_characters_are_errors():
    assert (Error, "#") in _tokens("permit (principal, action, resource) # comment;")


def test_cedar_syntax_tokens():
    tokens = _tokens(
        """
        @id("p1")
        forbid (principal is App::User, action in [App::Action::"view"], resource)
        unless { context.ip.isInRange(ip("10.0.0.0/8")) && resource has owner };
        """
    )
    assert (Name.Decorator, "@id") in tokens
    assert (Keyword, "forbid") in tokens
    assert (Keyword, "unless") in tokens
    assert (Operator.Word, "is") in tokens
    assert (Operator.Word, "has") in tokens
    assert (Name.Builtin.Pseudo, "principal") in tokens
    assert (Name.Namespace, "App") in tokens
    assert (Name.Attribute, "ip") in tokens
    assert (Name.Function, "isInRange") in tokens
    assert (Name.Function, "ip") in tokens


def test_string_escapes():
    tokens = _tokens(r'context.path like "/a\*b\u{2603}"')
    assert (String.Escape, r"\*") in tokens
    assert (String.Escape, r"\u{2603}") in tokens


def test_dogwood_temporal_tokens():
    tokens = _tokens(
        """
        def temporal approved_recently(?w) {
            formerly within ?w App::Action::"Approve"::response{}
        };
        permit (principal, action, resource)
        when temporal {
            count for (e: Event). where previous within 24h App::Action::"Login"::request{} > 0
        }
        unless temporal {
            !App::Action::"Login"::request{}
                since within 1 h App::Action::"Logout"::request{}
        };
        """
    )
    assert (Name.Function, "approved_recently") in tokens
    assert (Name.Variable, "?w") in tokens
    for keyword in (
        "def",
        "temporal",
        "formerly",
        "previous",
        "since",
        "within",
        "count",
        "for",
        "where",
    ):
        assert (Keyword, keyword) in tokens, keyword
    assert (Number.Integer, "24h") in tokens
    assert (Number.Integer, "1 h") in tokens


def _tokens(source):
    return list(get_lexer_by_name("cedar").get_tokens(source))
