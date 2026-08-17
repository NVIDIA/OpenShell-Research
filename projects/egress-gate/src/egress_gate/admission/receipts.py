# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Short-lived Ed25519 admission receipts."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from pydantic import Field, ValidationError

from egress_gate.admission.adapters import PiInputV1
from egress_gate.admission.canonical import canonical_json_bytes
from egress_gate.admission.models import (
    AdmissionHook,
    HarnessAdmissionContext,
    PromptProvenance,
)
from egress_gate.base import StrictDomainModel
from egress_gate.string_validators import BoundedMetadataString, ScalarString


class ReceiptClaimsV1(StrictDomainModel):
    """All security context signed into one rendered-prompt receipt."""

    receipt_version: Literal["egress-receipt.v1"] = "egress-receipt.v1"
    canonicalization_version: Literal["canonical-json.v1"] = "canonical-json.v1"
    harness: Literal["pi"]
    harness_version: Literal["extension-v1"]
    harness_schema: Literal["openshell.pi-input.v1"]
    hook: Literal["rendered_prompt_admission"]
    middleware_binding: BoundedMetadataString
    policy_fingerprint: ScalarString
    sandbox_id: BoundedMetadataString
    session_id: BoundedMetadataString
    submission_id: BoundedMetadataString
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    provider_adapter_schema: Literal["openai.chat-completions.v1"]
    scheme: ScalarString
    host: ScalarString
    port: int = Field(ge=0, le=2**32 - 1)
    method: ScalarString
    path: ScalarString
    query: ScalarString
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
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
        lifetime_seconds: int = 30,
        allowed_clock_skew_seconds: int = 5,
    ) -> None:
        if not 1 <= lifetime_seconds <= 300:
            raise ValueError("receipt lifetime must be between 1 and 300 seconds")
        if not 0 <= allowed_clock_skew_seconds <= 30:
            raise ValueError("receipt clock skew must be between 0 and 30 seconds")
        self._private_key = private_key or Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        public_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._key_id = hashlib.sha256(public_bytes).hexdigest()[:16]
        self._lifetime_seconds = lifetime_seconds
        self._allowed_clock_skew_seconds = allowed_clock_skew_seconds
        self._consumed_receipts: dict[str, int] = {}
        self._consumed_receipts_lock = threading.Lock()

    @property
    def key_id(self) -> str:
        """Return the non-secret identifier of the active ephemeral key."""
        return self._key_id

    def issue(
        self,
        rendered_prompt: PiInputV1,
        context: HarnessAdmissionContext,
        provenance: PromptProvenance,
        *,
        policy_fingerprint: str,
        now: int | None = None,
    ) -> bytes:
        """Issue one opaque receipt after final admission validation."""
        if context.hook is not AdmissionHook.RENDERED_PROMPT:
            raise ValueError("receipts may be issued only for rendered prompts")
        issued_at = _now_seconds() if now is None else now
        target = context.provider_target
        claims = ReceiptClaimsV1(
            harness=context.harness,
            harness_version=context.harness_version,
            harness_schema=context.schema_version,
            hook=context.hook.value,
            middleware_binding=context.middleware_name,
            policy_fingerprint=policy_fingerprint,
            sandbox_id=context.sandbox_id,
            session_id=provenance.session_id,
            submission_id=provenance.submission_id,
            receipt_id=secrets.token_hex(16),
            provider_adapter_schema=context.provider_adapter_schema,
            scheme=target.scheme,
            host=target.host,
            port=target.port,
            method=target.method,
            path=target.path,
            query=target.query,
            rendered_prompt_hash=_prompt_hash(rendered_prompt),
            issued_at=issued_at,
            expires_at=issued_at + self._lifetime_seconds,
            key_id=self._key_id,
        )
        payload = canonical_json_bytes(claims)
        signature = self._private_key.sign(payload)
        return b"eg1." + _encode(payload) + b"." + _encode(signature)

    def verify(
        self,
        receipt: bytes,
        rendered_prompt: PiInputV1,
        context: HarnessAdmissionContext,
        *,
        policy_fingerprint: str,
        now: int | None = None,
    ) -> ReceiptClaimsV1:
        """Verify signature, lifetime, trusted context, target, and prompt hash."""
        if context.hook is not AdmissionHook.RENDERED_PROMPT:
            raise ReceiptVerificationError("receipt_context_mismatch")
        payload, signature = _decode_receipt(receipt)
        try:
            self._public_key.verify(signature, payload)
        except InvalidSignature:
            raise ReceiptVerificationError("receipt_signature_invalid") from None
        try:
            claims = ReceiptClaimsV1.model_validate_json(payload, strict=True)
        except ValidationError:
            raise ReceiptVerificationError("receipt_malformed") from None
        if canonical_json_bytes(claims) != payload:
            raise ReceiptVerificationError("receipt_malformed")
        current = _now_seconds() if now is None else now
        if claims.key_id != self._key_id:
            raise ReceiptVerificationError("receipt_key_mismatch")
        if claims.issued_at > current + self._allowed_clock_skew_seconds:
            raise ReceiptVerificationError("receipt_not_yet_valid")
        if claims.expires_at <= current or claims.expires_at <= claims.issued_at:
            raise ReceiptVerificationError("receipt_expired")
        target = context.provider_target
        expected = (
            context.harness,
            context.harness_version,
            context.schema_version,
            AdmissionHook.RENDERED_PROMPT.value,
            context.middleware_name,
            policy_fingerprint,
            context.sandbox_id,
            context.provider_adapter_schema,
            target.scheme,
            target.host,
            target.port,
            target.method,
            target.path,
            target.query,
            _prompt_hash(rendered_prompt),
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
            claims.scheme,
            claims.host,
            claims.port,
            claims.method,
            claims.path,
            claims.query,
            claims.rendered_prompt_hash,
        )
        if actual != expected:
            raise ReceiptVerificationError("receipt_context_mismatch")
        with self._consumed_receipts_lock:
            self._consumed_receipts = {
                receipt_id: expires_at
                for receipt_id, expires_at in self._consumed_receipts.items()
                if expires_at > current
            }
            if claims.receipt_id in self._consumed_receipts:
                raise ReceiptVerificationError("receipt_replayed")
            self._consumed_receipts[claims.receipt_id] = claims.expires_at
        return claims


def _prompt_hash(rendered_prompt: PiInputV1) -> str:
    return hashlib.sha256(canonical_json_bytes(rendered_prompt)).hexdigest()


def _encode(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _decode(value: bytes) -> bytes:
    padding = b"=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except ValueError:
        raise ReceiptVerificationError("receipt_malformed") from None


def _decode_receipt(receipt: bytes) -> tuple[bytes, bytes]:
    if len(receipt) > 8 * 1024:
        raise ReceiptVerificationError("receipt_malformed")
    parts = receipt.split(b".")
    if len(parts) != 3 or parts[0] != b"eg1" or not parts[1] or not parts[2]:
        raise ReceiptVerificationError("receipt_malformed")
    return _decode(parts[1]), _decode(parts[2])


def _now_seconds() -> int:
    return int(datetime.now(UTC).timestamp())


__all__ = [
    "ReceiptAuthority",
    "ReceiptClaimsV1",
    "ReceiptVerificationError",
]
