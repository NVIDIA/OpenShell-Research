# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Send one small JEV API request without MCP servers or the OpenShell prover."""

import json
import os

from typesafe_sdk import Choice, TypeSafeClient


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("Export TYPESAFE_API_KEY before running this script.")

    model = "jev-1.13.0"
    with TypeSafeClient(model=model, timeout=30) as client:
        response = client.system_one(
            state={"task": "Read a file and return a summary. Do not modify it."},
            questions={
                "write_needed": Choice(
                    instructions="Does this task require modifying the file?",
                    criteria={
                        "yes": "The task requires modifying the file.",
                        "no": "The task only requires reading the file.",
                    },
                )
            },
        )

    answer = response.answers["write_needed"]
    print(
        json.dumps(
            {
                "model": model,
                "choice": answer.choice,
                "confidence": answer.confidence,
                "probabilities": dict(answer.probabilities),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
