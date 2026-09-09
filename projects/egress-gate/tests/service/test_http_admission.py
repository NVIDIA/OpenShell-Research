# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real local HTTP/RPC transport tests; model traffic uses checked-in fixtures."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import jwt
import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf import empty_pb2, json_format, message_factory

from egress_gate.admission import RECEIPT_HEADER
from egress_gate.bindings import supervisor_middleware_pb2 as pb
from egress_gate.bindings import supervisor_middleware_pb2_grpc as rpc
from egress_gate.gates import create_builtin_registry
from egress_gate.service.admission import (
    AdmissionServerConfig,
    admission_tls_context,
    create_admission_application,
)
from egress_gate.service.authentication import GatewayAuthentication
from egress_gate.service.server import _create_grpc_server
from egress_gate.service.servicer import EgressGateMiddleware

PROJECT = Path(__file__).resolve().parents[2]
AUDIENCE = "urn:openshell:extension:middleware:pi-egress"
AUTHORIZATION = {"Authorization": "Bearer test-admission-credential"}


@pytest.mark.asyncio
async def test_http_admission_allow_deny_replace_and_authentication(
    tmp_path: Path,
) -> None:
    async with _clients(tmp_path) as (client, _, config, _, _):
        denied_auth = await client.post("/v1/admission", json=_call("safe"))
        assert denied_auth.status == 401
        for text, expected in (
            ("safe", "allow"),
            ("DENY_THIS", "deny"),
            ("REDACT_THIS", "replace"),
        ):
            response = await client.post(
                "/v1/admission", json=_call(text), headers=AUTHORIZATION
            )
            assert response.status == 200
            result = await response.json()
            assert result["decision"] == expected, result["reason_code"]
            assert result["receipt"] is None
            if expected == "replace":
                assert result["replacement"]["text"] == "[REDACTED]"
        forged = {**_call("safe"), "sandbox_id": "somebody-else"}
        response = await client.post(
            "/v1/admission", json=forged, headers=AUTHORIZATION
        )
        assert response.status == 400
        config.sandbox_id_file.unlink()
        response = await client.post(
            "/v1/admission", json=_call("safe"), headers=AUTHORIZATION
        )
        assert response.status == 503


@pytest.mark.asyncio
async def test_http_receipt_is_verified_and_stripped_by_standard_authenticated_rpc(
    tmp_path: Path,
) -> None:
    async with _clients(tmp_path) as (client, stub, config, token, _):
        response = await client.post(
            "/v1/admission",
            json=_call("safe", kind="provider_context"),
            headers=AUTHORIZATION,
        )
        result = await response.json()
        assert result["decision"] == "allow", result["reason_code"]
        assert result["receipt"]
        request = _network(config, result["receipt"])
        metadata = (("authorization", f"Bearer {token}"),)
        allowed = await stub.EvaluateHttpRequest(request, metadata=metadata)
        assert allowed.decision == pb.DECISION_ALLOW
        assert allowed.header_mutations[-1].remove.name == RECEIPT_HEADER
        with pytest.raises(grpc.aio.AioRpcError) as failure:
            await stub.EvaluateHttpRequest(request)
        assert failure.value.code() == grpc.StatusCode.UNAUTHENTICATED
        request.context.sandbox_id = "forged"
        with pytest.raises(grpc.aio.AioRpcError) as failure:
            await stub.EvaluateHttpRequest(request, metadata=metadata)
        assert failure.value.code() == grpc.StatusCode.UNAUTHENTICATED
        request.context.sandbox_id = "sandbox"
        request.headers.pop()
        denied = await stub.EvaluateHttpRequest(request, metadata=metadata)
        assert denied.reason_code == "attestation_missing"
        request.headers.add(name=RECEIPT_HEADER, value=result["receipt"])
        request.headers.add(name=RECEIPT_HEADER, value=result["receipt"])
        denied = await stub.EvaluateHttpRequest(request, metadata=metadata)
        assert denied.reason_code == "attestation_malformed"
        empty = message_factory.GetMessageClass(
            empty_pb2.DESCRIPTOR.message_types_by_name["Empty"]
        )()
        manifest = await stub.Describe(empty, metadata=metadata)
        assert manifest.expected_audience == AUDIENCE
        assert len(manifest.bindings) == 1


@pytest.mark.parametrize("change", ["issuer", "audience", "expired", "type", "key"])
def test_gateway_authentication_rejects_invalid_trust_claims(change: str) -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = {
        "iss": "trusted-gateway",
        "aud": AUDIENCE,
        "iat": int(time.time()) - 10,
        "exp": int(time.time()) + 60,
        "caller_kind": "supervisor",
        "sandbox_id": "sandbox",
    }
    if change == "issuer":
        claims["iss"] = "some-other-gateway"
    elif change == "audience":
        claims["aud"] = "another-service"
    elif change == "expired":
        claims["exp"] = int(time.time()) - 1
    token = jwt.encode(
        claims,
        Ed25519PrivateKey.generate() if change == "key" else key,
        algorithm="EdDSA",
        headers={"typ": "JWT" if change == "type" else "openshell-ext+jwt"},
    )
    request = pb.HttpRequestEvaluation(context=pb.RequestContext(sandbox_id="sandbox"))
    with pytest.raises((ValueError, jwt.PyJWTError)):
        GatewayAuthentication(public, "trusted-gateway", AUDIENCE).verify(
            (("authorization", f"Bearer {token}"),), request
        )


@pytest.mark.asyncio
async def test_pi_session_through_admission_and_authenticated_egress(
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    example = PROJECT / "examples/pi-attested-admission"
    subprocess.run(
        [
            sys.executable,
            str(example / "prepare.py"),
            "--state",
            str(tmp_path),
            "--host-ip",
            "127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    async with _clients(tmp_path) as (_, stub, config, token, middleware):
        config = config.model_copy(
            update={
                "tls_certificate": tmp_path / "tls/server/tls.crt",
                "tls_private_key": tmp_path / "tls/server/tls.key",
                "provider_target": config.provider_target.model_copy(
                    update={"host": "127.0.0.1", "port": unused_tcp_port}
                ),
            }
        )
        calls: list[bytes] = []

        async def provider(request: web.Request) -> web.Response:
            body = await request.read()
            evaluation = pb.HttpRequestEvaluation(
                phase=pb.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                context=pb.RequestContext(sandbox_id="sandbox", request_id="pi"),
                target=pb.HttpRequestTarget(**config.provider_target.model_dump()),
                middleware_name=config.middleware_name,
                body=body,
                headers=[
                    pb.HttpHeader(name=k, value=v) for k, v in request.headers.items()
                ],
            )
            json_format.ParseDict(config.policy, evaluation.config)
            metadata = (("authorization", f"Bearer {token}"),)
            result = await stub.EvaluateHttpRequest(evaluation, metadata=metadata)
            assert result.decision == pb.DECISION_ALLOW, result.reason_code
            assert result.header_mutations[-1].remove.name == RECEIPT_HEADER
            assert (
                "REDACT_THIS" not in body.decode() and "DENY_THIS" not in body.decode()
            )
            calls.append(body)
            if len(calls) == 1:
                changed = json.loads(body)
                next(m for m in changed["messages"] if m["role"] == "user")[
                    "content"
                ] = "tampered"
                evaluation.body = json.dumps(changed).encode()
                denied = await stub.EvaluateHttpRequest(evaluation, metadata=metadata)
                assert denied.reason_code == "context_hash_mismatch"
            assert len(calls) <= 7, "unexpected model call"
            delta: dict[str, object] = {"role": "assistant", "content": "REDACT_THIS"}
            finish = "stop"
            if len(calls) == 2:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": '{"path":"notes.txt"}',
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            elif len(calls) in (4, 7):
                delta["content"] = "Approved summary"
            chunk = {
                "id": "local",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return web.Response(
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                content_type="text/event-stream",
            )

        application = create_admission_application(middleware, config)
        application.router.add_post("/v1/chat/completions", provider)
        server = TestServer(application, scheme="https", port=unused_tcp_port)
        await server.start_server(ssl=admission_tls_context(config))
        try:
            process = await asyncio.create_subprocess_exec(
                "node",
                str(example / "app/dist/test/service-integration.js"),
                str(server.make_url("/")).rstrip("/"),
                str(tmp_path),
                env=os.environ | {"NODE_EXTRA_CA_CERTS": str(tmp_path / "tls/ca.crt")},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert process.returncode == 0, (stdout + stderr).decode()
            assert len(calls) == 7
            assert any(m["role"] == "tool" for m in json.loads(calls[2])["messages"])
        finally:
            await server.close()


@asynccontextmanager
async def _clients(
    directory: Path,
) -> AsyncIterator[
    tuple[
        TestClient[web.Request, web.Application],
        rpc.SupervisorMiddlewareStub,
        AdmissionServerConfig,
        str,
        EgressGateMiddleware,
    ]
]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (directory / "public.pem").write_bytes(public)
    (directory / "sandbox-id").write_text("sandbox")
    policy = yaml.safe_load(
        (PROJECT / "examples/pi-attested-admission/policy.yaml").read_text()
    )["network_middlewares"]["pi_egress_gate"]["config"]
    config = AdmissionServerConfig.model_validate(
        {
            "listen": "127.0.0.1:9443",
            "tls_certificate": directory / "unused.crt",
            "tls_private_key": directory / "unused.key",
            "gateway_public_key": directory / "public.pem",
            "gateway_issuer": "openshell-gateway:fixture",
            "gateway_audience": AUDIENCE,
            "middleware_name": "pi-egress",
            "bearer_token": "test-admission-credential",
            "sandbox_id_file": directory / "sandbox-id",
            "provider_target": {
                "scheme": "https",
                "host": "provider.test",
                "port": 443,
                "method": "POST",
                "path": "/v1/chat/completions",
                "query": "",
            },
            "policy": policy,
        }
    )
    token = jwt.encode(
        {
            "iss": config.gateway_issuer,
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "caller_kind": "supervisor",
            "sandbox_id": "sandbox",
        },
        key,
        algorithm="EdDSA",
        headers={"typ": "openshell-ext+jwt"},
    )
    middleware = EgressGateMiddleware(
        create_builtin_registry(),
        require_agent_attestation=True,
        expected_audience=AUDIENCE,
    )
    server = _create_grpc_server(
        middleware, GatewayAuthentication(public, config.gateway_issuer, AUDIENCE)
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    async with TestClient(
        TestServer(create_admission_application(middleware, config))
    ) as client:
        try:
            yield (
                client,
                rpc.SupervisorMiddlewareStub(channel),
                config,
                token,
                middleware,
            )
        finally:
            await channel.close()
            await server.stop(0)
            await middleware.close()


def _call(text: str, *, kind: str = "user_message") -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "openshell.pi-message.v1",
        "origin": "user",
        "text": text,
    }
    if kind == "provider_context":
        body = {
            "schema_version": "openshell.pi-provider-context.v1",
            "entries": [{"role": "user", "text": text}],
        }
    return {
        "kind": kind,
        "session_id": "session",
        "submission_id": "submission",
        "body": body,
    }


def _network(config: AdmissionServerConfig, receipt: str) -> pb.HttpRequestEvaluation:
    fixtures = json.loads(
        (PROJECT / "tests/admission/fixtures/pi-openai-completions.json").read_text()
    )
    body = fixtures["user_request"]
    body["messages"][1]["content"] = "safe"
    request = pb.HttpRequestEvaluation(
        phase=pb.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        context=pb.RequestContext(sandbox_id="sandbox", request_id="network"),
        target=pb.HttpRequestTarget(**config.provider_target.model_dump()),
        middleware_name="pi-egress",
        body=json.dumps(body).encode(),
        headers=[
            pb.HttpHeader(name="content-type", value="application/json"),
            pb.HttpHeader(name=RECEIPT_HEADER, value=receipt),
        ],
    )
    json_format.ParseDict(config.policy, request.config)
    return request
