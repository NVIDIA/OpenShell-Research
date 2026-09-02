# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Short-lived Ed25519 agent attestations."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from pydantic import Field, ValidationError

from egress_gate.admission.adapters import AttestedCandidate
from egress_gate.admission.canonical import canonical_json_bytes
from egress_gate.admission.models import (
    AdmissionProvenance,
    HarnessAdmissionContext,
)
from egress_gate.base import StrictDomainModel
from egress_gate.string_validators import BoundedMetadataString, ScalarString


class AgentAttestationClaimsV1(StrictDomainModel):
    """Supervisor-only proof that the latest context addition was admitted."""

    attestation_version: Literal["agent-attestation.v1"] = "agent-attestation.v1"
    canonicalization_version: Literal["canonical-json.v1"] = "canonical-json.v1"
    harness: ScalarString
    harness_version: Literal["sdk-v1"]
    harness_schema: ScalarString
    hook: Literal["user_message", "tool_result"]
    middleware_binding: BoundedMetadataString
    policy_fingerprint: ScalarString
    sandbox_id: BoundedMetadataString
    session_id: BoundedMetadataString
    submission_id: BoundedMetadataString
    attestation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    provider_adapter_schema: Literal["openai.request.v1"]
    host: ScalarString
    port: int = Field(ge=0, le=2**32 - 1)
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")


class ReceiptVerificationError(ValueError):
    """A bounded receipt verification failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ReceiptAuthority:
    """Single-instance Ed25519 issuer and verifier with an ephemeral default key."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey | None = None,
        *,
        allowed_clock_skew_seconds: int = 5,
    ) -> None:
        if not 0 <= allowed_clock_skew_seconds <= 30:
            raise ValueError("attestation clock skew must be between 0 and 30 seconds")
        self._private_key = private_key or Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        public_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._key_id = hashlib.sha256(public_bytes).hexdigest()[:16]
        self._allowed_clock_skew_seconds = allowed_clock_skew_seconds
        self._attestation_lifetime_seconds = 300

    @property
    def key_id(self) -> str:
        """Return the non-secret identifier of the active ephemeral key."""
        return self._key_id

    def issue_attestation(
        self,
        candidate: AttestedCandidate,
        context: HarnessAdmissionContext,
        provenance: AdmissionProvenance,
        *,
        policy_fingerprint: str,
        now: int | None = None,
    ) -> bytes:
        """Issue a retry-safe proof retained by the OpenShell supervisor."""
        if context.harness_version != "sdk-v1":
            raise ValueError("agent attestation context is unsupported")
        issued_at = _now_seconds() if now is None else now
        target = context.provider_target
        claims = AgentAttestationClaimsV1(
            harness=context.harness,
            harness_version=context.harness_version,
            harness_schema=context.schema_version,
            hook=context.hook.value,
            middleware_binding=context.middleware_name,
            policy_fingerprint=policy_fingerprint,
            sandbox_id=context.sandbox_id,
            session_id=provenance.session_id,
            submission_id=provenance.submission_id,
            attestation_id=secrets.token_hex(16),
            provider_adapter_schema=context.provider_adapter_schema,
            host=target.host,
            port=target.port,
            candidate_hash=_candidate_hash(candidate),
            issued_at=issued_at,
            expires_at=issued_at + self._attestation_lifetime_seconds,
            key_id=self._key_id,
        )
        payload = canonical_json_bytes(claims)
        signature = self._private_key.sign(payload)
        return b"ag1." + _encode(payload) + b"." + _encode(signature)

    def verify_attestation(
        self,
        attestation: bytes,
        candidate: AttestedCandidate,
        context: HarnessAdmissionContext,
        *,
        policy_fingerprint: str,
        now: int | None = None,
    ) -> AgentAttestationClaimsV1:
        """Verify a supervisor-supplied context-addition attestation."""
        payload, signature = _decode_token(
            attestation, prefix=b"ag1", malformed_reason="attestation_malformed"
        )
        try:
            self._public_key.verify(signature, payload)
        except InvalidSignature:
            raise ReceiptVerificationError("attestation_signature_invalid") from None
        try:
            claims = AgentAttestationClaimsV1.model_validate_json(payload, strict=True)
        except ValidationError:
            raise ReceiptVerificationError("attestation_malformed") from None
        if canonical_json_bytes(claims) != payload:
            raise ReceiptVerificationError("attestation_malformed")
        current = _now_seconds() if now is None else now
        if claims.key_id != self._key_id:
            raise ReceiptVerificationError("attestation_key_mismatch")
        if claims.issued_at > current + self._allowed_clock_skew_seconds:
            raise ReceiptVerificationError("attestation_not_yet_valid")
        if claims.expires_at <= current or claims.expires_at <= claims.issued_at:
            raise ReceiptVerificationError("attestation_expired")
        target = context.provider_target
        expected = (
            context.harness,
            context.harness_version,
            context.schema_version,
            context.hook.value,
            context.middleware_name,
            policy_fingerprint,
            context.sandbox_id,
            context.provider_adapter_schema,
            target.host,
            target.port,
            _candidate_hash(candidate),
        )
        actual = (
            claims.harness,
            claims.harness_version,
            claims.harness_schema,
            claims.hook,
            claims.middleware_binding,
            claims.policy_fingerprint,
            claims.sandbox_id,
            claims.provider_adapter_schema,
            claims.host,
            claims.port,
            claims.candidate_hash,
        )
        if actual != expected:
            raise ReceiptVerificationError("attestation_context_mismatch")
        return claims


def _candidate_hash(candidate: AttestedCandidate) -> str:
    return hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()


def _encode(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _decode(value: bytes) -> bytes:
    padding = b"=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except ValueError:
        raise ValueError("token is malformed") from None


def _decode_token(
    value: bytes, *, prefix: bytes, malformed_reason: str
) -> tuple[bytes, bytes]:
    if len(value) > 8 * 1024:
        raise ReceiptVerificationError(malformed_reason)
    parts = value.split(b".")
    if len(parts) != 3 or parts[0] != prefix or not parts[1] or not parts[2]:
        raise ReceiptVerificationError(malformed_reason)
    try:
        return _decode(parts[1]), _decode(parts[2])
    except ValueError:
        raise ReceiptVerificationError(malformed_reason) from None


def _now_seconds() -> int:
    return int(datetime.now(UTC).timestamp())


__all__ = [
    "AgentAttestationClaimsV1",
    "ReceiptAuthority",
    "ReceiptVerificationError",
]
