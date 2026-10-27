# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Egress watchdog: an OpenShell supervisor middleware that accounts for allowed traffic.

Every call is TLS and carries a gateway-signed Ed25519 JWT; there is no
insecure mode. The watchdog is a component sandbox nodes must not be able to
touch, so it never runs unauthenticated, even in development.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import grpc
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from google.protobuf import json_format

from egress_watchdog.bindings import extension_pb2 as ext_pb2
from egress_watchdog.bindings import supervisor_middleware_pb2 as pb2
from egress_watchdog.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_watchdog.ledger import Budget, Ledger

SERVICE_NAME = "egress-watchdog"
SERVICE_VERSION = "0.1.0"
AUDIENCE = f"urn:openshell:extension:middleware:{SERVICE_NAME}"
# The supervisor skips middleware for larger bodies and applies on_error, so
# attach the watchdog with on_error: fail_closed to make this a request ceiling.
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_MESSAGE_BYTES = MAX_PAYLOAD_BYTES + 1024 * 1024


class Authenticator:
    """Verify the gateway's extension JWT on each call."""

    def __init__(self, public_key_pem: bytes, gateway_id: str) -> None:
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("gateway JWT public key must be Ed25519")
        self._key = key
        self._issuer = f"openshell-gateway:{gateway_id}"

    def verify(self, metadata: Sequence[tuple[str, str | bytes]]) -> dict[str, object]:
        """Return the token claims, or raise PermissionError."""
        values = [value for key, value in metadata if key == "authorization"]
        if len(values) != 1 or not isinstance(values[0], str):
            raise PermissionError("missing bearer token")
        scheme, _, token = values[0].partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise PermissionError("missing bearer token")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("typ") != "openshell-ext+jwt":
                raise PermissionError("unexpected token type")
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["EdDSA"],
                audience=AUDIENCE,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss"]},
            )
        except jwt.PyJWTError as error:
            raise PermissionError(f"invalid token: {error}") from error
        if claims.get("caller_kind") not in ("gateway", "supervisor"):
            raise PermissionError("unexpected caller_kind")
        return claims


def build_manifest() -> pb2.MiddlewareManifest:
    return pb2.MiddlewareManifest(
        name=SERVICE_NAME,
        service_version=SERVICE_VERSION,
        expected_audience=AUDIENCE,
        bindings=[
            pb2.MiddlewareBinding(
                operation=pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST,
                phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                max_payload_bytes=MAX_PAYLOAD_BYTES,
            )
        ],
        extension=ext_pb2.PeerMetadata(
            protocol_version=ext_pb2.ProtocolVersion(major=1, minor=0),
            implementation_name=f"openshell-research/{SERVICE_NAME}",
            implementation_version=SERVICE_VERSION,
            supported_capabilities=["openshell.supervisor-middleware.contract"],
        ),
    )


def request_bytes(request: pb2.HttpRequestEvaluation) -> int:
    """Bytes the request sends: the larger of Content-Length and the body we received."""
    declared = 0
    for header in request.headers:
        if header.name.lower() == "content-length" and header.value.isdigit():
            declared = int(header.value)
    return max(declared, len(request.body))


class Watchdog(pb2_grpc.SupervisorMiddlewareServicer):
    def __init__(self, authenticator: Authenticator, ledger: Ledger, log: TextIO) -> None:
        self._authenticator = authenticator
        self._ledger = ledger
        self._log = log

    async def _authenticate(self, context: grpc.aio.ServicerContext) -> dict[str, object]:
        try:
            return self._authenticator.verify(context.invocation_metadata() or ())
        except PermissionError as error:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, str(error))
            raise  # abort raises; this keeps type checkers honest

    async def Describe(self, request, context):
        await self._authenticate(context)
        return build_manifest()

    async def ValidateConfig(self, request, context):
        await self._authenticate(context)
        try:
            Budget.from_config(json_format.MessageToDict(request.config))
        except ValueError as error:
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        return pb2.ValidateConfigResponse(valid=True)

    async def EvaluateHttpRequest(self, request, context):
        claims = await self._authenticate(context)
        sandbox_id = request.context.sandbox_id
        if claims.get("caller_kind") != "supervisor" or claims.get("sandbox_id") != sandbox_id:
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED, "token does not match the request's sandbox"
            )
        if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
            return pb2.HttpRequestResult(
                decision=pb2.DECISION_DENY,
                reason="unsupported middleware phase",
                reason_code="unsupported_phase",
            )

        budget = Budget.from_config(json_format.MessageToDict(request.config))
        size = request_bytes(request)
        verdict = self._ledger.record(sandbox_id, request.target.host, size, budget)
        self._write_log_line(request, size, verdict.window_bytes, verdict)

        result = pb2.HttpRequestResult(
            decision=pb2.DECISION_ALLOW if verdict.allow else pb2.DECISION_DENY,
            reason=verdict.reason,
            reason_code=verdict.reason_code,
        )
        if verdict.finding:
            result.findings.append(
                pb2.Finding(
                    type="egress_budget",
                    label=request.target.host,
                    count=1,
                    confidence="high",
                    severity="high" if not verdict.allow else "medium",
                )
            )
        return result

    def _write_log_line(self, request, size: int, window_bytes: int, verdict) -> None:
        # One line per request is the accounting record. Never log bodies or queries.
        line = {
            "ts": round(time.time(), 3),
            "sandbox_id": request.context.sandbox_id,
            "sandbox": request.context.sandbox,
            "binary": request.context.originating_process.binary,
            "method": request.target.method,
            "host": request.target.host,
            "path": request.target.path,
            "request_bytes": size,
            "window_bytes": window_bytes,
            "decision": "allow" if verdict.allow else "deny",
            "reason_code": verdict.reason_code,
        }
        print(json.dumps(line), file=self._log, flush=True)


def create_server(watchdog: Watchdog) -> grpc.aio.Server:
    server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        )
    )
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(watchdog, server)
    return server


async def serve(arguments: argparse.Namespace) -> None:
    authenticator = Authenticator(arguments.jwt_public_key.read_bytes(), arguments.gateway_id)
    server = create_server(Watchdog(authenticator, Ledger(), sys.stdout))
    credentials = grpc.ssl_server_credentials(
        [(arguments.tls_key.read_bytes(), arguments.tls_cert.read_bytes())]
    )
    if server.add_secure_port(arguments.listen, credentials) == 0:
        raise RuntimeError(f"could not bind watchdog to {arguments.listen}")
    await server.start()
    print(f"{SERVICE_NAME} listening on {arguments.listen}", file=sys.stderr, flush=True)
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=0)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the two-box egress watchdog")
    parser.add_argument("--listen", required=True, help="host:port to bind")
    parser.add_argument("--tls-cert", type=Path, required=True)
    parser.add_argument("--tls-key", type=Path, required=True)
    parser.add_argument(
        "--jwt-public-key", type=Path, required=True, help="gateway Ed25519 public.pem"
    )
    parser.add_argument("--gateway-id", default="openshell")
    asyncio.run(serve(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
