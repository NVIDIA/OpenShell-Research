"""Show that managed Pi can deny or redact before recording a user prompt."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from egress_gate.admission import (
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

DENY_MARKER = "DENY_THIS"
REDACT_MARKER = "REDACT_THIS"
MIDDLEWARE_NAME = "pi-egress"


class PiExample:
    """Preserve the extension's admit, append, then send ordering."""

    def __init__(
        self,
        admission: HarnessAdmissionProcessor,
        egress: AttestedEgressProcessor,
    ) -> None:
        self.admission = admission
        self.egress = egress
        self.history: list[str] = []
        self.provider_prompts: list[str] = []

    def submit(self, prompt: str) -> AdmissionDecision:
        body = canonical_json_bytes(
            PiInputV1(schema_version="openshell.pi-input.v1", text=prompt)
        )
        admitted = self.admission.process(
            HarnessAdmissionRequest(
                request_body=body,
                provenance=PromptProvenance(
                    kind="rendered_prompt",
                    session_id="example-session",
                    submission_id=f"submission-{len(self.history) + 1}",
                ),
            ),
            _admission_context(),
            timeout=Timeout.from_seconds(1),
        )
        if admitted.decision is AdmissionDecision.DENY:
            return admitted.decision

        accepted_body = admitted.replacement_body or body
        accepted_prompt = PiInputV1.model_validate_json(accepted_body, strict=True).text
        self.history.append(accepted_prompt)

        request = _provider_request(accepted_prompt, admitted.receipt)
        result = self.egress.process(request, timeout=Timeout.from_seconds(1))
        if result.decision.value != "allow":
            raise RuntimeError(f"attested egress denied: {result.reason_code}")
        forwarded = apply_request_mutations(request, result.request_mutations)
        self.provider_prompts.append(
            json.loads(forwarded.body)["messages"][-1]["content"]
        )
        return admitted.decision


def main() -> None:
    admission, egress = _processors()
    example = PiExample(admission, egress)

    before_history = list(example.history)
    before_provider = list(example.provider_prompts)
    denied = example.submit(f"please {DENY_MARKER}")
    history_unchanged = example.history == before_history
    provider_unchanged = example.provider_prompts == before_provider

    redacted = example.submit(f"please {REDACT_MARKER}")
    print(
        json.dumps(
            {
                "deny": {
                    "decision": denied.value,
                    "history_unchanged": history_unchanged,
                    "provider_unchanged": provider_unchanged,
                },
                "redact": {
                    "decision": redacted.value,
                    "history": example.history,
                    "provider_prompts": example.provider_prompts,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


def _processors() -> tuple[HarnessAdmissionProcessor, AttestedEgressProcessor]:
    registry = create_builtin_registry()
    policy = yaml.safe_load(
        (Path(__file__).parent / "egress-gate-config.yaml").read_text()
    )
    processor = registry.prepare_processor(
        registry.validate_config(policy), timeout=Timeout.from_seconds(1)
    )
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


def _admission_context() -> HarnessAdmissionContext:
    return HarnessAdmissionContext(
        request_id="admission-request",
        sandbox_id="example-sandbox",
        middleware_name=MIDDLEWARE_NAME,
        harness="pi",
        harness_version="extension-v1",
        hook=AdmissionHook.RENDERED_PROMPT,
        schema_version="openshell.pi-input.v1",
        provider_target=_target(),
        provider_adapter_schema="openai.chat-completions.v1",
    )


def _provider_request(prompt: str, receipt: bytes | None) -> HttpRequest:
    body = json.dumps(
        {
            "model": "fixture-model",
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
            "store": False,
        },
        separators=(",", ":"),
    ).encode()
    headers = [HttpHeader(name="content-type", value="application/json")]
    if receipt is not None:
        headers.append(
            HttpHeader(
                name="x-openshell-middleware-egress-receipt",
                value=receipt.decode("ascii"),
            )
        )
    return HttpRequest(
        context=RequestContext(
            request_id="provider-request", sandbox_id="example-sandbox"
        ),
        target=_target(),
        headers=tuple(headers),
        body=body,
    )


if __name__ == "__main__":
    main()
