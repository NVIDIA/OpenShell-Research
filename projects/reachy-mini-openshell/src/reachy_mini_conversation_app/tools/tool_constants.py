# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from enum import Enum


class ToolState(Enum):
    """Status of a background tool."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SystemTool(Enum):
    """System tools are tools that are used to manage the background tool manager."""

    TASK_STATUS = "task_status"
    TASK_CANCEL = "task_cancel"
