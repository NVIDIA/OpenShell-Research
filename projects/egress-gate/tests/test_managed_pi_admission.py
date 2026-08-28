# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path


def test_managed_pi_admission_maps_handles_to_exact_provider_context() -> None:
    project_dir = Path(__file__).parents[1]
    test_file = (
        project_dir / "examples/pi-attested-admission/managed-pi-admission.test.mjs"
    )

    subprocess.run(
        ["node", "--experimental-strip-types", "--test", str(test_file)],
        check=True,
    )
