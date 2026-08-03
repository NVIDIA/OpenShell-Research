"""Adversarial matching and output tests for the request-rules gate."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import pytest
from pydantic import ValidationError

import egress_gate.gates.request_rules as request_rules_module
from egress_gate.errors import TimeoutExpiredError
from egress_gate.gates import (
    GlobPath,
    RequestRulesConfig,
    RequestRulesGate,
)
from egress_gate.request import (
    HttpHeader,
    HttpRequest,
    HttpTarget,
    Process,
    RequestContext,
)
from egress_gate.result import GateControl
from egress_gate.timeout import Timeout


def _rule(
    name: str,
    match: dict[str, object],
    *,
    decision: str = "allow",
    reason_code: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "name": name,
        "match": match,
        "decision": decision,
    }
    if reason_code is not None:
        value["reason_code"] = reason_code
    return value


def _config(*rules: dict[str, object]) -> RequestRulesConfig:
    return RequestRulesConfig.model_validate({"rules": list(rules)})


def _request(
    *,
    scheme: str = "https",
    host: str = "example.com",
    port: int = 443,
    method: str = "GET",
    path: str = "/",
    query: str = "",
    headers: tuple[HttpHeader, ...] = (),
    process: Process | None = None,
) -> HttpRequest:
    return HttpRequest(
        context=RequestContext(
            request_id="request-1",
            sandbox_id="sandbox-1",
            originating_process=process,
        ),
        target=HttpTarget(
            scheme=scheme,
            host=host,
            port=port,
            method=method,
            path=path,
            query=query,
        ),
        headers=headers,
        body=b"request body is never inspected",
    )


def _evaluate(config: RequestRulesConfig, request: HttpRequest):
    return RequestRulesGate(config, None).evaluate(
        request,
        timeout=Timeout.from_seconds(1),
    )


def test_config_is_strict_and_normalizes_only_authority_related_names() -> None:
    config = _config(
        _rule(
            "known-request",
            {
                "schemes": ["HTTPS"],
                "hosts": ["EXAMPLE.COM", "2001:0DB8:0:0:0:0:0:1"],
                "ports": [443, 8443],
                "methods": ["GET"],
                "path": {"type": "glob", "value": "/v1/*"},
                "headers_present": ["X-Api-Key"],
                "process_binaries": ["/usr/bin/Node"],
                "ancestor_binaries": ["/usr/bin/sh"],
            },
        )
    )

    match = config.rules[0].match
    assert match.schemes == ("https",)
    assert match.hosts == ("example.com", "2001:db8::1")
    assert match.ports == (443, 8443)
    assert match.methods == ("GET",)
    assert isinstance(match.path, GlobPath)
    assert match.path.value == "/v1/*"
    assert match.headers_present == ("x-api-key",)
    assert match.process_binaries == ("/usr/bin/Node",)
    assert match.ancestor_binaries == ("/usr/bin/sh",)


@pytest.mark.parametrize(
    "values",
    [
        {"rules": [_rule("empty", {})]},
        {"rules": [_rule("empty", {"methods": []})]},
        {"rules": [_rule("bad-path", {"path": {"type": "regex", "value": "/"}})]},
        {"rules": [_rule("bad-method", {"methods": ["GET "]})]},
        {"rules": [_rule("bad-port", {"ports": [0]})]},
        {
            "rules": [
                _rule(
                    "bad-path-delimiter",
                    {"path": {"type": "exact", "value": "/x?y"}},
                )
            ]
        },
        {
            "rules": [
                {
                    "name": "allow-with-reason",
                    "match": {"methods": ["GET"]},
                    "decision": "allow",
                    "reason_code": "not_allowed_here",
                }
            ]
        },
        {
            "rules": [
                {
                    "name": "deny-without-reason",
                    "match": {"methods": ["GET"]},
                    "decision": "deny",
                }
            ]
        },
        {
            "rules": [
                {
                    "name": "unknown-field",
                    "match": {"methods": ["GET"], "no_match": "deny"},
                    "decision": "allow",
                }
            ]
        },
    ],
)
def test_config_rejects_empty_or_ambiguous_shapes(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RequestRulesConfig.model_validate(values)


def test_config_rejects_duplicate_names_and_more_than_256_rules() -> None:
    duplicate = (
        _rule("same", {"methods": ["GET"]}),
        _rule("same", {"methods": ["POST"]}),
    )
    with pytest.raises(ValidationError):
        _config(*duplicate)

    too_many = tuple(
        _rule(f"rule-{index}", {"methods": ["GET"]}) for index in range(257)
    )
    with pytest.raises(ValidationError):
        _config(*too_many)


@pytest.mark.parametrize(
    "match",
    [
        {"schemes": ["HTTPS", "https"]},
        {"hosts": ["EXAMPLE.COM", "example.com"]},
        {"ports": [443, 443]},
        {"methods": ["GET", "GET"]},
        {"headers_present": ["X-Token", "x-token"]},
        {"process_binaries": ["/bin/node", "/bin/node"]},
    ],
)
def test_config_rejects_duplicate_match_values_after_normalization(
    match: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _config(_rule("duplicates", match))


def test_each_match_value_tuple_is_bounded_at_64_items() -> None:
    with pytest.raises(ValidationError):
        _config(
            _rule(
                "too-many-methods",
                {"methods": [f"M{index}" for index in range(65)]},
            )
        )


def test_glob_wildcards_are_bounded_at_64() -> None:
    with pytest.raises(ValidationError):
        _config(
            _rule(
                "too-many-wildcards",
                {"path": {"type": "glob", "value": "*" * 65}},
            )
        )


@pytest.mark.parametrize(
    "host",
    [
        "user@example.com",
        "example.com:443",
        "https://example.com",
        "[2001:db8::1]",
        "[2001:db8::1]:443",
        "ｅxample.com",
    ],
)
def test_config_rejects_non_authority_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        _config(_rule("invalid-host", {"hosts": [host]}))


def test_all_match_fields_are_conjunctive_and_tuple_values_are_or() -> None:
    config = _config(
        _rule(
            "all-facts",
            {
                "schemes": ["http", "HTTPS"],
                "hosts": ["wrong.example", "EXAMPLE.COM"],
                "ports": [80, 443],
                "methods": ["HEAD", "GET"],
                "path": {"type": "prefix", "value": "/api"},
                "headers_present": ["X-One", "x-two"],
                "process_binaries": ["/usr/bin/node", "/usr/bin/python"],
                "ancestor_binaries": ["/bin/sh", "/usr/bin/env"],
            },
        )
    )
    request = _request(
        scheme="HTTPS",
        method="GET",
        path="/api/items",
        headers=(
            HttpHeader(name="X-One", value="secret-header-value"),
            HttpHeader(name="X-Two", value="another-secret"),
            HttpHeader(name="X-Two", value="repeated"),
        ),
        process=Process(
            binary="/usr/bin/node",
            pid=7,
            ancestors=("/usr/bin/env",),
        ),
    )

    evaluation = _evaluate(config, request)

    assert evaluation.control is GateControl.ALLOW
    assert evaluation.findings[0].label == "all-facts"

    missing_header = _request(
        scheme="HTTPS",
        method="GET",
        path="/api/items",
        headers=(HttpHeader(name="X-One", value="present"),),
        process=Process(binary="/usr/bin/node", pid=7, ancestors=("/bin/sh",)),
    )
    assert _evaluate(config, missing_header).control is GateControl.PROCEED


def test_normalization_does_not_change_case_sensitive_facts() -> None:
    config = _config(
        _rule(
            "case-sensitive",
            {
                "schemes": ["HTTPS"],
                "hosts": ["Example.COM"],
                "methods": ["GET"],
                "path": {"type": "exact", "value": "/Exact"},
                "process_binaries": ["/usr/bin/Node"],
            },
        )
    )
    matching = _request(
        scheme="https",
        host="example.com",
        method="GET",
        path="/Exact",
        process=Process(binary="/usr/bin/Node", pid=1),
    )
    assert _evaluate(config, matching).control is GateControl.ALLOW

    for changed in (
        _request(
            method="get", path="/Exact", process=Process(binary="/usr/bin/Node", pid=1)
        ),
        _request(path="/exact", process=Process(binary="/usr/bin/Node", pid=1)),
        _request(path="/Exact", process=Process(binary="/usr/bin/node", pid=1)),
    ):
        assert _evaluate(config, changed).control is GateControl.PROCEED


def test_authority_matching_canonicalizes_ip_literals_but_not_ports_or_dots() -> None:
    config = _config(
        _rule(
            "ipv6",
            {"hosts": ["2001:0DB8:0:0:0:0:0:1"], "ports": [443]},
        ),
        _rule("dns", {"hosts": ["example.com"]}),
    )

    assert (
        _evaluate(
            config,
            _request(host="2001:DB8::1", port=443),
        )
        .findings[0]
        .label
        == "ipv6"
    )
    assert (
        _evaluate(config, _request(host="example.com.", port=443)).findings[0].label
        == "dns"
    )
    assert (
        _evaluate(
            config,
            _request(host="example.com:443", port=443),
        ).control
        is GateControl.PROCEED
    )
    assert (
        _evaluate(
            config,
            _request(host="example.com", port=8443),
        )
        .findings[0]
        .label
        == "dns"
    )


@pytest.mark.parametrize(
    ("path", "query", "expected"),
    [
        ("/api/../secret", "", False),
        ("/api%2Fsecret", "", False),
        ("//api/secret", "", False),
        ("/other", "/api/secret", False),
        ("/api/secret", "anything", True),
    ],
)
def test_exact_paths_do_not_decode_normalize_or_include_queries(
    path: str,
    query: str,
    expected: bool,
) -> None:
    config = _config(
        _rule("exact-path", {"path": {"type": "exact", "value": "/api/secret"}})
    )
    evaluation = _evaluate(config, _request(path=path, query=query))
    assert (evaluation.control is GateControl.ALLOW) is expected


def test_glob_supports_only_literal_characters_and_star_including_slashes() -> None:
    config = _config(
        _rule(
            "glob-path",
            {"path": {"type": "glob", "value": "/repos/*/issues"}},
        )
    )
    assert _evaluate(config, _request(path="/repos/acme/project/issues")).control is (
        GateControl.ALLOW
    )
    assert _evaluate(config, _request(path="/repos/acme/project/pulls")).control is (
        GateControl.PROCEED
    )

    with pytest.raises(ValidationError):
        _config(_rule("query-delimiter", {"path": {"type": "glob", "value": "/x?"}}))
    with pytest.raises(ValidationError):
        _config(_rule("fragment-delimiter", {"path": {"type": "glob", "value": "/x#"}}))


def test_process_matching_is_exact_and_does_not_use_basenames() -> None:
    config = _config(
        _rule(
            "process-rule",
            {
                "process_binaries": ["/usr/bin/node", "/usr/bin/python"],
                "ancestor_binaries": ["/bin/sh", "/usr/bin/env"],
            },
        )
    )
    matching = _request(
        process=Process(binary="/usr/bin/node", pid=10, ancestors=("/usr/bin/env",))
    )
    assert _evaluate(config, matching).control is GateControl.ALLOW
    assert (
        _evaluate(
            config,
            _request(process=Process(binary="node", pid=10, ancestors=("/bin/sh",))),
        ).control
        is GateControl.PROCEED
    )
    assert _evaluate(config, _request()).control is GateControl.PROCEED


def test_deny_precedence_and_ordered_winners_are_explicit() -> None:
    config = _config(
        _rule("allow-first", {"methods": ["GET"]}, decision="allow"),
        _rule(
            "deny-first-in-denies",
            {"methods": ["GET"]},
            decision="deny",
            reason_code="first_deny",
        ),
        _rule(
            "deny-second-in-denies",
            {"methods": ["GET"]},
            decision="deny",
            reason_code="second_deny",
        ),
    )
    evaluation = _evaluate(config, _request())
    assert evaluation.control is GateControl.DENY
    assert evaluation.reason_code == "first_deny"
    assert evaluation.findings[0].label == "deny-first-in-denies"

    allows = _config(
        _rule("allow-first", {"methods": ["GET"]}, decision="allow"),
        _rule("allow-second", {"methods": ["GET"]}, decision="allow"),
    )
    allow_evaluation = _evaluate(allows, _request())
    assert allow_evaluation.control is GateControl.ALLOW
    assert allow_evaluation.findings[0].label == "allow-first"

    no_match = _evaluate(allows, _request(method="POST"))
    assert no_match.control is GateControl.PROCEED
    assert no_match.findings == ()


def test_winner_emits_one_content_safe_five_field_finding() -> None:
    config = _config(
        _rule(
            "deny-secret-route",
            {"path": {"type": "prefix", "value": "/private"}},
            decision="deny",
            reason_code="private_route_denied",
        )
    )
    evaluation = _evaluate(
        config,
        _request(
            path="/private/secret-token",
            query="body=secret-token",
            headers=(HttpHeader(name="X-Token", value="secret-token"),),
        ),
    )

    assert evaluation.patch.is_empty
    assert len(evaluation.findings) == 1
    assert evaluation.findings[0].model_dump(exclude_none=True) == {
        "type": "request_rule_match",
        "label": "deny-secret-route",
        "count": 1,
        "severity": "deny",
    }
    assert "secret-token" not in str(evaluation.findings[0].model_dump())


def test_glob_matching_checks_the_shared_timeout() -> None:
    with pytest.raises(TimeoutExpiredError):
        request_rules_module._glob_matches(
            "*",
            "path",
            Timeout(deadline=monotonic() - 1),
        )


def test_gate_is_safe_for_concurrent_evaluations() -> None:
    gate = RequestRulesGate(
        _config(_rule("allow-get", {"methods": ["GET"]})),
        None,
    )

    def evaluate(_: int) -> GateControl:
        return gate.evaluate(_request(), timeout=Timeout.from_seconds(1)).control

    with ThreadPoolExecutor(max_workers=8) as executor:
        controls = tuple(executor.map(evaluate, range(32)))

    assert controls == (GateControl.ALLOW,) * 32
