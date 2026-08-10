from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from slop_cop.benchmarks import (
    BenchmarkManifest,
    BenchmarkReference,
    evaluate_benchmarks,
    load_benchmark_manifest,
)
from slop_cop.config import load_config
from slop_cop.rules.registry import build_registry

PROJECT = Path(__file__).parents[1]
CONFIG = PROJECT / "slop-cop.toml"
REVISION = "1" * 40
PATH = "docs/dev-notes/posts/example.md"


def _reference(**updates: object) -> BenchmarkReference:
    values: dict[str, object] = {
        "name": "clean reference",
        "revision": REVISION,
        "path": PATH,
        "source_url": f"https://github.com/NVIDIA/OpenShell-Research/blob/{REVISION}/{PATH}",
        "expected_decision": "pass",
        "min_score": 95,
        "max_score": 100,
    }
    values.update(updates)
    return BenchmarkReference.model_validate(values)


async def test_evaluate_benchmarks_scores_supplied_sources() -> None:
    config = load_config(CONFIG)
    reference = _reference()
    results = await evaluate_benchmarks(
        BenchmarkManifest(benchmark=(reference,)),
        config=config,
        registry=build_registry(config),
        source_loader=lambda _: b"# Direct title\n\nThe controller rejects denied requests.\n",
    )

    assert len(results) == 1
    assert results[0].score == 100
    assert results[0].within_range


def test_reference_rejects_mismatched_source_url() -> None:
    with pytest.raises(ValidationError, match="must identify its revision and path"):
        _reference(source_url="https://github.com/NVIDIA/OpenShell-Research/blob/main/README.md")


def test_default_manifest_has_unique_references() -> None:
    manifest = load_benchmark_manifest(PROJECT / "benchmarks/dev-note-history.toml")
    keys = {(reference.revision, reference.path) for reference in manifest.benchmark}

    assert len(keys) == len(manifest.benchmark)
    assert {reference.expected_decision for reference in manifest.benchmark} == {"pass", "fail"}
