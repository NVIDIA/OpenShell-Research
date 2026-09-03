#!/usr/bin/env python3
"""Benchmark the Tool Service scheduler without OpenShell or model calls."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from openshell_tool_service.app import create_app
from openshell_tool_service.collaboration import ChildCollaboration
from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import PolicyReviewRequest, PolicyReviewResult
from openshell_tool_service.runtime import ExecutionResult
from openshell_tool_service.store import Job

TOKEN = "benchmark-token"
TERMINAL_STATES = {"completed", "failed", "stopped", "blocked"}


class AllowingReviewer:
    """Remove external model latency while retaining the policy-review call path."""

    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(
            decision="allow",
            reason="Mock child policy is allowed.",
            violations=(),
            task_alignment="aligned",
            task_alignment_reason="Mock task and policy are aligned.",
            reviewer="benchmark",
        )


class ParentPolicySource:
    """Remove OpenShell CLI access while retaining parent-policy lookup."""

    def get(self, _sandbox_name: str) -> str:
        return "mock-parent-policy"


class SaturatingRuntime:
    """Stand in for one complete child execution and track active workers."""

    def __init__(self, target_concurrency: int, duration_seconds: float) -> None:
        self.target_concurrency = target_concurrency
        self.duration_seconds = duration_seconds
        self._lock = threading.Lock()
        self._saturated = threading.Event()
        self.active = 0
        self.max_active = 0

    def prepare(
        self,
        _job: Job,
        _collaboration: ChildCollaboration,
        on_ready=None,
    ) -> None:
        if on_ready:
            on_ready()

    def execute(self, _job: Job) -> ExecutionResult:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= self.target_concurrency:
                self._saturated.set()
        try:
            # Keep the first wave alive until the configured number of scheduler
            # slots has had a chance to start. This prevents very short fake jobs
            # from finishing before concurrent HTTP admission can saturate the pool.
            self._saturated.wait(timeout=5)
            time.sleep(self.duration_seconds)
            return ExecutionResult(output="benchmark-complete", stderr="", exit_code=0)
        finally:
            with self._lock:
                self.active -= 1

    def cleanup(self, _job: Job) -> str | None:
        return None


@dataclass(frozen=True)
class BenchmarkResult:
    concurrency: int
    jobs: int
    accepted: int
    peak: int
    elapsed_seconds: float
    throughput: float
    submit_p95_ms: float
    status_p95_ms: float
    errors: int
    passed: bool
    reason: str


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentage * len(ordered)) - 1)
    return ordered[index]


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def payload(index: int, concurrency: int) -> dict[str, Any]:
    workflow: dict[str, object] = {
        "id": f"benchmark-run-{concurrency}",
        "startMode": "immediate" if concurrency == 1 else "all-ready",
    }
    if concurrency > 1:
        workflow["expectedWorkers"] = concurrency
    return {
        "idempotencyKey": f"benchmark-{concurrency}-{index}",
        "caller": {"sandboxName": "benchmark-parent"},
        "workflow": workflow,
        "worker": {
            "stepIndex": index,
            "role": f"worker-{index + 1}",
            "prompt": "Complete the mock benchmark task.",
            "resources": {"childPolicy": "version: 1\nnetwork_policies: {}"},
        },
    }


async def run_level(
    concurrency: int,
    *,
    duration_seconds: float,
    timeout_seconds: float,
    api_p95_limit_ms: float,
    submit_concurrency: int,
) -> BenchmarkResult:
    job_count = concurrency
    runtime = SaturatingRuntime(concurrency, duration_seconds)
    submit_latencies: list[float] = []
    status_latencies: list[float] = []
    submission_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="openshell-concurrency-") as directory:
        settings = Settings(
            token=TOKEN,
            database_path=Path(directory) / "jobs.sqlite3",
            create_concurrency=min(8, concurrency),
            max_active_workers=concurrency,
        )
        app = create_app(settings, runtime, AllowingReviewer(), ParentPolicySource())
        headers = {"Authorization": f"Bearer {TOKEN}"}
        transport = httpx.ASGITransport(app=app)
        submit_slots = asyncio.Semaphore(submit_concurrency)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://benchmark",
            ) as client,
        ):
            started_at = time.perf_counter()

            async def submit(index: int) -> tuple[str | None, float, str | None]:
                async with submit_slots:
                    request_started = time.perf_counter()
                    try:
                        response = await client.post(
                            "/v1/jobs",
                            json=payload(index, concurrency),
                            headers=headers,
                        )
                        latency = (time.perf_counter() - request_started) * 1000
                        if response.status_code != 202:
                            return None, latency, f"HTTP {response.status_code}"
                        return str(response.json()["providerJobId"]), latency, None
                    except Exception as error:
                        latency = (time.perf_counter() - request_started) * 1000
                        return None, latency, type(error).__name__

            submissions = await asyncio.gather(*(submit(index) for index in range(job_count)))

            submit_latencies.extend(item[1] for item in submissions)
            submission_errors.extend(item[2] for item in submissions if item[2] is not None)
            job_ids = [item[0] for item in submissions if item[0] is not None]
            terminal: dict[str, str] = {}
            deadline = time.monotonic() + timeout_seconds
            while len(terminal) < len(job_ids) and time.monotonic() < deadline:
                pending = [job_id for job_id in job_ids if job_id not in terminal]
                for batch in chunks(pending, 100):
                    request_started = time.perf_counter()
                    response = await client.post(
                        "/v1/jobs/status",
                        json={"jobIds": batch},
                        headers=headers,
                    )
                    status_latencies.append((time.perf_counter() - request_started) * 1000)
                    if response.status_code != 200:
                        submission_errors.append(f"status HTTP {response.status_code}")
                        continue
                    for job in response.json()["jobs"]:
                        if job["state"] in TERMINAL_STATES:
                            terminal[str(job["providerJobId"])] = str(job["state"])
                if len(terminal) < len(job_ids):
                    await asyncio.sleep(0.01)

            elapsed_seconds = time.perf_counter() - started_at

    failed_jobs = sum(state != "completed" for state in terminal.values())
    timed_out = len(job_ids) - len(terminal)
    errors = len(submission_errors) + failed_jobs + timed_out
    submit_p95_ms = percentile(submit_latencies, 0.95)
    status_p95_ms = percentile(status_latencies, 0.95)
    reached_limit = runtime.max_active == concurrency
    responsive = max(submit_p95_ms, status_p95_ms) <= api_p95_limit_ms
    passed = errors == 0 and reached_limit and responsive

    reasons: list[str] = []
    if errors:
        reasons.append(f"{errors} errors")
    if not reached_limit:
        reasons.append(f"peak {runtime.max_active} did not reach {concurrency}")
    if not responsive:
        reasons.append(f"API p95 exceeded {api_p95_limit_ms:.0f} ms")

    return BenchmarkResult(
        concurrency=concurrency,
        jobs=job_count,
        accepted=len(job_ids),
        peak=runtime.max_active,
        elapsed_seconds=elapsed_seconds,
        throughput=(len(terminal) - failed_jobs) / elapsed_seconds,
        submit_p95_ms=submit_p95_ms,
        status_p95_ms=status_p95_ms,
        errors=errors,
        passed=passed,
        reason=", ".join(reasons) or "healthy",
    )


def parse_levels(raw: str) -> list[int]:
    try:
        levels = [int(value.strip()) for value in raw.split(",") if value.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("levels must be comma-separated integers") from error
    if not levels or any(level <= 0 for level in levels):
        raise argparse.ArgumentTypeError("levels must contain positive integers")
    return list(dict.fromkeys(levels))


def print_results(results: list[BenchmarkResult], api_p95_limit_ms: float) -> None:
    print()
    print("| Limit | Jobs | Accepted | Peak | Jobs/s | Submit p95 | Status p95 | Errors | Result |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |")
    for result in results:
        verdict = "PASS" if result.passed else "FAIL"
        print(
            f"| {result.concurrency} | {result.jobs} | {result.accepted} | "
            f"{result.peak} | {result.throughput:.1f} | {result.submit_p95_ms:.1f} ms | "
            f"{result.status_p95_ms:.1f} ms | {result.errors} | {verdict} |"
        )

    passing = [result for result in results if result.passed]
    print()
    if passing:
        recommended = passing[-1]
        print(f"Recommended mock concurrency ceiling: {recommended.concurrency}")
        if recommended is results[-1]:
            print("The highest tested level passed; test higher levels to locate the ceiling.")
    else:
        print("Recommended mock concurrency ceiling: none of the tested levels passed")
    print(
        "Pass criteria: zero errors, peak reaches the configured limit, "
        f"API p95 <= {api_p95_limit_ms:.0f} ms."
    )
    failures = [result for result in results if not result.passed]
    for result in failures:
        print(f"- Limit {result.concurrency}: {result.reason}")
    print(
        "This measures Tool Service scheduling only, not OpenShell, provider, "
        "or inference capacity."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sweep the Tool Service's mock concurrency limit and print a result table."
    )
    parser.add_argument(
        "--levels",
        type=parse_levels,
        default=parse_levels("1,2,4,8,16,32,64"),
        help="Comma-separated concurrency limits (default: 1,2,4,8,16,32,64).",
    )
    parser.add_argument(
        "--job-duration-ms",
        type=float,
        default=100,
        help="How long each fake child runs after pool saturation (default: 100 ms).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=60,
        help="Maximum time allowed for each level (default: 60 seconds).",
    )
    parser.add_argument(
        "--api-p95-limit-ms",
        type=float,
        default=500,
        help="Maximum healthy submit/status p95 latency (default: 500 ms).",
    )
    parser.add_argument(
        "--submit-concurrency",
        type=int,
        default=128,
        help="Maximum simultaneous HTTP submission requests (default: 128).",
    )
    arguments = parser.parse_args()
    if arguments.job_duration_ms <= 0:
        parser.error("--job-duration-ms must be greater than zero")
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    if arguments.api_p95_limit_ms <= 0:
        parser.error("--api-p95-limit-ms must be greater than zero")
    if arguments.submit_concurrency <= 0:
        parser.error("--submit-concurrency must be greater than zero")

    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def benchmark() -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        for level in arguments.levels:
            print(f"Running mock concurrency level {level}...", flush=True)
            results.append(
                await run_level(
                    level,
                    duration_seconds=arguments.job_duration_ms / 1000,
                    timeout_seconds=arguments.timeout_seconds,
                    api_p95_limit_ms=arguments.api_p95_limit_ms,
                    submit_concurrency=arguments.submit_concurrency,
                )
            )
        return results

    results = asyncio.run(benchmark())
    print_results(results, arguments.api_p95_limit_ms)


if __name__ == "__main__":
    main()
