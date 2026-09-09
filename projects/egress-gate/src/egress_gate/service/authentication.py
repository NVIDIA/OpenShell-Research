# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify the upstream OpenShell extension-token contract at the RPC boundary."""

from __future__ import annotations

from collections.abc import Iterable

import jwt

from egress_gate.bindings import supervisor_middleware_pb2 as pb2


class GatewayAuthentication:
    """A single operator-provisioned gateway key, issuer, and exact audience."""

    def __init__(self, public_key: bytes, issuer: str, audience: str) -> None:
        self._public_key = public_key
        self._issuer = issuer
        self.audience = audience

    def verify(
        self, metadata: Iterable[tuple[str, str | bytes]], request: object
    ) -> None:
        """Raise on unauthenticated calls or a forged supervisor request context."""
        values = [value for key, value in metadata if key == "authorization"]
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError("authentication required")
        scheme, separator, token = values[0].partition(" ")
        if scheme != "Bearer" or not separator:
            raise ValueError("authentication required")
        if jwt.get_unverified_header(token).get("typ") != "openshell-ext+jwt":
            raise ValueError("incorrect token type")
        claims = jwt.decode(
            token,
            self._public_key,
            algorithms=["EdDSA"],
            issuer=self._issuer,
            audience=self.audience,
            options={"require": ["iss", "aud", "exp", "iat", "caller_kind"]},
        )
        kind = claims["caller_kind"]
        if kind not in ("gateway", "supervisor"):
            raise ValueError("invalid caller")
        if isinstance(request, pb2.HttpRequestEvaluation) and (
            kind != "supervisor"
            or not request.context.sandbox_id
            or claims.get("sandbox_id") != request.context.sandbox_id
        ):
            raise ValueError("sandbox identity mismatch")
