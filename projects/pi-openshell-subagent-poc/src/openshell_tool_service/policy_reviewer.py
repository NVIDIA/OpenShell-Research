"""Fail-closed containment review using the standalone OpenShell prover."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_OUTPUT_BYTES = 1024 * 1024
PROVER_DOMAINS = {"filesystem", "network_l4", "network_rest", "process", "landlock"}


@dataclass(frozen=True)
class PolicyReviewRequest:
    parent_policy: str
    child_policy: str
    task: str


class PolicyReviewError(RuntimeError):
    """No containment proof was obtained; a child must not be launched."""

    def __init__(self, message: str, *, code: str = "policy-review-unavailable") -> None:
        super().__init__(message)
        self.code = code


class PolicyReviewer(Protocol):
    def review(self, request: PolicyReviewRequest) -> PolicyReviewResult: ...


class PolicyReviewResult(BaseModel):
    """Service-facing decision; a denial is a demonstrated permission increase."""

    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["allow", "deny"]
    reason: str = Field(min_length=1, max_length=2048)
    violations: list[Annotated[str, Field(max_length=MAX_OUTPUT_BYTES)]] = Field(max_length=20)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review reason must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        if self.decision == "allow" and self.violations:
            raise ValueError("allow decision must not contain violations")
        return self


class _Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    domains: list[str]

    @field_validator("domains")
    @classmethod
    def supported_domains(cls, value: list[str]) -> list[str]:
        if len(value) != len(PROVER_DOMAINS) or set(value) != PROVER_DOMAINS:
            raise ValueError("unexpected prover domains")
        return value


class _Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate: str
    boundary: str


class _Report(BaseModel):
    """Pin the reviewed CLI contract instead of accepting an arbitrary allow string."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Annotated[int, Field(ge=1, le=1)]
    prover_version: str = Field(min_length=1)
    check: Literal["boundary"]
    coverage: _Coverage
    result: Literal["within_boundary", "exceeds_boundary", "unsupported", "inconclusive"]
    exit_code: int
    inputs: _Inputs
    counterexample: dict[str, object] | None
    reason_code: str | None
    reason: str | None


ProverRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]


def _run_prover(command: Sequence[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=timeout_seconds,
        env={**os.environ, "NO_COLOR": "1"},
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in prover output")
        result[key] = value
    return result


class ProverPolicyReviewer:
    """Check the full policies without an LLM, normalization, or dropped fields."""

    def __init__(
        self,
        *,
        binary: str = "openshell-prover",
        timeout_seconds: int = 10,
        runner: ProverRunner = _run_prover,
    ) -> None:
        if not binary.strip() or timeout_seconds <= 0:
            raise ValueError("prover binary and a positive timeout are required")
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def review(self, request: PolicyReviewRequest) -> PolicyReviewResult:
        # A task cannot authorize a permission increase, so it is not passed to the CLI.
        # Each check gets private files; neither policy is normalized or projected.
        try:
            with tempfile.TemporaryDirectory(prefix="openshell-policy-review-") as directory:
                child_path = Path(directory) / "child.yaml"
                parent_path = Path(directory) / "parent.yaml"
                for path, policy in (
                    (child_path, request.child_policy),
                    (parent_path, request.parent_policy),
                ):
                    with path.open("x", encoding="utf-8") as stream:
                        path.chmod(0o600)
                        stream.write(policy)
                command = [
                    self.binary,
                    "check",
                    str(child_path),
                    "--boundary",
                    str(parent_path),
                    "--output",
                    "json",
                    "--timeout",
                    f"{self.timeout_seconds}s",
                ]
                # Allow the CLI to report a solver timeout, but also bound the process
                # independently so a hung prover cannot keep a worker occupied forever.
                completed = self.runner(command, self.timeout_seconds + 5)
                return self._decision(completed, child_path, parent_path)
        except subprocess.TimeoutExpired as error:
            raise PolicyReviewError("openshell-prover exceeded the host timeout") from error
        except (OSError, UnicodeError) as error:
            raise PolicyReviewError(f"could not run openshell-prover: {error}") from error

    def _decision(
        self, completed: subprocess.CompletedProcess[str], child_path: Path, parent_path: Path
    ) -> PolicyReviewResult:
        if any(
            len(output.encode("utf-8")) > MAX_OUTPUT_BYTES
            for output in (completed.stdout, completed.stderr)
        ):
            raise PolicyReviewError("openshell-prover output exceeded the size limit")
        if completed.returncode not in {0, 1, 3, 130}:
            diagnostic = completed.stderr.strip()[:2048]
            raise PolicyReviewError(
                f"openshell-prover exited {completed.returncode}; no proof was obtained"
                + (f": {diagnostic}" if diagnostic else "")
            )
        try:
            raw = json.loads(completed.stdout, object_pairs_hook=_unique_object)
            report = _Report.model_validate(raw)
        except (ValueError, ValidationError) as error:
            raise PolicyReviewError(
                "openshell-prover returned invalid or incompatible JSON"
            ) from error
        expected_codes = {
            "within_boundary": {0},
            "exceeds_boundary": {1},
            "unsupported": {3},
            "inconclusive": {3, 130},
        }
        if (
            report.exit_code != completed.returncode
            or completed.returncode not in expected_codes[report.result]
            or report.inputs.candidate != str(child_path)
            or report.inputs.boundary != str(parent_path)
        ):
            raise PolicyReviewError("openshell-prover returned an inconsistent result")
        if report.result == "within_boundary":
            if any(
                value is not None
                for value in (
                    report.counterexample,
                    report.reason_code,
                    report.reason,
                )
            ):
                raise PolicyReviewError("openshell-prover success contained contradictory evidence")
            return PolicyReviewResult(
                decision="allow",
                reason="openshell-prover verified within_boundary",
                violations=[],
            )
        if report.result == "exceeds_boundary":
            if not report.counterexample:
                raise PolicyReviewError(
                    "openshell-prover reported expansion without a counterexample"
                )
            return PolicyReviewResult(
                decision="deny",
                reason="openshell-prover found child authority outside the parent policy",
                violations=[json.dumps(
                    report.counterexample, sort_keys=True,
                    ensure_ascii=False, separators=(",", ":"),
                )],
            )
        diagnostic = (
            f"{report.reason_code or report.result}: {report.reason or 'no proof obtained'}"
        )
        raise PolicyReviewError(
            f"openshell-prover {report.result}: {diagnostic[:2048]}. "
            "No child was created. Do not remove policy fields to bypass this check.",
            code=f"policy-review-{report.result}",
        )
