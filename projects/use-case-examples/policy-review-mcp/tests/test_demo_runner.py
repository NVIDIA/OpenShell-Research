# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

DEMO = Path(__file__).parents[1] / "demo"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("demo_runner", DEMO / "run_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_flag_preserves_machine_readable_output(runner, monkeypatch, capsys) -> None:
    report = {"prover": {"status": "adapter_error"}, "jev": {"status": "not_assessed"}}

    async def run(args):
        return report

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "read_issue_broad", "--json"])
    runner.main()
    assert json.loads(capsys.readouterr().out) == report


def test_default_output_is_readable_and_runner_errors_exit_nonzero(
    runner, monkeypatch, capsys
) -> None:
    async def run(args):
        raise RuntimeError("server could not start")

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "read_issue_broad"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "Demo could not finish" in output
    assert "server could not start" in output


def test_details_flag_routes_to_diagnostic_renderer(runner, monkeypatch) -> None:
    async def run(args):
        return {"jev": {"status": "complete"}}

    received = []
    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(
        runner, "print_review_report", lambda report, **kwargs: received.append(kwargs)
    )
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "read_issue_narrow", "--details"])
    runner.main()
    assert received[0]["details"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("passed", [False, True])
async def test_actual_runner_gates_jev_and_routes_service_logs(runner, monkeypatch, passed) -> None:
    calls = []
    reports = [
        {"status": "complete", "within_boundary": passed, "candidate_sha256": "same"},
        {"status": "complete", "candidate_sha256": "same"},
    ]

    async def session(stack, command, args, env, errlog):
        calls.append(command)
        if len(calls) == 1:
            assert "TYPESAFE_API_KEY" not in env
        else:
            assert env["TYPESAFE_API_KEY"] == "test-key"
        assert errlog is not sys.stderr
        reply = reports[len(calls) - 1]

        async def call_tool(name, payload):
            return SimpleNamespace(structuredContent=reply)

        return SimpleNamespace(call_tool=call_tool)

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(runner, "_session", session)
    report = await runner.run(
        argparse.Namespace(
            scenarios=DEMO / "fixtures/scenarios.yaml",
            scenario="read_issue_broad",
            prover_config="prover.toml",
            jev_config="jev.toml",
            verbose=False,
        )
    )
    assert len(calls) == (2 if passed else 1)
    assert report["combined"] is passed
    if not passed:
        assert report["jev"]["status"] == "not_assessed"
