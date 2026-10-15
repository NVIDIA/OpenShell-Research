# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stdio MCP entrypoint for task-aware JEV policy review."""

import argparse
import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from policy_review_mcp.contracts import (
    ExecutionContext,
    FieldAnnotation,
    ReviewRequest,
    TargetedQuestion,
)
from policy_review_mcp.jev import JevConfig, invalid_request_report, review_delegation
from policy_review_mcp.mcp_guidance import JEV_DESCRIPTION, ORDERED_WORKFLOW
from policy_review_mcp.results import JevReport


def create_server(config: JevConfig) -> FastMCP:
    server = FastMCP(
        "OpenShell Delegation Review", instructions=JEV_DESCRIPTION + "\n" + ORDERED_WORKFLOW
    )

    @server.tool(
        name="review_delegation",
        description=JEV_DESCRIPTION + "\n" + ORDERED_WORKFLOW,
        annotations=ToolAnnotations(
            title="Review OpenShell task fit with JEV",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    def review_delegation_tool(
        task: Annotated[
            str, Field(description="Exact delegated assignment, not a broader project objective.")
        ],
        candidate_policy: Annotated[
            str,
            Field(
                description=(
                    "Complete candidate OpenShell YAML text, not a path or diff. Use "
                    "the exact bytes checked by the prover."
                )
            ),
        ],
        execution_context: Annotated[
            ExecutionContext,
            Field(
                description=(
                    "Known runtime facts; {} is allowed when unknown. Do not invent "
                    "dependencies or write requirements."
                )
            ),
        ],
        annotations: Annotated[
            list[FieldAnnotation] | None,
            Field(
                description=(
                    "Optional caller rationale and change labels at JSON pointers; not "
                    "evidence of need."
                )
            ),
        ] = None,
        starting_policy: Annotated[
            str | None,
            Field(
                description=(
                    "Optional complete prior policy YAML for comparison; not the operator boundary."
                )
            ),
        ] = None,
        questions: Annotated[
            list[TargetedQuestion] | None,
            Field(
                description=(
                    "Optional independent Choice questions in the same batch. "
                    "Diagnostic mode requires one exact supported target per question."
                )
            ),
        ] = None,
    ) -> JevReport:

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
            return JevReport.model_validate(
                invalid_request_report(
                    task=task,
                    candidate_policy=candidate_policy,
                    execution_context=execution_context.model_dump(mode="json"),
                    annotations=[item.model_dump(mode="json") for item in annotations or []],
                    starting_policy=starting_policy,
                    questions=[item.model_dump(mode="json") for item in questions or []],
                    config=config,
                    reason=str(error),
                )
            )
        return JevReport.model_validate(review_delegation(request, config))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JEV MCP server; requires TYPESAFE_API_KEY in the server process environment."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("POLICY_REVIEW_JEV_CONFIG", "jev.toml"),
        type=Path,
    )
    args = parser.parse_args()
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        parser.error(
            "TYPESAFE_API_KEY is missing or blank. Supply it in the environment of the process "
            "that launches this JEV MCP server, then restart the server. "
            "Shell profiles and secret files are not loaded automatically."
        )
    create_server(JevConfig.load(args.config.resolve())).run(transport="stdio")


if __name__ == "__main__":
    main()
