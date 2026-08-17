# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared resources prepared by an agent harness."""

import tempfile
from dataclasses import dataclass


@dataclass
class PreparedResources:
    temporary: tempfile.TemporaryDirectory[str]
    uploads: tuple[str, ...]
    arguments: tuple[str, ...]

    def close(self) -> None:
        self.temporary.cleanup()
