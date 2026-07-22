#!/usr/bin/env python3
"""Benchmark complete Privacy Guard normalize/scan/validate/reconstruct paths."""

from __future__ import annotations

import argparse
import cProfile
import gc
import platform
import statistics
import time
import tracemalloc
from dataclasses import dataclass

from pydantic import Field
from typing_extensions import override

from privacy_guard.config import (
    ActionConfig,
    ObserveActionConfig,
    PolicyConfig,
    RedactActionConfig,
)
from privacy_guard.constants import (
    MAX_BODY_BYTES,
    MAX_FINDINGS_PER_BLOCK,
    MAX_FINDINGS_PER_REQUEST,
)
from privacy_guard.payloads import InterceptedRequest, ProcessingDecision
from privacy_guard.processor import RequestProcessor
from privacy_guard.scanners import Finding, ScanBudget, Scanner, ScannerConfig

_KIB = 1024
_MIB = 1024 * 1024
_MARKER_CHARACTER = "x"
_SCANNER_COUNT_MULTIPLE = 4
_MAXIMUM_WIDTH_NUMERIC_ELEMENT_COUNT = 2_097_151
_MAXIMUM_WIDTH_NUMERIC_BYTES = 4_194_303


class _BenchmarkScannerConfig(ScannerConfig):
    findings_per_block: int = Field(ge=0, le=MAX_FINDINGS_PER_BLOCK)


class _BenchmarkScanner(Scanner[_BenchmarkScannerConfig]):
    @override
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        if not text_block.startswith(
            _MARKER_CHARACTER * self.config.findings_per_block
        ):
            return ()
        return tuple(
            Finding(
                entity="benchmark",
                scanner_name=self.scanner_name,
                start_offset=offset,
                end_offset=offset + 1,
            )
            for offset in range(self.config.findings_per_block)
        )


@dataclass(frozen=True)
class _Scenario:
    name: str
    target_bytes: int
    finding_load: str
    scanner_count: int
    reconstruction: str
    body_shape: str = "text-blocks"


@dataclass(frozen=True)
class _PreparedScenario:
    definition: _Scenario
    processor: RequestProcessor
    request: InterceptedRequest
    expected_findings: int


@dataclass(frozen=True)
class _Measurement:
    definition: _Scenario
    actual_bytes: int
    finding_count: int
    median_wall_ms: float
    median_peak_bytes: int


_QUICK_SCENARIOS = (
    _Scenario("1KiB-zero-one-noop", _KIB, "zero", 1, "noop"),
    _Scenario(
        "1KiB-typical-multiple-replacement",
        _KIB,
        "typical",
        _SCANNER_COUNT_MULTIPLE,
        "replacement",
    ),
    _Scenario("1MiB-typical-one-noop", _MIB, "typical", 1, "noop"),
    _Scenario("1MiB-max-one-replacement", _MIB, "max", 1, "replacement"),
    _Scenario(
        "4MiB-zero-multiple-noop",
        MAX_BODY_BYTES,
        "zero",
        _SCANNER_COUNT_MULTIPLE,
        "noop",
    ),
    _Scenario(
        "4MiB-max-multiple-replacement",
        MAX_BODY_BYTES,
        "max",
        _SCANNER_COUNT_MULTIPLE,
        "replacement",
    ),
)

_FULL_SCENARIOS = _QUICK_SCENARIOS + (
    _Scenario("1KiB-typical-one-noop", _KIB, "typical", 1, "noop"),
    _Scenario("1MiB-zero-one-noop", _MIB, "zero", 1, "noop"),
    _Scenario(
        "1MiB-typical-multiple-replacement",
        _MIB,
        "typical",
        _SCANNER_COUNT_MULTIPLE,
        "replacement",
    ),
    _Scenario(
        "1MiB-max-multiple-noop",
        _MIB,
        "max",
        _SCANNER_COUNT_MULTIPLE,
        "noop",
    ),
    _Scenario(
        "4MiB-typical-one-replacement", MAX_BODY_BYTES, "typical", 1, "replacement"
    ),
    _Scenario(
        "4MiB-typical-multiple-noop",
        MAX_BODY_BYTES,
        "typical",
        _SCANNER_COUNT_MULTIPLE,
        "noop",
    ),
    _Scenario("4MiB-max-one-noop", MAX_BODY_BYTES, "max", 1, "noop"),
    _Scenario(
        "4194303B-2097151-elements-wide-numeric-zero-one-noop",
        _MAXIMUM_WIDTH_NUMERIC_BYTES,
        "zero",
        1,
        "noop",
        "wide-numeric",
    ),
)


def _build_json_body(target_bytes: int, block_count: int) -> bytes:
    minimum_text_length = MAX_FINDINGS_PER_BLOCK
    values = [_MARKER_CHARACTER * minimum_text_length for _ in range(block_count)]
    encoded = ('{"blocks":["' + '","'.join(values) + '"]}').encode()
    if len(encoded) > target_bytes:
        raise ValueError("target body is too small for the requested finding load")
    values[0] += _MARKER_CHARACTER * (target_bytes - len(encoded))
    body = ('{"blocks":["' + '","'.join(values) + '"]}').encode()
    if len(body) != target_bytes:
        raise AssertionError("benchmark body construction is not exact")
    return body


def _build_maximum_width_numeric_body() -> bytes:
    body = b"[" + (b"0," * (_MAXIMUM_WIDTH_NUMERIC_ELEMENT_COUNT - 1)) + b"0]"
    if len(body) != _MAXIMUM_WIDTH_NUMERIC_BYTES:
        raise AssertionError("maximum-width body construction is not exact")
    return body


def _prepare_scenario(definition: _Scenario) -> _PreparedScenario:
    if definition.finding_load == "zero":
        total_findings_per_block = 0
        block_count = 1
    elif definition.finding_load == "typical":
        total_findings_per_block = 8
        block_count = 1
    elif definition.finding_load == "max":
        total_findings_per_block = MAX_FINDINGS_PER_BLOCK
        block_count = MAX_FINDINGS_PER_REQUEST // MAX_FINDINGS_PER_BLOCK
    else:
        raise ValueError("unknown finding load")

    findings_per_scanner = total_findings_per_block // definition.scanner_count
    if findings_per_scanner * definition.scanner_count != total_findings_per_block:
        raise AssertionError("finding load must divide evenly across scanners")
    scanners = [
        _BenchmarkScanner(
            _BenchmarkScannerConfig(
                name=f"benchmark-{index}",
                entity_types=frozenset({"benchmark"}),
                findings_per_block=findings_per_scanner,
            )
        )
        for index in range(definition.scanner_count)
    ]
    action: ActionConfig = (
        RedactActionConfig(template="")
        if definition.reconstruction == "replacement"
        else ObserveActionConfig()
    )
    policy = PolicyConfig(on_finding=action)
    expected_findings = block_count * total_findings_per_block
    if definition.body_shape == "text-blocks":
        body = _build_json_body(definition.target_bytes, block_count)
    elif definition.body_shape == "wide-numeric":
        if block_count != 1 or expected_findings != 0:
            raise AssertionError("wide numeric scenario must have zero findings")
        body = _build_maximum_width_numeric_body()
    else:
        raise ValueError("unknown body shape")
    request = InterceptedRequest(raw_body=body, policy_config=policy)
    return _PreparedScenario(
        definition=definition,
        processor=RequestProcessor(scanners),
        request=request,
        expected_findings=expected_findings,
    )


def _run_and_verify(prepared: _PreparedScenario) -> None:
    result = prepared.processor.process(prepared.request)
    if result.decision is not ProcessingDecision.ALLOW:
        raise AssertionError(f"{prepared.definition.name} unexpectedly denied")
    if len(result.findings) != prepared.expected_findings:
        raise AssertionError(f"{prepared.definition.name} finding count changed")
    has_replacement = result.replacement_body is not None
    if has_replacement != (prepared.definition.reconstruction == "replacement"):
        raise AssertionError(f"{prepared.definition.name} reconstruction changed")


def _measure(prepared: _PreparedScenario, samples: int) -> _Measurement:
    _run_and_verify(prepared)
    wall_samples: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        _run_and_verify(prepared)
        wall_samples.append((time.perf_counter_ns() - started) / 1_000_000)

    peak_samples: list[int] = []
    for _ in range(samples):
        gc.collect()
        tracemalloc.start()
        try:
            _run_and_verify(prepared)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        peak_samples.append(peak)

    return _Measurement(
        definition=prepared.definition,
        actual_bytes=len(prepared.request.raw_body),
        finding_count=prepared.expected_findings,
        median_wall_ms=statistics.median(wall_samples),
        median_peak_bytes=int(statistics.median(peak_samples)),
    )


def _print_results(measurements: list[_Measurement], samples: int) -> None:
    print(f"Python: {platform.python_version()} ({platform.platform()})")
    print(f"Samples per median: {samples}")
    print(
        "| Scenario | Input bytes | Findings | Scanners | Reconstruction | "
        "Median wall (ms) | Median peak traced allocation (bytes) |"
    )
    print("| --- | ---: | ---: | ---: | --- | ---: | ---: |")
    for measurement in measurements:
        definition = measurement.definition
        print(
            f"| {definition.name} | {measurement.actual_bytes} | "
            f"{measurement.finding_count} | {definition.scanner_count} | "
            f"{definition.reconstruction} | {measurement.median_wall_ms:.3f} | "
            f"{measurement.median_peak_bytes} |"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("quick", "full"), default="quick")
    parser.add_argument("--samples", type=int)
    parser.add_argument(
        "--profile",
        metavar="PATH",
        help="write cProfile data for one verified pass through every scenario",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    scenarios = _QUICK_SCENARIOS if arguments.suite == "quick" else _FULL_SCENARIOS
    samples = (
        arguments.samples
        if arguments.samples is not None
        else (3 if arguments.suite == "quick" else 7)
    )
    if samples < 3 or samples % 2 == 0:
        raise SystemExit("--samples must be an odd integer of at least 3")
    prepared = [_prepare_scenario(scenario) for scenario in scenarios]
    if arguments.profile is not None:
        profiler = cProfile.Profile()
        profiler.enable()
        for item in prepared:
            _run_and_verify(item)
        profiler.disable()
        profiler.dump_stats(arguments.profile)
    measurements = [_measure(item, samples) for item in prepared]
    _print_results(measurements, samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
