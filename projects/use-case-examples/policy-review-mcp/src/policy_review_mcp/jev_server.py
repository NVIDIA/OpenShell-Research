# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stdio MCP entrypoint for task-aware JEV policy review."""

import argparse
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from policy_review_mcp.contracts import (
    ExecutionContext,
    FieldAnnotation,
    ReviewRequest,
    TargetedQuestion,
)
from policy_review_mcp.jev import JevConfig, invalid_request_report, review_delegation


def create_server(config: JevConfig) -> FastMCP:
    server = FastMCP("OpenShell Delegation Review")

    @server.tool(name="review_delegation")
    def review_delegation_tool(
        task: str,
        candidate_policy: str,
        execution_context: ExecutionContext,
        annotations: list[FieldAnnotation] | None = None,
        starting_policy: str | None = None,
        questions: list[TargetedQuestion] | None = None,
    ) -> dict:
        """Assess task fit for supported permissions; this is not a containment proof."""

        try:
            request = ReviewRequest(
                task=task,
                candidate_policy=candidate_policy,
                execution_context=execution_context,
                annotations=annotations or [],
                starting_policy=starting_policy,
                questions=questions or [],
            )
        except ValidationError as error:
            return invalid_request_report(
                task=task,
                candidate_policy=candidate_policy,
                execution_context=execution_context.model_dump(mode="json"),
                annotations=[item.model_dump(mode="json") for item in annotations or []],
                starting_policy=starting_policy,
                questions=[item.model_dump(mode="json") for item in questions or []],
                config=config,
                reason=str(error),
            )
        return review_delegation(request, config)

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=os.environ.get("POLICY_REVIEW_JEV_CONFIG", "jev.toml"),
        type=Path,
    )
    args = parser.parse_args()
    create_server(JevConfig.load(args.config.resolve())).run(transport="stdio")


if __name__ == "__main__":
    main()
