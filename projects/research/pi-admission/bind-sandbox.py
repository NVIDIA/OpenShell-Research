# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bind the service's demo credential to an operator-observed sandbox ID."""

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    sandbox = json.load(sys.stdin)
    identifier = sandbox["id"]
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("OpenShell did not return a sandbox ID")
    (args.state / "sandbox-id").write_text(identifier + "\n")
    print("Admission identity bound. Run ./demo.sh launch or ./demo.sh verify.")


if __name__ == "__main__":
    main()
