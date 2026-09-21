"""One-shot adapter for the external ``openshell-prover`` executable."""

import hashlib
import json
import os
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProverConfig:
    executable: str
    boundary: Path
    timeout_seconds: float = 10.0
    max_policy_bytes: int = 1024 * 1024

    @classmethod
    def load(cls, path: Path) -> "ProverConfig":
        with path.open("rb") as stream:
            values = tomllib.load(stream)
        boundary = Path(values["boundary"])
        if not boundary.is_absolute():
            boundary = path.parent / boundary
        return cls(
            executable=str(values.get("executable", "openshell-prover")),
            boundary=boundary.resolve(),
            timeout_seconds=float(values.get("timeout_seconds", 10.0)),
            max_policy_bytes=int(values.get("max_policy_bytes", 1024 * 1024)),
        )


def check_policy_boundary(candidate_policy: str, config: ProverConfig) -> dict[str, Any]:
    """Snapshot both policies, invoke the prover once, and validate its v1 JSON."""

    started = time.perf_counter()
    candidate_bytes = candidate_policy.encode("utf-8")
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    if len(candidate_bytes) > config.max_policy_bytes:
        return _adapter_error(
            candidate_sha256,
            "candidate_too_large",
            "candidate exceeds configured byte limit",
            started,
        )
    try:
        boundary_bytes = config.boundary.read_bytes()
    except OSError as error:
        return _adapter_error(candidate_sha256, "boundary_unavailable", str(error), started)
    if len(boundary_bytes) > config.max_policy_bytes:
        return _adapter_error(
            candidate_sha256,
            "boundary_too_large",
            "boundary exceeds configured byte limit",
            started,
        )
    boundary_sha256 = hashlib.sha256(boundary_bytes).hexdigest()

    try:
        with tempfile.TemporaryDirectory(prefix="policy-review-prover-") as directory:
            candidate_path = Path(directory) / "candidate.yaml"
            boundary_path = Path(directory) / "boundary.yaml"
            candidate_path.write_bytes(candidate_bytes)
            boundary_path.write_bytes(boundary_bytes)
            command = [
                config.executable,
                "check",
                os.fspath(candidate_path),
                "--boundary",
                os.fspath(boundary_path),
                "--output",
                "json",
                "--timeout",
                f"{max(1, int(config.timeout_seconds * 1000))}ms",
            ]
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=config.timeout_seconds + 1.0,
                shell=False,
            )
    except subprocess.TimeoutExpired:
        return _adapter_error(candidate_sha256, "adapter_timeout", "prover timed out", started)
    except OSError as error:
        return _adapter_error(candidate_sha256, "prover_unavailable", str(error), started)

    try:
        raw = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return _adapter_error(
            candidate_sha256,
            "malformed_output",
            f"prover did not return one JSON object: {error}",
            started,
            boundary_sha256,
        )
    validation_error = _validate_prover_output(raw, completed.returncode)
    if validation_error:
        return _adapter_error(
            candidate_sha256,
            "invalid_output_contract",
            validation_error,
            started,
            boundary_sha256,
            raw,
        )
    return {
        "schema_version": 1,
        "status": "complete"
        if raw["result"] in {"within_boundary", "exceeds_boundary"}
        else "unresolved",
        "within_boundary": raw["result"] == "within_boundary",
        "candidate_sha256": candidate_sha256,
        "boundary_sha256": boundary_sha256,
        "prover_version": raw["prover_version"],
        "coverage": raw.get("coverage"),
        "result": raw["result"],
        "counterexample": raw.get("counterexample"),
        "reason_code": raw.get("reason_code"),
        "reason": raw.get("reason"),
        "prover_report": raw,
        "timings_ms": {"prover": round((time.perf_counter() - started) * 1000, 3)},
        "summary": _summary(raw),
    }


def _validate_prover_output(value: Any, returncode: int) -> str | None:
    if not isinstance(value, dict):
        return "root must be an object"
    if value.get("schema_version") != 1:
        return "unsupported prover schema_version"
    if value.get("check") != "boundary":
        return "unexpected check kind"
    result = value.get("result")
    expected_codes = {
        "within_boundary": {0},
        "exceeds_boundary": {1},
        "unsupported": {3},
        "inconclusive": {3, 130},
        "error": {2},
    }
    if result not in expected_codes:
        return "unknown result"
    if returncode not in expected_codes[result] or value.get("exit_code") != returncode:
        return "result and exit code are inconsistent"
    if not isinstance(value.get("prover_version"), str):
        return "missing prover_version"
    if result == "exceeds_boundary" and not isinstance(value.get("counterexample"), dict):
        return "exceeds result requires counterexample"
    return None


def _adapter_error(
    candidate_sha256: str,
    reason_code: str,
    reason: str,
    started: float,
    boundary_sha256: str | None = None,
    raw: Any = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "adapter_error",
        "within_boundary": False,
        "candidate_sha256": candidate_sha256,
        "boundary_sha256": boundary_sha256,
        "result": "adapter_error",
        "reason_code": reason_code,
        "reason": reason,
        "prover_report": raw,
        "timings_ms": {"prover": round((time.perf_counter() - started) * 1000, 3)},
        "summary": f"Boundary check adapter error: {reason_code}.",
    }


def _summary(report: dict[str, Any]) -> str:
    result = report["result"]
    if result == "within_boundary":
        return "Candidate is within the configured boundary."
    if result == "exceeds_boundary":
        return "Candidate exceeds the configured boundary."
    return f"Boundary check unresolved: {result}."
