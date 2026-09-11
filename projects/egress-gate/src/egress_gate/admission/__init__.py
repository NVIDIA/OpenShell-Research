# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""First-class harness admission and attested-egress APIs."""

from egress_gate.admission.adapters import (
    AttestedEntries,
    ContextEntryV1,
    PiAssistantMessageV1,
    PiAssistantToolCallV1,
    PiMessageV1,
    PiProviderContextV1,
    PiTextContentV1,
    PiToolResultV1,
    ToolContextEntryV1,
    UserContextEntryV1,
    context_entries_subject,
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
    "HarnessAdmissionContext",
    "HarnessAdmissionProcessor",
    "HarnessAdmissionRequest",
    "HarnessAdmissionResult",
    "MAX_ADMISSION_BODY_BYTES",
    "PI_HARNESS_VERSION",
    "PiAssistantMessageV1",
    "PiAssistantToolCallV1",
    "PiMessageV1",
    "PiTextContentV1",
    "PiToolResultV1",
    "PiProviderContextV1",
    "RECEIPT_HEADER",
    "ReceiptAuthority",
    "ReceiptVerificationError",
    "ToolContextEntryV1",
    "UserContextEntryV1",
    "canonical_json_bytes",
    "context_entries_subject",
    "extract_provider_entries",
]
