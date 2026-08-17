# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""First-class harness admission and attested-egress APIs."""

from egress_gate.admission.adapters import (
    HarnessAdapter,
    HarnessAdapterRegistry,
    OpenAIChatCompletionsV1Adapter,
    PiInputV1,
    PiV1Adapter,
    PreparedHarnessRequest,
    ProviderAdapterRegistry,
    ProviderRequestAdapter,
    create_pi_adapter_registry,
    create_provider_adapter_registry,
)
from egress_gate.admission.canonical import (
    CanonicalFunctionCallV1,
    CanonicalGenerationV1,
    CanonicalMessageV1,
    CanonicalRole,
    CanonicalToolChoiceV1,
    CanonicalToolV1,
    ModelRequestV1,
    canonical_json_bytes,
)
from egress_gate.admission.models import (
    MAX_ADMISSION_BODY_BYTES,
    PI_HARNESS_VERSION,
    AdmissionDecision,
    AdmissionHook,
    HarnessAdmissionContext,
    HarnessAdmissionRequest,
    HarnessAdmissionResult,
    PromptProvenance,
)
from egress_gate.admission.processor import (
    RECEIPT_HEADER,
    AttestedEgressProcessor,
    HarnessAdmissionProcessor,
)
from egress_gate.admission.receipts import ReceiptAuthority, ReceiptClaimsV1

__all__ = [
    "AdmissionDecision",
    "AdmissionHook",
    "AttestedEgressProcessor",
    "CanonicalFunctionCallV1",
    "CanonicalGenerationV1",
    "CanonicalMessageV1",
    "CanonicalRole",
    "CanonicalToolChoiceV1",
    "CanonicalToolV1",
    "HarnessAdapter",
    "HarnessAdapterRegistry",
    "HarnessAdmissionContext",
    "HarnessAdmissionProcessor",
    "HarnessAdmissionRequest",
    "HarnessAdmissionResult",
    "MAX_ADMISSION_BODY_BYTES",
    "PromptProvenance",
    "PI_HARNESS_VERSION",
    "ModelRequestV1",
    "OpenAIChatCompletionsV1Adapter",
    "PiInputV1",
    "PiV1Adapter",
    "PreparedHarnessRequest",
    "ProviderAdapterRegistry",
    "ProviderRequestAdapter",
    "RECEIPT_HEADER",
    "ReceiptAuthority",
    "ReceiptClaimsV1",
    "canonical_json_bytes",
    "create_pi_adapter_registry",
    "create_provider_adapter_registry",
]
