# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from policy_review_mcp.jev import JevConfig
from policy_review_mcp.jev_server import create_server as create_jev_server
from policy_review_mcp.prover import ProverConfig
from policy_review_mcp.prover_server import create_server as create_prover_server

PROJECT = Path(__file__).parents[1]
CANDIDATE = "version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["complete", "uncertain", "conflict", "unavailable", "invalid", "empty_task", "unsupported"],
)
async def test_jev_typed_results_cover_success_and_failure(monkeypatch, mode):
    def model(state, questions, config):
        if mode == "unavailable":
            raise RuntimeError("test API unavailable")
        assert mode not in {"invalid", "empty_task", "unsupported"}
        answers = {}
        for key, spec in questions.items():
            if spec["type"] == "score":
                answers[key] = {
                    "type": "score",
                    "value": 0.1,
                    "confidence": 0.9,
                    "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0},
                }
            else:
                value = "none" if key.endswith(".context") else "justified"
                if mode == "conflict" and key.endswith(".justification"):
                    value = "unjustified"
                answers[key] = {
                    "type": "choice",
                    "value": value,
                    "confidence": 0.2 if mode == "uncertain" else 0.9,
                    "probabilities": {
                        label: 0.9 if label == value else 0.1 / (len(spec["criteria"]) - 1)
                        for label in spec["criteria"]
                    },
                }
        return answers

    monkeypatch.setattr("policy_review_mcp.jev.call_typesafe", model)
    server = create_jev_server(JevConfig())
    tool = (await server.list_tools())[0]
    candidate = {"invalid": "[", "unsupported": "version: 1\nprocess: {}\n"}.get(mode, CANDIDATE)
    arguments = {
        "task": "" if mode == "empty_task" else "Read the checkout.",
        "candidate_policy": candidate,
        "execution_context": {},
        "questions": []
        if mode == "unsupported"
        else [
            {
                "id": "mode",
                "pointers": ["/filesystem_policy/read_only/0"],
                "instructions": "Which explanation fits?",
                "criteria": {
                    "justified": "Reading this checkout is needed.",
                    "unneeded": "No reads are needed.",
                },
                "diagnostic_outcomes": {"justified": "justified", "unneeded": "unjustified"},
            }
        ],
    }
    content, structured = await server.call_tool(tool.name, arguments)
    validate(structured, tool.outputSchema)
    assert json.loads(content[0].text) == structured
    assert (
        structured["status"]
        == {
            "complete": "complete",
            "uncertain": "incomplete",
            "conflict": "incomplete",
            "unavailable": "unavailable",
            "invalid": "invalid_input",
            "empty_task": "invalid_input",
            "unsupported": "incomplete",
        }[mode]
    )
    if mode in {"complete", "uncertain"}:
        answer = structured["custom_answers"][0]
        assert answer["diagnostic_status"] == ("aligned" if mode == "complete" else "uncertain")
        assert {"none_fit", "insufficient_context"} <= answer["criteria"].keys()
    if mode == "conflict":
        finding = structured["findings"][0]
        assert finding["reason"] == "permission_not_justified"
        assert finding["locations"][0]["pointer"] == "/filesystem_policy/read_only/0"
        assert finding["actionable_guidance"] is False
        assert finding["blocked_by"] == ["diagnostic_conflict"]
        assert structured["custom_answers"][0]["diagnostic_status"] == "conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,code",
    [
        ("within_boundary", 0),
        ("exceeds_boundary", 1),
        ("unsupported", 3),
        ("inconclusive", 130),
        ("error", 2),
        ("malformed", 0),
    ],
)
async def test_prover_typed_results_preserve_verdicts_and_raw_output(tmp_path, result, code):
    executable, boundary = _prover_fixture(tmp_path, result, code)
    server = create_prover_server(ProverConfig(str(executable), boundary))
    tool = (await server.list_tools())[0]
    content, structured = await server.call_tool(tool.name, {"candidate_policy": CANDIDATE})
    validate(structured, tool.outputSchema)
    assert json.loads(content[0].text) == structured
    assert structured["within_boundary"] is (result == "within_boundary")
    assert structured["result"] == ("adapter_error" if result == "malformed" else result)
    if result == "malformed":
        assert structured["prover_report"] == {"schema_version": 999}
    elif result == "exceeds_boundary":
        assert structured["counterexample"]["path"] == "/outside"


@pytest.mark.asyncio
async def test_readme_registrations_work_over_stdio_outside_project(tmp_path):
    """Exercise the documented uv args, not just in-process FastMCP helpers."""
    registrations = next(
        json.loads(block)["mcpServers"]
        for block in re.findall(r"```json\n(.*?)\n```", (PROJECT / "README.md").read_text(), re.S)
        if '"mcpServers"' in block
    )
    executable, boundary = _prover_fixture(tmp_path)
    (tmp_path / "prover.toml").write_text(
        f"executable = {json.dumps(str(executable))}\nboundary = {json.dumps(boundary.name)}\n"
    )
    (tmp_path / "jev.toml").write_text("")  # Defaults, with no live API credentials.
    for suffix, tool_name in (("prover", "check_policy_boundary"), ("jev", "review_delegation")):
        registration = registrations[
            "openshell-policy-prover" if suffix == "prover" else "openshell-delegation-review"
        ]
        args = [
            value.replace("/absolute/path/to/policy-review-mcp", str(PROJECT))
            for value in registration["args"]
        ]
        args[args.index("--config") + 1] = str(tmp_path / f"{suffix}.toml")
        params = StdioServerParameters(
            command=shutil.which(registration["command"]),
            args=args,
            cwd=str(tmp_path),
            env={
                "TYPESAFE_API_KEY": "",
                "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(tmp_path / "uv-cache")),
            },
        )
        async with asyncio.timeout(30), stdio_client(params) as streams:
            async with ClientSession(
                *streams, read_timeout_seconds=timedelta(seconds=10)
            ) as client:
                initialized = await client.initialize()
                assert "candidate_sha256" in initialized.instructions
                assert "neither server" in initialized.instructions.lower()
                tool = (await client.list_tools()).tools[0]
                assert tool.name == tool_name
                assert tool.outputSchema
                assert tool.annotations.readOnlyHint is True
                assert tool.annotations.destructiveHint is False
                assert tool.annotations.openWorldHint is (suffix == "jev")
                assert "not a" in tool.inputSchema["properties"]["candidate_policy"]["description"]
                if suffix == "jev":
                    assert "TypeSafe" in tool.description
                    assert "unsupported policy fields" in tool.description
                    arguments = {"candidate_policy": "[", "task": "Read.", "execution_context": {}}
                else:
                    arguments = {"candidate_policy": CANDIDATE}
                response = await client.call_tool(tool_name, arguments)
                assert response.isError is False
                validate(response.structuredContent, tool.outputSchema)
                assert json.loads(response.content[0].text) == response.structuredContent
                assert response.structuredContent["status"] == (
                    "complete" if suffix == "prover" else "invalid_input"
                )
                assert (
                    response.structuredContent["candidate_sha256"]
                    == hashlib.sha256(arguments["candidate_policy"].encode()).hexdigest()
                )
                if suffix == "jev":
                    assert response.structuredContent["model_request_attempted"] is False
                    invalid = await client.call_tool(
                        tool_name,
                        {
                            "candidate_policy": CANDIDATE,
                            "task": "Read.",
                            "execution_context": {"unknown_field": True},
                        },
                    )
                    assert invalid.isError is True


def _prover_fixture(tmp_path, result="within_boundary", code=0):
    boundary = tmp_path / "boundary.yaml"
    boundary.write_text("version: 1\n")
    raw = (
        {"schema_version": 999}
        if result == "malformed"
        else {
            "schema_version": 1,
            "check": "boundary",
            "prover_version": "test",
            "result": result,
            "exit_code": code,
            "coverage": {"domains": ["filesystem"]},
            "counterexample": {"path": "/outside"} if result == "exceeds_boundary" else None,
        }
    )
    executable = tmp_path / "fake-prover"
    executable.write_text(
        f"#!{sys.executable}\nimport json\nprint(json.dumps({raw!r}))\nraise SystemExit({code})\n"
    )
    executable.chmod(0o700)
    return executable, boundary
