# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

from policy_review_mcp.jev import JevConfig
from policy_review_mcp.jev_server import create_server


@pytest.mark.asyncio
async def test_tool_schema_exposes_nested_request_contracts() -> None:
    tools = await create_server(JevConfig()).list_tools()
    schema = tools[0].inputSchema
    definitions = schema["$defs"]

    assert schema["properties"]["execution_context"]["$ref"].endswith("/ExecutionContext")
    assert schema["properties"]["annotations"]["anyOf"][0]["items"]["$ref"].endswith(
        "/FieldAnnotation"
    )
    assert schema["properties"]["questions"]["anyOf"][0]["items"]["$ref"].endswith(
        "/TargetedQuestion"
    )
    assert set(definitions["ExecutionContext"]["properties"]) == {
        "intended_tools",
        "prepared_inputs",
        "installed_dependencies",
        "output_locations",
        "scratch_locations",
        "runtime_requirements",
    }
    assert definitions["ExecutionContext"]["additionalProperties"] is False
