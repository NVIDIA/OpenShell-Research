"""Data records carrying one request and its processing result."""

from __future__ import annotations

from privacy_guard.payloads.request import InterceptedRequest
from privacy_guard.payloads.result import ProcessingDecision, ProcessingResult

__all__ = ["InterceptedRequest", "ProcessingDecision", "ProcessingResult"]
