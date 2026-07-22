"""The proto-free outcome of processing one intercepted request."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from privacy_guard.scanners import RequestBodyFinding
from privacy_guard.validation import StrictSensitiveModel


class ProcessingDecision(StrEnum):
    """Whether the supervisor should continue or stop the request."""

    ALLOW = "allow"
    DENY = "deny"


class ProcessingResult(StrictSensitiveModel):
    """A decision, safe findings, and bytes that replace the body when present."""

    decision: ProcessingDecision
    replacement_body: bytes | None = Field(default=None, repr=False)
    findings: tuple[RequestBodyFinding, ...] = ()
    reason_code: str | None = None
