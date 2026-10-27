# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import io
import json
import time

import grpc
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf import struct_pb2

from egress_watchdog.bindings import supervisor_middleware_pb2 as pb2
from egress_watchdog.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_watchdog.ledger import Ledger
from egress_watchdog.server import AUDIENCE, Authenticator, Watchdog, build_manifest, create_server

SIGNING_KEY = Ed25519PrivateKey.generate()
PUBLIC_PEM = SIGNING_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def make_token(**overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": "openshell-gateway:openshell",
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
        "caller_kind": "supervisor",
        "sandbox_id": "sb-1",
    }
    headers = {"typ": overrides.pop("typ", "openshell-ext+jwt")}
    claims.update(overrides)
    return jwt.encode(claims, SIGNING_KEY, algorithm="EdDSA", headers=headers)


def bearer(token: str) -> list[tuple[str, str]]:
    return [("authorization", f"Bearer {token}")]


def test_manifest_declares_protocol_and_audience() -> None:
    manifest = build_manifest()
    assert manifest.expected_audience == AUDIENCE
    assert manifest.extension.protocol_version.major == 1
    assert "openshell.supervisor-middleware.contract" in manifest.extension.supported_capabilities


def test_authenticator_accepts_a_gateway_token() -> None:
    claims = Authenticator(PUBLIC_PEM, "openshell").verify(bearer(make_token()))
    assert claims["sandbox_id"] == "sb-1"


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        bearer(make_token(aud="urn:openshell:extension:middleware:other")),
        bearer(make_token(iss="openshell-gateway:someone-else")),
        bearer(make_token(typ="JWT")),
        bearer(make_token(exp=int(time.time()) - 10)),
        bearer(make_token(caller_kind="user")),
        bearer(
            jwt.encode(
                {"iss": "openshell-gateway:openshell", "aud": AUDIENCE, "exp": 2**31},
                Ed25519PrivateKey.generate(),
                algorithm="EdDSA",
                headers={"typ": "openshell-ext+jwt"},
            )
        ),
    ],
    ids=["missing", "audience", "issuer", "typ", "expired", "caller", "foreign-key"],
)
def test_authenticator_rejects(metadata: list[tuple[str, str]]) -> None:
    with pytest.raises(PermissionError):
        Authenticator(PUBLIC_PEM, "openshell").verify(metadata)


def evaluation(sandbox_id: str, size: int, budget: int | None) -> pb2.HttpRequestEvaluation:
    config = struct_pb2.Struct()
    if budget is not None:
        config.update({"budget": {"bytes_per_minute": budget}})
    return pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        context=pb2.RequestContext(sandbox_id=sandbox_id, sandbox="demo"),
        target=pb2.HttpRequestTarget(scheme="https", host="httpbin.org", method="POST", path="/"),
        headers=[pb2.HttpHeader(name="Content-Length", value=str(size))],
        config=config,
    )


async def run_against_server(calls):
    log = io.StringIO()
    server = create_server(Watchdog(Authenticator(PUBLIC_PEM, "openshell"), Ledger(), log))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            return await calls(pb2_grpc.SupervisorMiddlewareStub(channel)), log.getvalue()
    finally:
        await server.stop(grace=0)


def test_budget_denies_and_logs_over_grpc() -> None:
    async def calls(stub):
        metadata = bearer(make_token())
        first = await stub.EvaluateHttpRequest(evaluation("sb-1", 600, 1000), metadata=metadata)
        second = await stub.EvaluateHttpRequest(evaluation("sb-1", 600, 1000), metadata=metadata)
        return first, second

    (first, second), log = asyncio.run(run_against_server(calls))
    assert first.decision == pb2.DECISION_ALLOW
    assert second.decision == pb2.DECISION_DENY
    assert second.reason_code == "egress_budget_exceeded"
    lines = [json.loads(line) for line in log.splitlines()]
    assert [line["decision"] for line in lines] == ["allow", "deny"]
    assert lines[0]["request_bytes"] == 600


def test_token_for_another_sandbox_is_refused() -> None:
    async def calls(stub):
        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.EvaluateHttpRequest(
                evaluation("sb-2", 1, None), metadata=bearer(make_token(sandbox_id="sb-1"))
            )
        return error.value.code()

    code, _ = asyncio.run(run_against_server(calls))
    assert code == grpc.StatusCode.PERMISSION_DENIED


def test_unauthenticated_call_is_refused() -> None:
    async def calls(stub):
        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.Describe(pb2.MiddlewareDescribeRequest())
        return error.value.code()

    code, _ = asyncio.run(run_against_server(calls))
    assert code == grpc.StatusCode.UNAUTHENTICATED
