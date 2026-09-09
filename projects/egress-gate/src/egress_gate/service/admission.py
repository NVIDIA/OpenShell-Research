# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authenticated HTTP admission beside the standard OpenShell middleware RPCs."""

from __future__ import annotations

import base64
import hmac
import json
import ssl
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from pydantic import SecretStr, ValidationError

from egress_gate.admission import (
    MAX_ADMISSION_BODY_BYTES,
    AdmissionHook,
    AdmissionProvenance,
    HarnessAdmissionContext,
    HarnessAdmissionRequest,
)
from egress_gate.base import StrictDomainModel
from egress_gate.errors import EgressGateError, TimeoutExpiredError
from egress_gate.request import HttpTarget
from egress_gate.service.servicer import EgressGateMiddleware
from egress_gate.string_validators import BoundedMetadataString


class AdmissionServerConfig(StrictDomainModel):
    """Operator-owned configuration; never supplied by the sandbox application."""

    listen: str
    tls_certificate: Path
    tls_private_key: Path
    gateway_public_key: Path
    gateway_issuer: str
    gateway_audience: str
    middleware_name: BoundedMetadataString
    bearer_token: SecretStr
    sandbox_id_file: Path
    provider_target: HttpTarget
    policy: dict[str, object]


class AdmissionCall(StrictDomainModel):
    """Only the candidate and correlation identifiers are caller assertions."""

    kind: AdmissionHook
    session_id: BoundedMetadataString
    submission_id: BoundedMetadataString
    body: dict[str, object]


def create_admission_application(
    middleware: EgressGateMiddleware, config: AdmissionServerConfig
) -> web.Application:
    """Create one bounded endpoint; identity and policy come from the operator."""

    async def admit(request: web.Request) -> web.Response:
        authorizations = request.headers.getall("Authorization", [])
        expected = f"Bearer {config.bearer_token.get_secret_value()}".encode()
        if len(authorizations) != 1 or not hmac.compare_digest(
            authorizations[0].encode(), expected
        ):
            raise web.HTTPUnauthorized(text="admission authentication failed")
        try:
            call = AdmissionCall.model_validate_json(await request.read())
            schema = call.body.get("schema_version")
            if not isinstance(schema, str):
                raise ValueError("missing schema")
            # Setup learns the real ID after sandbox creation. Reading this small,
            # host-owned file lets setup finish without an administrative HTTP API.
            # Until it exists, admission is unavailable, never anonymously allowed.
            sandbox_id = config.sandbox_id_file.read_text().strip()
            context = HarnessAdmissionContext(
                request_id=str(uuid4()),
                sandbox_id=sandbox_id,
                middleware_name=config.middleware_name,
                harness="pi",
                harness_version="sdk-v1",
                hook=call.kind,
                schema_version=schema,
                provider_target=config.provider_target,
                provider_adapter_schema="openai.request.v1",
            )
            result = await middleware.admit(
                HarnessAdmissionRequest(
                    request_body=json.dumps(
                        call.body,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode(),
                    provenance=AdmissionProvenance(
                        session_id=call.session_id, submission_id=call.submission_id
                    ),
                ),
                context,
                config.policy,
            )
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(text="invalid admission request") from None
        except (OSError, EgressGateError, TimeoutExpiredError):
            raise web.HTTPServiceUnavailable(
                text="admission is not provisioned"
            ) from None
        return web.json_response(
            {
                "decision": result.decision.value,
                "reason_code": result.reason_code,
                "replacement": (
                    json.loads(result.replacement_body)
                    if result.replacement_body is not None
                    else None
                ),
                "receipt": (
                    base64.urlsafe_b64encode(result.attestation).decode("ascii")
                    if result.attestation is not None
                    else None
                ),
            }
        )

    application = web.Application(client_max_size=MAX_ADMISSION_BODY_BYTES)
    application.router.add_post("/v1/admission", admit)
    return application


def admission_tls_context(config: AdmissionServerConfig) -> ssl.SSLContext:
    """Use operator-provisioned TLS, with no insecure fallback."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config.tls_certificate, config.tls_private_key)
    return context
