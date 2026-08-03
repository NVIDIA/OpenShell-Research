"""Programmatic Egress Gate server lifecycle and transport-isolation tests."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import grpc
import pytest

from egress_gate.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from egress_gate.errors import EgressGateError, ErrorCode, GateRegistryError
from egress_gate.gates import GateRegistry, create_builtin_registry
from egress_gate.service import server as server_module
from egress_gate.service.server import EgressGateServer
from egress_gate.service.servicer import EgressGateMiddleware


class _FakeServer:
    def __init__(self, *, bound_port: int = 50051) -> None:
        self.bound_port = bound_port
        self.addresses: list[str] = []
        self.started = False
        self.waited = False
        self.stop_graces: list[float | None] = []

    def add_insecure_port(self, address: str) -> int:
        self.addresses.append(address)
        return self.bound_port

    async def start(self) -> None:
        self.started = True

    async def wait_for_termination(self) -> bool:
        self.waited = True
        return True

    async def stop(self, grace: float | None) -> None:
        self.stop_graces.append(grace)


def test_server_requires_a_finalized_gate_registry() -> None:
    with pytest.raises(GateRegistryError, match="finalized"):
        EgressGateServer(GateRegistry())


@pytest.mark.parametrize("seconds", [True, 0, 31, float("inf")])
def test_server_validates_the_service_timeout(seconds: bool | int | float) -> None:
    with pytest.raises(ValueError, match="finite number greater than 0 and at most 30"):
        EgressGateServer(create_builtin_registry(), timeout_seconds=seconds)


def test_server_keeps_timeout_ownership_at_the_service_boundary() -> None:
    server = EgressGateServer(create_builtin_registry(), timeout_seconds=4.5)
    try:
        assert server._middleware._timeout_seconds == 4.5
        assert not hasattr(server._middleware._policy, "_timeout_seconds")
    finally:
        asyncio.run(server._middleware.close())


def test_server_sets_transport_limits_and_registers_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = object()
    transport_options: list[tuple[int, tuple[tuple[str, int], ...]]] = []
    registrations: list[tuple[EgressGateMiddleware, object]] = []

    def fake_factory(
        *,
        maximum_concurrent_rpcs: int,
        options: tuple[tuple[str, int], ...],
    ) -> object:
        transport_options.append((maximum_concurrent_rpcs, options))
        return fake_server

    def record_registration(middleware: EgressGateMiddleware, server: object) -> None:
        registrations.append((middleware, server))

    middleware = EgressGateMiddleware(create_builtin_registry())
    monkeypatch.setattr(grpc.aio, "server", fake_factory)
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
    assert transport_options == [
        (
            MAX_CONCURRENT_RPCS,
            (("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),),
        )
    ]
    assert registrations == [(middleware, fake_server)]


@pytest.mark.parametrize(
    ("listen", "port"),
    [("127.0.0.1:1", 1), ("middleware.local:65535", 65_535), ("[::1]:50051", 50_051)],
)
def test_listen_address_accepts_supported_tcp_forms(listen: str, port: int) -> None:
    assert server_module._validated_listen_port(listen) == port


@pytest.mark.parametrize(
    "listen",
    ["127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:-1", "[::1]", "::1:50051"],
)
def test_listen_address_rejects_invalid_forms(listen: str) -> None:
    with pytest.raises(EgressGateError) as error:
        server_module._validated_listen_port(listen)
    assert error.value.code is ErrorCode.SERVER_BIND_FAILED


@pytest.mark.asyncio
async def test_serve_async_starts_waits_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _FakeServer(bound_port=50053)
    closed: list[EgressGateMiddleware] = []

    async def record_close(middleware: EgressGateMiddleware) -> None:
        closed.append(middleware)

    server = EgressGateServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(EgressGateMiddleware, "close", record_close)

    await server.serve_async("127.0.0.1:50053")

    assert fake_server.addresses == ["127.0.0.1:50053"]
    assert fake_server.started is True
    assert fake_server.waited is True
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


@pytest.mark.asyncio
async def test_serve_async_sanitizes_bind_failures_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = _FakeServer(bound_port=0)
    closed: list[EgressGateMiddleware] = []

    async def record_close(middleware: EgressGateMiddleware) -> None:
        closed.append(middleware)

    server = EgressGateServer(create_builtin_registry())
    monkeypatch.setattr(server_module, "_create_grpc_server", lambda _: fake_server)
    monkeypatch.setattr(EgressGateMiddleware, "close", record_close)

    with pytest.raises(EgressGateError) as error:
        await server.serve_async("invalid-sensitive-listen:50051")

    assert error.value.code is ErrorCode.SERVER_BIND_FAILED
    assert "invalid-sensitive-listen" not in str(error.value)
    assert fake_server.stop_graces == [0]
    assert closed == [server._middleware]


def test_programmatic_server_import_does_not_load_cli_or_gate_transport() -> None:
    probe = (
        "import sys; "
        "from egress_gate.service import EgressGateServer; "
        "assert EgressGateServer.__name__ == 'EgressGateServer'; "
        "assert 'egress_gate.cli' not in sys.modules; "
        "assert 'typer' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)
