# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the ordered demo against two independently configured stdio MCP servers."""

import argparse
import asyncio
import json
import os
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ruamel.yaml import YAML


def _structured(result: Any) -> dict[str, Any]:
    if result.structuredContent:
        return dict(result.structuredContent)
    for block in result.content:
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise RuntimeError("tool returned no structured or JSON text content")


def _exception_message(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return _exception_message(error.exceptions[0])
    return f"{type(error).__name__}: {error}"


async def _session(
    stack: AsyncExitStack,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> ClientSession:
    streams = await stack.enter_async_context(
        stdio_client(StdioServerParameters(command=command, args=args, env=env))
    )
    session = await stack.enter_async_context(
        ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30))
    )
    await session.initialize()
    return session


async def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    scenario_data = YAML(typ="safe").load(args.scenarios.read_text())
    scenario = scenario_data["scenarios"][args.scenario]
    candidate = (args.scenarios.parent / scenario["candidate"]).read_text()
    prover_env = os.environ.copy()
    prover_env.pop("TYPESAFE_API_KEY", None)
    prover_env.pop("TYPESAFEAI_API_KEY", None)
    async with AsyncExitStack() as stack:
        prover = await _session(
            stack,
            "policy-review-prover-mcp",
            ["--config", str(args.prover_config)],
            prover_env,
        )
        proof = _structured(
            await prover.call_tool("check_policy_boundary", {"candidate_policy": candidate})
        )
        if proof.get("status") != "complete" or not proof.get("within_boundary"):
            return {
                "prover": proof,
                "jev": {"status": "not_assessed"},
                "combined": False,
                "timings_ms": {"end_to_end": round((time.perf_counter() - started) * 1000, 3)},
            }
        jev = await _session(
            stack,
            "policy-review-jev-mcp",
            ["--config", str(args.jev_config)],
            os.environ.copy(),
        )
        review = _structured(
            await jev.call_tool(
                "review_delegation",
                {
                    "task": scenario["task"],
                    "candidate_policy": candidate,
                    "execution_context": scenario["execution_context"],
                    "annotations": scenario.get("annotations", []),
                    "questions": scenario.get("questions", []),
                },
            )
        )
        matching = proof["candidate_sha256"] == review["candidate_sha256"]
        return {
            "prover": proof,
            "jev": review,
            "combined": matching,
            "timings_ms": {"end_to_end": round((time.perf_counter() - started) * 1000, 3)},
            **({} if matching else {"reason": "candidate_fingerprint_mismatch"}),
        }


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=[
            "read_issue_broad",
            "read_issue_narrow",
            "publish_comment",
            "prepared_checkout_review",
            "outside_boundary",
            "vague_assignment",
            "misleading_rationale",
            "dynamic_write_choice",
        ],
    )
    parser.add_argument("--scenarios", type=Path, default=root / "fixtures/scenarios.yaml")
    parser.add_argument("--prover-config", type=Path, default=root.parent / "prover.toml")
    parser.add_argument("--jev-config", type=Path, default=root.parent / "jev.toml")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as error:
        print(json.dumps({"status": "runner_error", "reason": _exception_message(error)}, indent=2))
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
