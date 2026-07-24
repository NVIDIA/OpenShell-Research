"""Registry-based server lifecycle and discovery CLI tests."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import grpc
import pytest
from typer.testing import CliRunner, Result

from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.engines import EntityProcessingStrategy
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.service import server as server_module
from privacy_guard.service.server import (
    MiddlewareServer,
    app,
    create_default_registry,
    create_server,
    serve,
)
from privacy_guard.service.servicer import PrivacyGuardMiddleware

_ANSI_STYLE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


class LifecycleServerFake:
    """Minimal async-server fake for lifecycle-only tests."""

    def __init__(
        self,
        *,
        bound_port: int = 50051,
        bind_error: RuntimeError | None = None,
        start_error: RuntimeError | None = None,
    ) -> None:
        self.bound_port = bound_port
        self.bind_error = bind_error
        self.start_error = start_error
        self.addresses: list[str] = []
        self.started = False
        self.waited = False
        self.stop_graces: list[float | None] = []

    def add_insecure_port(self, address: str) -> int:
        self.addresses.append(address)
        if self.bind_error is not None:
            raise self.bind_error
        return self.bound_port

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def wait_for_termination(self, timeout: float | None = None) -> bool:
        del timeout
        self.waited = True
        return True

    async def stop(self, grace: float | None) -> None:
        self.stop_graces.append(grace)


def _plain_output(result: Result) -> str:
    return _ANSI_STYLE_PATTERN.sub("", result.output)


def _middleware() -> PrivacyGuardMiddleware:
    return PrivacyGuardMiddleware(create_default_registry())


def test_default_registry_contains_the_builtin_regex_engine() -> None:
    registry = create_default_registry()

    assert registry.is_finalized is True
    assert registry.engine_names == ("regex",)
    description = registry.describe_engines()[0]
    assert description.engine == "regex"
    assert description.supported_strategy is EntityProcessingStrategy.REPLACE


def test_middleware_server_uses_an_injected_registry_and_default_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = create_default_registry()
    served: list[tuple[PrivacyGuardMiddleware, str]] = []

    async def record_serve(servicer: PrivacyGuardMiddleware, listen: str) -> None:
        served.append((servicer, listen))
        await servicer.close()

    monkeypatch.setattr(server_module, "serve", record_serve)

    MiddlewareServer(registry=registry, log_request_content=True).serve()

    assert len(served) == 1
    assert served[0][1] == "127.0.0.1:50051"
    assert served[0][0]._registry is registry
    assert served[0][0]._processors._log_request_content is True


def test_create_server_sets_transport_limits_and_registers_servicer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = object()
    server_options: list[tuple[int, tuple[tuple[str, int], ...]]] = []
    registrations: list[tuple[PrivacyGuardMiddleware, object]] = []

    def fake_server_factory(
        *,
        maximum_concurrent_rpcs: int,
        options: tuple[tuple[str, int], ...],
    ) -> object:
        server_options.append((maximum_concurrent_rpcs, options))
        return fake_server

    def record_registration(
        servicer: PrivacyGuardMiddleware,
        server: object,
    ) -> None:
        registrations.append((servicer, server))

    middleware = _middleware()
    monkeypatch.setattr(grpc.aio, "server", fake_server_factory)
    monkeypatch.setattr(
        server_module.pb2_grpc,
        "add_SupervisorMiddlewareServicer_to_server",
        record_registration,
    )
    try:
        result = create_server(middleware)
    finally:
        asyncio.run(middleware.close())

    assert result is fake_server
    assert server_options == [
        (
            MAX_CONCURRENT_RPCS,
            (("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),),
        )
    ]
    assert registrations == [(middleware, fake_server)]


def test_cli_help_exposes_only_pipeline_server_and_discovery_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    output = _plain_output(result)
    assert "serve" in output
    assert "schema" in output
    assert "engines" in output
    assert "--debug" in output
    assert "--debug-log-content" in output
    assert "--config" not in output
    assert "--profile" not in output
    assert "--scanner-name" not in output


def test_cli_engines_describes_the_installed_engine() -> None:
    result = CliRunner().invoke(app, ["engines"])

    assert result.exit_code == 0
    assert result.output.startswith("regex\treplace\t")
    assert "Detect overlapping regex matches" in result.output


def test_cli_schema_prints_the_finalized_discriminated_policy_schema() -> None:
    result = CliRunner().invoke(app, ["schema"])

    assert result.exit_code == 0
    schema = json.loads(result.output)
    serialized = json.dumps(schema, sort_keys=True)
    assert '"propertyName": "engine"' in serialized
    assert '"regex"' in serialized
    assert '"on_detection"' in serialized


def test_cli_serve_forwards_operational_options_only(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[str, bool]] = []

    def record_serve(self: MiddlewareServer, listen: str) -> None:
        calls.append((listen, self._servicer._processors._log_request_content))

    monkeypatch.setattr(MiddlewareServer, "serve", record_serve)

    with caplog.at_level(logging.WARNING, logger="privacy_guard.service.server"):
        result = CliRunner().invoke(
            app,
            ["--debug-log-content", "serve", "--listen", "127.0.0.1:50052"],
        )

    assert result.exit_code == 0
    assert calls == [("127.0.0.1:50052", True)]
    assert "privacy_guard_request_content_logging_enabled" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fake_server", "sensitive_address"),
    [
        (LifecycleServerFake(bound_port=0), "invalid-sensitive-listen-8472"),
        (
            LifecycleServerFake(
                bind_error=RuntimeError("invalid-sensitive-listen-9472")
            ),
            "invalid-sensitive-listen-9472",
        ),
    ],
)
async def test_serve_sanitizes_bind_failures_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    fake_server: LifecycleServerFake,
    sensitive_address: str,
) -> None:
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(servicer: PrivacyGuardMiddleware) -> None:
        closed.append(servicer)

    middleware = _middleware()
    monkeypatch.setattr(server_module, "create_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(PrivacyGuardError) as captured:
        await serve(middleware, sensitive_address)

    assert captured.value.code is ErrorCode.SERVER_BIND_FAILED
    assert captured.value.__cause__ is None
    assert sensitive_address not in str(captured.value)
    assert fake_server.started is False
    assert fake_server.waited is False
    assert fake_server.stop_graces == [0]
    assert closed == [middleware]


@pytest.mark.asyncio
async def test_serve_starts_waits_and_closes_on_normal_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = LifecycleServerFake()
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(servicer: PrivacyGuardMiddleware) -> None:
        closed.append(servicer)

    middleware = _middleware()
    monkeypatch.setattr(server_module, "create_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    await serve(middleware, "127.0.0.1:50053")

    assert fake_server.addresses == ["127.0.0.1:50053"]
    assert fake_server.started is True
    assert fake_server.waited is True
    assert fake_server.stop_graces == [0]
    assert closed == [middleware]


@pytest.mark.asyncio
async def test_serve_closes_resources_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = LifecycleServerFake(start_error=RuntimeError("startup failed"))
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(servicer: PrivacyGuardMiddleware) -> None:
        closed.append(servicer)

    middleware = _middleware()
    monkeypatch.setattr(server_module, "create_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(RuntimeError, match="startup failed"):
        await serve(middleware, "127.0.0.1:50054")

    assert fake_server.waited is False
    assert fake_server.stop_graces == [0]
    assert closed == [middleware]
