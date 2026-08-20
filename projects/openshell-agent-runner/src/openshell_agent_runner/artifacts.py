# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate and publish agent results without interpreting domain fields."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from openshell_agent_runner.errors import ArtifactError

ARTIFACT_PATH = "/sandbox/artifacts/result"
MAX_ARTIFACT_BYTES = 1024 * 1024


def validate_artifact(downloaded: Path, schema_path: Path | None = None) -> None:
    try:
        size = downloaded.stat().st_size
    except OSError as error:
        raise ArtifactError(f"required artifact is missing: {downloaded}") from error
    if size == 0:
        raise ArtifactError("agent result is empty")
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactError(
            f"output exceeds maximum size ({size} > {MAX_ARTIFACT_BYTES} bytes)"
        )
    if schema_path is None:
        return
    try:
        result = json.loads(downloaded.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"result is not valid JSON: {error}") from error
    errors = sorted(
        Draft202012Validator(schema).iter_errors(result),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        diagnostics = "; ".join(error.message for error in errors[:12])
        raise ArtifactError(f"result failed output schema validation: {diagnostics}")


def atomic_publish(source: Path, destination: Path) -> None:
    temporary: Path | None = None
    try:
        if destination.is_symlink():
            raise ArtifactError(
                f"artifact destination must not be a symlink: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as incoming:
            while block := incoming.read(64 * 1024):
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(destination)
    except ArtifactError:
        raise
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ArtifactError(
            f"cannot publish artifact to {destination}: {error}"
        ) from error
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
