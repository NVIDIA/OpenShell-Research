"""Score calibration references."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from slop_cop.config import SlopCopConfig, StrictModel
from slop_cop.document import build_document
from slop_cop.engine import analyze_document
from slop_cop.findings import Decision
from slop_cop.rules.registry import RuleRegistry

_REVISION = re.compile(r"^[0-9a-f]{40}$")


class BenchmarkReference(StrictModel):
    """One immutable document revision and its acceptable result."""

    name: str = Field(min_length=1, max_length=100)
    revision: str | None = None
    path: str = Field(min_length=1, max_length=4_096)
    source_url: str | None = Field(default=None, min_length=1, max_length=4_096)
    fixture_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    expected_decision: Literal["pass", "fail"]
    min_score: int = Field(ge=0, le=100)
    max_score: int = Field(ge=0, le=100)

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _REVISION.fullmatch(value):
            raise ValueError("benchmark revision must be a 40-character lowercase commit SHA")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".md":
            raise ValueError("benchmark path must be a repository-relative Markdown path")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname != "github.com":
            raise ValueError("benchmark source_url must be an HTTPS GitHub URL")
        return value

    @field_validator("fixture_path")
    @classmethod
    def validate_fixture_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".md":
            raise ValueError("fixture_path must be a repository-relative Markdown path")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> BenchmarkReference:
        if self.min_score > self.max_score:
            raise ValueError("benchmark min_score cannot exceed max_score")
        historical = self.revision is not None or self.source_url is not None
        fixture = self.fixture_path is not None
        if historical == fixture:
            raise ValueError("benchmark must select one historical revision or fixture_path")
        if historical and (
            self.revision is None
            or self.source_url is None
            or f"/blob/{self.revision}/{self.path}" not in self.source_url
        ):
            raise ValueError("benchmark source_url must identify its revision and path")
        return self


class BenchmarkManifest(StrictModel):
    """Strict calibration manifest."""

    benchmark: tuple[BenchmarkReference, ...] = Field(min_length=1)

    @field_validator("benchmark", mode="before")
    @classmethod
    def convert_toml_array(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class BenchmarkResult(StrictModel):
    """Observed result for one calibration reference."""

    reference: BenchmarkReference
    score: int
    decision: Decision
    within_range: bool


SourceLoader = Callable[[BenchmarkReference], bytes]


def load_benchmark_manifest(path: Path) -> BenchmarkManifest:
    """Load and validate a benchmark manifest."""

    return BenchmarkManifest.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def git_source_loader(repository_root: Path) -> SourceLoader:
    """Return a loader for committed history and repository fixtures."""

    def load(reference: BenchmarkReference) -> bytes:
        if reference.fixture_path is not None:
            root = repository_root.resolve(strict=True)
            fixture = (root / reference.fixture_path).resolve(strict=True)
            try:
                fixture.relative_to(root)
            except ValueError as error:
                raise ValueError(
                    f"benchmark fixture is outside the repository: {reference.fixture_path}"
                ) from error
            if fixture.is_symlink() or not fixture.is_file():
                raise ValueError(f"benchmark fixture is not a regular file: {fixture}")
            return fixture.read_bytes()
        assert reference.revision is not None
        completed = subprocess.run(
            ["git", "show", f"{reference.revision}:{reference.path}"],
            cwd=repository_root,
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"cannot load benchmark {reference.name!r}: {detail}")
        return completed.stdout

    return load


async def evaluate_benchmarks(
    manifest: BenchmarkManifest,
    *,
    config: SlopCopConfig,
    registry: RuleRegistry,
    source_loader: SourceLoader,
) -> tuple[BenchmarkResult, ...]:
    """Score every reference and compare it with the declared calibration range."""

    results: list[BenchmarkResult] = []
    for reference in manifest.benchmark:
        document = build_document(
            reference.path,
            source_loader(reference),
            contexts=config.contexts,
            max_source_bytes=config.source_max_bytes,
        )
        observed = (await analyze_document(document, registry, config)).file_result
        if observed.score is None:
            raise ValueError(f"benchmark {reference.name!r} produced no score")
        within_range = (
            reference.min_score <= observed.score <= reference.max_score
            and observed.decision.value == reference.expected_decision
        )
        results.append(
            BenchmarkResult(
                reference=reference,
                score=observed.score,
                decision=observed.decision,
                within_range=within_range,
            )
        )
    return tuple(results)
