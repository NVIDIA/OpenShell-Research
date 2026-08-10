from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from slop_cop.config import ServiceConfig, SlopCopConfig
from slop_cop.rules.api import RuleMetadata

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class RuleRuntimeError(RuntimeError):
    """A content-safe rule execution failure."""


@dataclass(frozen=True, slots=True)
class ServiceResponse:
    data: Mapping[str, Any]
    audit: Mapping[str, str | int | float | bool | None]


@dataclass(slots=True)
class RuntimeManager:
    config: SlopCopConfig
    client: httpx.AsyncClient | None = None
    deadline: float | None = None
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)
    _owns_client: bool = field(default=False, init=False)
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.config.external_concurrency)
        if self.deadline is None:
            self.deadline = time.monotonic() + self.config.external_file_timeout_seconds

    async def __aenter__(self) -> RuntimeManager:
        if self.client is None:
            self.client = httpx.AsyncClient(
                follow_redirects=False,
                limits=httpx.Limits(max_connections=self.config.external_concurrency),
            )
            self._owns_client = True
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    def for_rule(self, metadata: RuleMetadata) -> RuleRuntime:
        return RuleRuntime(self, metadata)


@dataclass(frozen=True, slots=True)
class RuleRuntime:
    manager: RuntimeManager
    metadata: RuleMetadata

    @property
    def deadline(self) -> float:
        assert self.manager.deadline is not None
        return self.manager.deadline

    def service(self, name: str) -> NamedService:
        if name not in self.metadata.services:
            raise RuleRuntimeError(
                f"rule {self.metadata.id} attempted to use an undeclared service"
            )
        try:
            service_config = self.manager.config.services[name]
        except KeyError as error:
            raise RuleRuntimeError(f"named service {name!r} is not configured") from error
        return NamedService(self, name, service_config)


@dataclass(frozen=True, slots=True)
class NamedService:
    runtime: RuleRuntime
    name: str
    config: ServiceConfig

    async def post_json(self, payload: Mapping[str, Any]) -> ServiceResponse:
        manager = self.runtime.manager
        if manager.client is None:
            raise RuleRuntimeError("runtime manager is not active")
        token = manager.environment.get(self.config.token_env)
        if not token:
            raise RuleRuntimeError(f"required credential for service {self.name!r} is missing")

        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise RuleRuntimeError("external rule request is not valid JSON") from error
        content_hash = hashlib.sha256(encoded).hexdigest()
        idempotency = hashlib.sha256(
            f"{self.runtime.metadata.id}:{self.runtime.metadata.version}:"
            f"{manager.config.digest}:{content_hash}".encode()
        ).hexdigest()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": idempotency,
        }

        started = time.monotonic()
        response: httpx.Response | None = None
        body: bytes | None = None
        attempts = 0
        try:
            async with manager._semaphore:
                for attempts in range(1, self.config.max_attempts + 1):
                    remaining = self.runtime.deadline - time.monotonic()
                    timeout = min(self.config.timeout_seconds, remaining)
                    if timeout <= 0:
                        raise RuleRuntimeError("external rule deadline expired")
                    response = None
                    try:
                        async with asyncio.timeout(timeout):
                            request = manager.client.build_request(
                                "POST", self.config.url, content=encoded, headers=headers
                            )
                            response = await manager.client.send(request, stream=True)
                            if (
                                response.status_code in _RETRYABLE_STATUS_CODES
                                and attempts < self.config.max_attempts
                            ):
                                continue
                            if response.is_redirect or not response.is_success:
                                break
                            content_type = (
                                response.headers.get("content-type", "")
                                .split(";", 1)[0]
                                .strip()
                                .lower()
                            )
                            if content_type and content_type not in {
                                "application/json",
                                "application/problem+json",
                            }:
                                raise RuleRuntimeError(
                                    f"service {self.name!r} returned a non-JSON response"
                                )
                            buffer = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(buffer) + len(chunk) > self.config.max_response_bytes:
                                    raise RuleRuntimeError(
                                        f"service {self.name!r} response exceeded its size limit"
                                    )
                                buffer.extend(chunk)
                            body = bytes(buffer)
                            break
                    except (httpx.TransportError, TimeoutError):
                        if attempts >= self.config.max_attempts:
                            raise
                    finally:
                        if response is not None:
                            await response.aclose()
            assert response is not None
        except (httpx.TransportError, TimeoutError) as error:
            raise RuleRuntimeError(f"service {self.name!r} request failed") from error

        if response.is_redirect:
            raise RuleRuntimeError(f"service {self.name!r} returned a redirect")
        if response.status_code < 200 or response.status_code >= 300:
            raise RuleRuntimeError(f"service {self.name!r} returned HTTP {response.status_code}")
        assert body is not None
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuleRuntimeError(f"service {self.name!r} returned invalid JSON") from error
        if not isinstance(data, dict):
            raise RuleRuntimeError(f"service {self.name!r} response must be a JSON object")

        response_digest = hashlib.sha256(body).hexdigest()
        hostname = urlsplit(self.config.url).hostname or ""
        return ServiceResponse(
            data=data,
            audit={
                "service": self.name,
                "hostname": hostname,
                "request_content_hash": content_hash,
                "response_digest": response_digest,
                "request_id": response.headers.get("x-request-id"),
                "attempts": attempts,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "outcome": "success",
            },
        )
