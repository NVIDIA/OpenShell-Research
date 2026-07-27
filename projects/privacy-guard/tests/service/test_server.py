"""Programmatic Privacy Guard server lifecycle tests."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

import grpc
import pytest

from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.errors import EngineRegistryError, ErrorCode, PrivacyGuardError
from privacy_guard.service import server as server_module
from privacy_guard.service.server import PrivacyGuardServer
from privacy_guard.service.servicer import PrivacyGuardMiddleware


class _LifecycleServerFake:
    """Minimal async-server fake for lifecycle-only tests."""

    def __init__(
        self,
        *,
        bound_port: int = 50051,
        bind_error: RuntimeError | None = None,
        start_error: RuntimeError | None = None,
        wait_error: BaseException | None = None,
        block_stop: bool = False,
    ) -> None:
        self.bound_port = bound_port
        self.bind_error = bind_error
        self.start_error = start_error
        self.wait_error = wait_error
        self.addresses: list[str] = []
        self.started = False
        self.waited = False
        self.stop_graces: list[float | None] = []
        self.stop_started = asyncio.Event()
        self.stop_release = asyncio.Event()
        if not block_stop:
            self.stop_release.set()

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
        if self.wait_error is not None:
            raise self.wait_error
        self.waited = True
        return True

    async def stop(self, grace: float | None) -> None:
        self.stop_graces.append(grace)
        self.stop_started.set()
        await self.stop_release.wait()


def test_programmatic_server_runs_with_injected_registry_and_default_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = create_builtin_registry()
    served: list[tuple[PrivacyGuardServer, str]] = []

    async def record_serve(self: PrivacyGuardServer, listen: str) -> None:
        served.append((self, listen))
        await self._middleware.close()

    monkeypatch.setattr(PrivacyGuardServer, "serve_async", record_serve)

    server = PrivacyGuardServer(
        registry=registry,
        timeout_seconds=4.5,
        log_request_content=True,
    )
    server.serve_sync()

    assert served == [(server, "127.0.0.1:50051")]
    assert server._middleware._registry is registry
    assert server._middleware._processors._timeout_seconds == 4.5
    assert server._middleware._processors._log_request_content is True


def test_programmatic_server_requires_an_explicit_finalized_registry() -> None:
    with pytest.raises(EngineRegistryError, match="finalized"):
        PrivacyGuardServer(EngineRegistry())


@pytest.mark.parametrize("timeout_seconds", [True, 0, 31, float("inf")])
def test_programmatic_server_rejects_invalid_processing_timeout(
    timeout_seconds: bool | int | float,
) -> None:
    with pytest.raises(
        ValueError,
        match="finite number greater than 0 and at most 30",
    ):
        PrivacyGuardServer(
            create_builtin_registry(),
            timeout_seconds=timeout_seconds,
        )


def test_synchronous_server_exits_cleanly_after_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = PrivacyGuardServer(create_builtin_registry())

    async def interrupt(self: PrivacyGuardServer, listen: str) -> None:
        del self, listen
        raise KeyboardInterrupt

    monkeypatch.setattr(PrivacyGuardServer, "serve_async", interrupt)

    server.serve_sync()
    asyncio.run(server._middleware.close())


def test_programmatic_server_import_does_not_load_the_cli_framework() -> None:
    probe = (
        "import sys; "
        "from privacy_guard.service import PrivacyGuardServer; "
        "assert PrivacyGuardServer.__name__ == 'PrivacyGuardServer'; "
        "assert 'privacy_guard.cli' not in sys.modules; "
        "assert 'typer' not in sys.modules"
    )

    subprocess.run([sys.executable, "-c", probe], check=True)


def test_engine_import_does_not_load_the_server_transport() -> None:
    probe = (
        "import sys; "
        "import privacy_guard.engines; "
        "assert 'privacy_guard.service' not in sys.modules; "
        "assert 'grpc' not in sys.modules"
    )

    subprocess.run([sys.executable, "-c", probe], check=True)


def test_server_sets_transport_limits_and_registers_middleware(
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
        middleware: PrivacyGuardMiddleware,
        server: object,
    ) -> None:
        registrations.append((middleware, server))

    middleware = _middleware()
    monkeypatch.setattr(grpc.aio, "server", fake_server_factory)
    monkeypatch.setattr(
        server_module.pb2_grpc,
        "add_SupervisorMiddlewareServicer_to_server",
        record_registration,
    )
    try:
        result = server_module._create_grpc_server(middleware)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fake_server", "sensitive_address"),
    [
        (_LifecycleServerFake(bound_port=0), "invalid-sensitive-listen-8472"),
        (
            _LifecycleServerFake(
                bind_error=RuntimeError("invalid-sensitive-listen-9472")
            ),
            "invalid-sensitive-listen-9472",
        ),
    ],
)
async def test_serve_async_sanitizes_bind_failures_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    fake_server: _LifecycleServerFake,
    sensitive_address: str,
) -> None:
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(PrivacyGuardError) as captured:
        await server.serve_async(sensitive_address)

    assert captured.value.code is ErrorCode.SERVER_BIND_FAILED
    assert captured.value.__cause__ is None
    assert sensitive_address not in str(captured.value)
    assert fake_server.started is False
    assert fake_server.waited is False
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


@pytest.mark.asyncio
async def test_serve_async_starts_waits_and_closes_on_normal_termination(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_server = _LifecycleServerFake(bound_port=50053)
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with caplog.at_level(logging.INFO, logger="privacy_guard.service.server"):
        await server.serve_async("127.0.0.1:50053")

    assert fake_server.addresses == ["127.0.0.1:50053"]
    assert fake_server.started is True
    assert fake_server.waited is True
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]
    assert "privacy_guard_server_bound listen='127.0.0.1:50053'" in caplog.text


@pytest.mark.asyncio
async def test_serve_async_propagates_cancellation_after_closing_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _LifecycleServerFake(
        bound_port=50054,
        wait_error=asyncio.CancelledError(),
    )
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(asyncio.CancelledError):
        await server.serve_async("127.0.0.1:50054")

    assert fake_server.started is True
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


@pytest.mark.asyncio
async def test_serve_async_preserves_cancellation_during_server_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _LifecycleServerFake(bound_port=50055, block_stop=True)
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    serving = asyncio.create_task(server.serve_async("127.0.0.1:50055"))
    await fake_server.stop_started.wait()
    serving.cancel()
    await asyncio.sleep(0)

    assert serving.done() is False

    fake_server.stop_release.set()
    with pytest.raises(asyncio.CancelledError):
        await serving

    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


@pytest.mark.asyncio
async def test_serve_async_sanitizes_startup_failures_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _LifecycleServerFake(
        bound_port=50056,
        start_error=RuntimeError("startup failed"),
    )
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(PrivacyGuardError) as captured:
        await server.serve_async("127.0.0.1:50056")

    assert captured.value.code is ErrorCode.SERVER_BIND_FAILED
    assert captured.value.__cause__ is None
    assert "server.start" in str(captured.value)
    assert "startup failed" not in str(captured.value)
    assert fake_server.waited is False
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


@pytest.mark.parametrize(
    ("listen", "port"),
    [
        ("127.0.0.1:1", 1),
        ("middleware.local:65535", 65_535),
        ("[::1]:50051", 50_051),
    ],
)
def test_listen_address_accepts_supported_tcp_forms(listen: str, port: int) -> None:
    assert server_module._validated_listen_port(listen) == port


@pytest.mark.parametrize(
    "listen",
    [
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:99999",
        "127.0.0.1:-1",
        "[::1]",
        "::1:50051",
    ],
)
def test_listen_address_rejects_invalid_numeric_ports_and_forms(
    listen: str,
) -> None:
    with pytest.raises(PrivacyGuardError) as captured:
        server_module._validated_listen_port(listen)

    assert captured.value.code is ErrorCode.SERVER_BIND_FAILED


@pytest.mark.asyncio
async def test_serve_async_rejects_mismatched_bound_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _LifecycleServerFake(bound_port=34_463)
    closed: list[PrivacyGuardMiddleware] = []

    async def record_close(middleware: PrivacyGuardMiddleware) -> None:
        closed.append(middleware)

    server = PrivacyGuardServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(PrivacyGuardMiddleware, "close", record_close)

    with pytest.raises(PrivacyGuardError) as captured:
        await server.serve_async("127.0.0.1:9999")

    assert captured.value.code is ErrorCode.SERVER_BIND_FAILED
    assert fake_server.started is False
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


def _middleware() -> PrivacyGuardMiddleware:
    return PrivacyGuardMiddleware(create_builtin_registry())
