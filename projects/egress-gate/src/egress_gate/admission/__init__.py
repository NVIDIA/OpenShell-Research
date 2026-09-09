# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""First-class harness admission and attested-egress APIs."""

from egress_gate.admission.adapters import (
    AttestedEntries,
    ContextEntryV1,
    HarnessAdapter,
    HarnessAdapterRegistry,
    PiAssistantMessageV1,
    PiAssistantMessageV1Adapter,
    PiAssistantToolCallV1,
    PiImageContentV1,
    PiMessageV1,
    PiMessageV1Adapter,
    PiProviderContextV1,
    PiProviderContextV1Adapter,
    PiTextContentV1,
    PiToolResultV1,
    PiToolResultV1Adapter,
    PreparedHarnessRequest,
    ToolContextEntryV1,
    UserContextEntryV1,
    context_entries_subject,
    create_pi_adapter_registry,
    extract_provider_entries,
)
from egress_gate.admission.canonical import canonical_json_bytes
from egress_gate.admission.models import (
    MAX_ADMISSION_BODY_BYTES,
    PI_HARNESS_VERSION,
    AdmissionDecision,
    AdmissionHook,
    AdmissionProvenance,
    HarnessAdmissionContext,
    HarnessAdmissionRequest,
    HarnessAdmissionResult,
)
from egress_gate.admission.processor import (
    RECEIPT_HEADER,
    AttestedEgressProcessor,
    HarnessAdmissionProcessor,
)
from egress_gate.admission.receipts import (
    AgentAttestationClaimsV2,
    ReceiptAuthority,
    ReceiptVerificationError,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionHook",
    "AdmissionProvenance",
    "AgentAttestationClaimsV2",
    "AttestedEntries",
    "AttestedEgressProcessor",
    "ContextEntryV1",
    "HarnessAdapter",
    "HarnessAdapterRegistry",
    "HarnessAdmissionContext",
    "HarnessAdmissionProcessor",
    "HarnessAdmissionRequest",
    "HarnessAdmissionResult",
    "MAX_ADMISSION_BODY_BYTES",
    "PI_HARNESS_VERSION",
    "PiAssistantMessageV1",
    "PiAssistantMessageV1Adapter",
    "PiAssistantToolCallV1",
    "PiMessageV1",
    "PiImageContentV1",
    "PiTextContentV1",
    "PiToolResultV1",
    "PiToolResultV1Adapter",
    "PiMessageV1Adapter",
    "PiProviderContextV1",
    "PiProviderContextV1Adapter",
    "PreparedHarnessRequest",
    "RECEIPT_HEADER",
    "ReceiptAuthority",
    "ReceiptVerificationError",
    "ToolContextEntryV1",
    "UserContextEntryV1",
    "canonical_json_bytes",
    "context_entries_subject",
    "extract_provider_entries",
    "create_pi_adapter_registry",
]
