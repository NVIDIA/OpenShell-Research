# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harness-admission orchestration and attested network egress."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from egress_gate.admission.adapters import (
    AdmissionMutationError,
    AdmissionShapeError,
    HarnessAdapterRegistry,
    ProviderAdapterRegistry,
    ProviderShapeError,
)
from egress_gate.admission.canonical import canonical_json_bytes
from egress_gate.admission.models import (
    MAX_ADMISSION_BODY_BYTES,
    AdmissionDecision,
    AdmissionHook,
    HarnessAdmissionContext,
    HarnessAdmissionRequest,
    HarnessAdmissionResult,
)
from egress_gate.admission.receipts import ReceiptAuthority, ReceiptVerificationError
from egress_gate.errors import EgressGateError, GateError, TimeoutExpiredError
from egress_gate.request import (
    EnforcementPoint,
    HarnessAdmissionMetadata,
    HttpRequest,
    RemoveHeaderMutation,
    RequestContext,
    RequestMutations,
)
from egress_gate.request_processor import RequestProcessor, apply_request_mutations
from egress_gate.result import (
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    GateDecisionSource,
)
from egress_gate.timeout import Timeout

RECEIPT_HEADER = "x-openshell-middleware-egress-receipt"


class HarnessAdmissionProcessor:
    """Apply the configured Gate pipeline through one registered harness adapter."""

    def __init__(
        self,
        request_processor: RequestProcessor,
        adapters: HarnessAdapterRegistry,
        receipt_authority: ReceiptAuthority,
    ) -> None:
        fingerprint = request_processor.policy_fingerprint
        if not fingerprint:
            raise ValueError("admission requires a policy fingerprint")
        self._request_processor = request_processor
        self._adapters = adapters
        self._receipt_authority = receipt_authority
        self._policy_fingerprint = fingerprint

    @property
    def readiness(self) -> dict[str, str]:
        """Return content-safe compatibility metadata for a managed launcher."""
        return {
            "admission_schema": "openshell.pi-input.v1",
            "canonicalization": "canonical-json.v1",
            "provider_adapter": "openai.chat-completions.v1",
            "receipt_version": "egress-receipt.v1",
            "key_id": self._receipt_authority.key_id,
            "policy_fingerprint": self._policy_fingerprint,
        }

    def process(
        self,
        request: HarnessAdmissionRequest,
        context: HarnessAdmissionContext,
        *,
        timeout: Timeout,
    ) -> HarnessAdmissionResult:
        """Return an explicit allow, replacement, or fail-closed denial."""
        try:
            adapter = self._adapters.resolve(context)
            prepared = adapter.prepare(request, context, timeout)
            projected = HttpRequest(
                context=RequestContext(
                    request_id=context.request_id,
                    sandbox_id=context.sandbox_id,
                    enforcement_point=EnforcementPoint.HARNESS_ADMISSION,
                    harness_admission=HarnessAdmissionMetadata(
                        harness=context.harness,
                        harness_version=context.harness_version,
                        hook=context.hook.value,
                        schema_version=context.schema_version,
                    ),
                ),
                target=context.provider_target,
                headers=(),
                body=prepared.projected_body,
            )
            gate_result = self._request_processor.process(projected, timeout=timeout)
            timeout.raise_if_expired()
            if gate_result.decision is EgressDecision.DENY:
                return HarnessAdmissionResult(
                    hook=context.hook,
                    decision=AdmissionDecision.DENY,
                    findings=gate_result.findings,
                    reason_code=gate_result.reason_code,
                    policy_fingerprint=self._policy_fingerprint,
                )
            if gate_result.request_mutations.header_mutations:
                raise AdmissionMutationError("admission cannot mutate HTTP headers")
            final_request = apply_request_mutations(
                projected, gate_result.request_mutations
            )
            replacement, rendered_prompt = adapter.validate_result(
                prepared, final_request.body, context, timeout
            )
            if replacement is not None and len(replacement) > MAX_ADMISSION_BODY_BYTES:
                raise AdmissionMutationError("admission replacement body is too large")
            timeout.raise_if_expired()
            receipt = self._receipt_authority.issue(
                rendered_prompt,
                context,
                request.provenance,
                policy_fingerprint=self._policy_fingerprint,
            )
            timeout.raise_if_expired()
            return HarnessAdmissionResult(
                hook=context.hook,
                decision=(
                    AdmissionDecision.REPLACE
                    if replacement is not None
                    else AdmissionDecision.ALLOW
                ),
                replacement_body=replacement,
                receipt=receipt,
                findings=gate_result.findings,
                policy_fingerprint=self._policy_fingerprint,
            )
        except (AdmissionShapeError, AdmissionMutationError, ValidationError):
            return self._deny("admission_contract_invalid", context.hook)
        except TimeoutExpiredError:
            return self._deny("admission_unavailable", context.hook)
        except (EgressGateError, GateError, ValueError):
            return self._deny("admission_unavailable", context.hook)
        except Exception:
            return self._deny("admission_unavailable", context.hook)

    def _deny(self, reason_code: str, hook: AdmissionHook) -> HarnessAdmissionResult:
        return HarnessAdmissionResult(
            hook=hook,
            decision=AdmissionDecision.DENY,
            reason_code=reason_code,
            policy_fingerprint=self._policy_fingerprint,
        )


class AttestedEgressProcessor:
    """Verify a receipt, run network Gates, and reject prompt divergence."""

    def __init__(
        self,
        request_processor: RequestProcessor,
        provider_adapters: ProviderAdapterRegistry,
        receipt_authority: ReceiptAuthority,
        *,
        middleware_name: str,
        harness_version: Literal["extension-v1"],
    ) -> None:
        fingerprint = request_processor.policy_fingerprint
        if not fingerprint:
            raise ValueError("attested egress requires a policy fingerprint")
        self._request_processor = request_processor
        self._provider_adapters = provider_adapters
        self._receipt_authority = receipt_authority
        self._middleware_name = middleware_name
        self._harness_version = harness_version
        self._provider_adapter_schema = "openai.chat-completions.v1"
        self._policy_fingerprint = fingerprint

    def process(self, request: HttpRequest, *, timeout: Timeout) -> EgressResult:
        """Deny any unattested or semantically changed provider request."""
        if request.context.enforcement_point is not EnforcementPoint.NETWORK_EGRESS:
            return self._deny("network_context_invalid")
        receipt_headers = tuple(
            header
            for header in request.headers
            if header.name.lower() == RECEIPT_HEADER
        )
        if len(receipt_headers) != 1:
            reason = "receipt_missing" if not receipt_headers else "receipt_duplicate"
            return self._deny(reason)
        stripped = request.model_copy(
            update={
                "headers": tuple(
                    header
                    for header in request.headers
                    if header.name.lower() != RECEIPT_HEADER
                )
            }
        )
        try:
            adapter = self._provider_adapters.resolve(self._provider_adapter_schema)
            rendered_prompt = adapter.rendered_prompt(stripped, timeout)
            timeout.raise_if_expired()
            context = HarnessAdmissionContext(
                request_id=request.context.request_id,
                sandbox_id=request.context.sandbox_id,
                middleware_name=self._middleware_name,
                harness="pi",
                harness_version=self._harness_version,
                hook=AdmissionHook.RENDERED_PROMPT,
                schema_version="openshell.pi-input.v1",
                provider_target=request.target,
                provider_adapter_schema="openai.chat-completions.v1",
            )
            self._receipt_authority.verify(
                receipt_headers[0].value.encode("ascii"),
                rendered_prompt,
                context,
                policy_fingerprint=self._policy_fingerprint,
            )
            timeout.raise_if_expired()
            gate_result = self._request_processor.process(stripped, timeout=timeout)
            timeout.raise_if_expired()
            if gate_result.decision is EgressDecision.DENY:
                return gate_result
            final_request = apply_request_mutations(
                stripped, gate_result.request_mutations
            )
            final_prompt = adapter.rendered_prompt(final_request, timeout)
            if canonical_json_bytes(final_prompt) != canonical_json_bytes(
                rendered_prompt
            ):
                return self._deny("semantic_mutation_denied")
            timeout.raise_if_expired()
            mutations = RequestMutations(
                replacement_body=gate_result.request_mutations.replacement_body,
                header_mutations=gate_result.request_mutations.header_mutations
                + (RemoveHeaderMutation(kind="remove", name=RECEIPT_HEADER),),
            )
            return gate_result.model_copy(update={"request_mutations": mutations})
        except UnicodeEncodeError:
            return self._deny("receipt_malformed")
        except ReceiptVerificationError as error:
            return self._deny(error.reason_code)
        except TimeoutExpiredError:
            return self._deny("egress_verification_failed")
        except (ProviderShapeError, ValidationError):
            return self._deny("provider_shape_unsupported")
        except (EgressGateError, GateError, ValueError):
            return self._deny("egress_verification_failed")
        except Exception:
            return self._deny("egress_verification_failed")

    def _deny(self, reason_code: str) -> EgressResult:
        return EgressResult(
            decision=EgressDecision.DENY,
            decision_source=GateDecisionSource(
                kind=DecisionSourceKind.GATE,
                gate_name="receipt-verifier",
                gate_type="receipt-verifier",
            ),
            reason_code=reason_code,
            policy_fingerprint=self._policy_fingerprint,
        )


__all__ = [
    "AttestedEgressProcessor",
    "HarnessAdmissionProcessor",
    "RECEIPT_HEADER",
]
