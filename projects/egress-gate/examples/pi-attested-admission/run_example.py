"""Hermetic rendered-prompt admission example for the Pi MVP."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import yaml

from egress_gate.admission import (
    RECEIPT_HEADER,
    AdmissionDecision,
    AdmissionHook,
    AttestedEgressProcessor,
    HarnessAdmissionContext,
    HarnessAdmissionProcessor,
    HarnessAdmissionRequest,
    PiInputV1,
    PromptProvenance,
    ReceiptAuthority,
    canonical_json_bytes,
    create_pi_adapter_registry,
    create_provider_adapter_registry,
)
from egress_gate.gates import create_builtin_registry
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.request_processor import apply_request_mutations
from egress_gate.timeout import Timeout

DENY_MARKER = "OPEN_SHELL_ADMISSION_DENY_TEST"
REPLACE_MARKER = "OPEN_SHELL_ADMISSION_REPLACE_TEST"
MIDDLEWARE_NAME = "pi-egress"


class ManagedPiSession:
    """Model the extension's admit, optionally replace, commit, and send order."""

    def __init__(
        self,
        session_file: Path,
        admission: HarnessAdmissionProcessor,
        egress: AttestedEgressProcessor,
    ) -> None:
        self._session_file = session_file
        self._admission = admission
        self._egress = egress
        self._messages: list[dict[str, str]] = []
        self.provider_requests: list[HttpRequest] = []
        self._sequence = 0
        self._write_session()

    def submit(self, rendered_prompt: str) -> dict[str, object]:
        before_messages = len(self._messages)
        before_requests = len(self.provider_requests)
        body = canonical_json_bytes(
            PiInputV1(schema_version="openshell.pi-input.v1", text=rendered_prompt)
        )
        admitted = self._admission.process(
            HarnessAdmissionRequest(
                request_body=body,
                provenance=PromptProvenance(
                    kind="rendered_prompt",
                    session_id="example-session",
                    submission_id=self._next_id("submission"),
                ),
            ),
            _admission_context(self._next_id("admission")),
            timeout=Timeout.from_seconds(1),
        )
        if admitted.decision is AdmissionDecision.DENY:
            return {
                "decision": "deny",
                "reason_code": admitted.reason_code,
                "session_unchanged": len(self._messages) == before_messages,
                "provider_calls": len(self.provider_requests) - before_requests,
            }

        accepted_body = admitted.replacement_body or body
        accepted_prompt = PiInputV1.model_validate_json(accepted_body, strict=True).text
        self._messages.append({"role": "user", "content": accepted_prompt})
        self._write_session()
        request = _provider_request(
            accepted_prompt, admitted.receipt, request_id=self._next_id("network")
        )
        egress = self._egress.process(request, timeout=Timeout.from_seconds(1))
        if egress.decision.value != "allow":
            raise RuntimeError(f"attested egress denied: {egress.reason_code}")
        forwarded = apply_request_mutations(request, egress.request_mutations)
        if any(header.name.lower() == RECEIPT_HEADER for header in forwarded.headers):
            raise RuntimeError("internal receipt reached provider fixture")
        self.provider_requests.append(forwarded)
        history = self._session_file.read_text(encoding="utf-8")
        return {
            "decision": admitted.decision.value,
            "provider_calls": len(self.provider_requests) - before_requests,
            "receipt_count": int(admitted.receipt is not None),
            "original_absent": rendered_prompt not in history,
            "replacement_present": accepted_prompt in history,
            "provider_original_absent": rendered_prompt.encode() not in forwarded.body,
            "provider_replacement_present": accepted_prompt.encode() in forwarded.body,
        }

    def continuation_without_receipt(self) -> str | None:
        result = self._egress.process(
            _provider_request(
                "continuation", None, request_id=self._next_id("continuation")
            ),
            timeout=Timeout.from_seconds(1),
        )
        return result.reason_code

    def _write_session(self) -> None:
        self._session_file.write_text(
            "".join(
                json.dumps(message, sort_keys=True) + "\n" for message in self._messages
            ),
            encoding="utf-8",
        )

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", type=Path)
    options = parser.parse_args()
    session_file = options.session_file or (
        Path(tempfile.mkdtemp(prefix="pi-egress-example-")) / "session.jsonl"
    )
    session_file.parent.mkdir(parents=True, exist_ok=True)
    admission, egress = _processors()
    session = ManagedPiSession(session_file, admission, egress)

    safe = session.submit("safe rendered prompt")
    before_denial = session_file.read_bytes()
    denied = session.submit(f"unsafe {DENY_MARKER}")
    denied["denied_content_absent"] = (
        DENY_MARKER.encode() not in session_file.read_bytes()
    )
    denied["session_unchanged"] = before_denial == session_file.read_bytes()
    replacement = session.submit(f"replace {REPLACE_MARKER}")
    evidence = {
        "versions": admission.readiness,
        "safe_direct": safe,
        "direct_denial": denied,
        "replacement_turn": replacement,
        "continuation": {"reason_code": session.continuation_without_receipt()},
        "provider": {
            "request_count": len(session.provider_requests),
            "receipt_headers_seen": sum(
                header.name.lower() == RECEIPT_HEADER
                for request in session.provider_requests
                for header in request.headers
            ),
        },
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))


def _processors() -> tuple[HarnessAdmissionProcessor, AttestedEgressProcessor]:
    example_dir = Path(__file__).resolve().parent
    registry = create_builtin_registry()
    config = registry.validate_config(
        yaml.safe_load(
            (example_dir / "egress-gate-config.yaml").read_text(encoding="utf-8")
        )
    )
    processor = registry.prepare_processor(config, timeout=Timeout.from_seconds(1))
    authority = ReceiptAuthority()
    return (
        HarnessAdmissionProcessor(processor, create_pi_adapter_registry(), authority),
        AttestedEgressProcessor(
            processor,
            create_provider_adapter_registry(),
            authority,
            middleware_name=MIDDLEWARE_NAME,
            harness_version="extension-v1",
        ),
    )


def _target() -> HttpTarget:
    return HttpTarget(
        scheme="https",
        host="provider.fixture",
        port=443,
        method="POST",
        path="/v1/chat/completions",
        query="",
    )


def _admission_context(request_id: str) -> HarnessAdmissionContext:
    return HarnessAdmissionContext(
        request_id=request_id,
        sandbox_id="example-sandbox",
        middleware_name=MIDDLEWARE_NAME,
        harness="pi",
        harness_version="extension-v1",
        hook=AdmissionHook.RENDERED_PROMPT,
        schema_version="openshell.pi-input.v1",
        provider_target=_target(),
        provider_adapter_schema="openai.chat-completions.v1",
    )


def _provider_request(
    prompt: str, receipt: bytes | None, *, request_id: str
) -> HttpRequest:
    body = json.dumps(
        {
            "model": "fixture-model",
            "messages": [{"role": "user", "content": prompt}],
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0,
            "max_completion_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
            "store": False,
            "prompt_cache_key": "example-session",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    headers = [HttpHeader(name="content-type", value="application/json")]
    if receipt is not None:
        headers.append(HttpHeader(name=RECEIPT_HEADER, value=receipt.decode("ascii")))
    return HttpRequest(
        context=RequestContext(request_id=request_id, sandbox_id="example-sandbox"),
        target=_target(),
        headers=tuple(headers),
        body=body,
    )


if __name__ == "__main__":
    main()
