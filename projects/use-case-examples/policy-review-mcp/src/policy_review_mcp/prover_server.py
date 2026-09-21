"""Stdio MCP entrypoint for deterministic OpenShell boundary checks."""

import argparse
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from policy_review_mcp.prover import ProverConfig, check_policy_boundary


def create_server(config: ProverConfig) -> FastMCP:
    server = FastMCP("OpenShell Policy Prover")

    @server.tool(name="check_policy_boundary")
    def check_policy_boundary_tool(candidate_policy: str) -> dict:
        """Check complete candidate YAML against the configured operator boundary."""

        return check_policy_boundary(candidate_policy, config)

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
