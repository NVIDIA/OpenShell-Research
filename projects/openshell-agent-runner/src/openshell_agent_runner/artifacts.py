# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate downloaded artifacts without interpreting domain fields."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from openshell_agent_runner.config import OutputConfig
from openshell_agent_runner.document_review import DocumentReview
from openshell_agent_runner.errors import ArtifactError


def validate_artifact(
    downloaded: Path, output: OutputConfig, expected_model: str
) -> DocumentReview:
    try:
        size = downloaded.stat().st_size
    except OSError as error:
        raise ArtifactError(f"required artifact is missing: {downloaded}") from error
    if size > output.max_bytes:
        raise ArtifactError(
            f"output exceeds maximum size ({size} > {output.max_bytes} bytes)"
        )
    try:
        review = DocumentReview.model_validate_json(
            downloaded.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        raise ArtifactError(
            f"artifact failed DocumentReview validation: {error}"
        ) from error
    contract = output.contract
    diagnostics: list[str] = []
    if review.reviewer_id != contract.reviewer_id:
        diagnostics.append(
            f"reviewer_id must be {contract.reviewer_id!r}, got {review.reviewer_id!r}"
        )
    criteria = [score.criterion for score in review.criterion_scores]
    if criteria != contract.criteria:
        diagnostics.append(
            f"criterion order must be {contract.criteria!r}, got {criteria!r}"
        )
    if len(review.findings) > contract.max_findings:
        diagnostics.append(
            f"findings exceed maximum ({len(review.findings)} > "
            f"{contract.max_findings})"
        )
    if review.model_id != expected_model:
        diagnostics.append(
            f"model_id must be {expected_model!r}, got {review.model_id!r}"
        )
    if diagnostics:
        raise ArtifactError(
            "artifact failed DocumentReview contract: " + "; ".join(diagnostics)
        )
    return review


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
