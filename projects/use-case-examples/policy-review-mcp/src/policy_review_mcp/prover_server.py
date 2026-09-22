# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stdio MCP entrypoint for deterministic OpenShell boundary checks."""

import argparse
import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from policy_review_mcp.mcp_guidance import ORDERED_WORKFLOW, PROVER_DESCRIPTION
from policy_review_mcp.prover import ProverConfig, check_policy_boundary
from policy_review_mcp.results import ProverReport


def create_server(config: ProverConfig) -> FastMCP:
    server = FastMCP(
        "OpenShell Policy Prover", instructions=PROVER_DESCRIPTION + "\n" + ORDERED_WORKFLOW
    )

    @server.tool(
        name="check_policy_boundary",
        description=PROVER_DESCRIPTION + "\n" + ORDERED_WORKFLOW,
        annotations=ToolAnnotations(
            title="Check OpenShell policy boundary",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def check_policy_boundary_tool(
        candidate_policy: Annotated[
            str,
            Field(
                description=(
                    "Complete candidate OpenShell YAML text, not a path or diff. "
                    "Preserve exact bytes for JEV fingerprint comparison."
                )
            ),
        ],
    ) -> ProverReport:

        return ProverReport.model_validate(check_policy_boundary(candidate_policy, config))

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=os.environ.get("POLICY_REVIEW_PROVER_CONFIG", "prover.toml"),
        type=Path,
    )
    args = parser.parse_args()
    create_server(ProverConfig.load(args.config.resolve())).run(transport="stdio")


if __name__ == "__main__":
    main()
