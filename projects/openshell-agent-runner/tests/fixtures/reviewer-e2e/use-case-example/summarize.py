# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Aggregate fixed fixture records; no external operations."""

import json
from pathlib import Path

durations = json.loads(Path(__file__).with_name("durations.json").read_text())
summary = {"runs": len(durations), "total_seconds": sum(durations)}
assert summary == {"runs": 3, "total_seconds": 12}
print(json.dumps(summary))
