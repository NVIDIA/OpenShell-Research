import logging
import re
from collections.abc import Sequence
from pathlib import Path

import grpc
import pytest
from google.protobuf import empty_pb2, message_factory
from google.protobuf.message import Message
from typer.testing import CliRunner, Result
from typing_extensions import override

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.constants import MAX_BODY_BYTES
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.processor import RequestProcessor
from privacy_guard.scanners import ScannerConfig
from privacy_guard.service import server as server_module
from privacy_guard.service.server import MiddlewareServer, app, create_server, serve
from privacy_guard.service.servicer import PrivacyGuardMiddleware

from ..scanner_helpers import DeterministicEmailScanner

_ANSI_STYLE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


class LifecycleServerFake(grpc.aio.Server):
    """Nominal gRPC server fake for lifecycle-only server tests."""

    def __init__(self) -> None:
        self.stopped = False

    @override
    def add_generic_rpc_handlers(
        self, generic_rpc_handlers: Sequence[grpc.GenericRpcHandler]
    ) -> None:
        raise AssertionError("generic handler registration is not under test")

    @override
    def add_insecure_port(self, address: str) -> int:
        raise NotImplementedError

    @override
    def add_secure_port(
        self, address: str, server_credentials: grpc.ServerCredentials
    ) -> int:
        raise AssertionError("secure binding is not under test")

    @override
    async def start(self) -> None:
        raise NotImplementedError

    @override
    async def stop(self, grace: float | None) -> None:
        self.stopped = True

    @override
    async def wait_for_termination(self, timeout: float | None = None) -> bool:
        raise NotImplementedError


def _middleware() -> PrivacyGuardMiddleware:
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )
    return PrivacyGuardMiddleware(RequestProcessor([scanner]))


def _plain_output(result: Result) -> str:
    return _ANSI_STYLE_PATTERN.sub("", result.output)


def test_middleware_server_wires_scanner_and_has_a_default_listen_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served: list[tuple[str, PrivacyGuardMiddleware]] = []

    async def record_serve(servicer: PrivacyGuardMiddleware, listen: str) -> None:
        served.append((listen, servicer))

    monkeypatch.setattr(server_module, "serve", record_serve)
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )

    MiddlewareServer(scanner=scanner).serve()

    assert len(served) == 1
    assert served[0][0] == "127.0.0.1:50051"
    assert isinstance(served[0][1], PrivacyGuardMiddleware)


def test_cli_reports_expected_configuration_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = PrivacyGuardError(ErrorCode.SCANNER_CONFIG_INVALID)

    def reject_configuration(*args: object, **kwargs: object) -> None:
        raise expected

    monkeypatch.setattr(server_module.RegexScanner, "from_yaml", reject_configuration)

    result = CliRunner().invoke(
        app,
        ["regex", "--config", "sensitive-path-8472.yaml"],
    )

    assert result.exit_code == 1
    assert result.output == f"{expected}\n"
    assert "Traceback" not in result.output
    assert "8472" not in result.output


def test_cli_reports_bind_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = PrivacyGuardError(ErrorCode.SERVER_BIND_FAILED)
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="regex", entity_types=frozenset({"email"}))
    )

    monkeypatch.setattr(
        server_module.RegexScanner,
        "from_yaml",
        lambda *args, **kwargs: scanner,
    )

    def reject_listen_address(self: MiddlewareServer, listen: str) -> None:
        raise expected

    monkeypatch.setattr(MiddlewareServer, "serve", reject_listen_address)

    result = CliRunner().invoke(
        app,
        ["regex", "--config", "scanner-config.yaml", "--listen", "bad:8472"],
    )

    assert result.exit_code == 1
    assert result.output == f"{expected}\n"
    assert "Traceback" not in result.output
    assert "8472" not in result.output


@pytest.mark.parametrize("profile", [None, "customer-support"])
def test_cli_profile_is_optional_and_forwarded_only_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
    profile: str | None,
) -> None:
    calls: list[tuple[str, str | None, str]] = []
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="regex", entity_types=frozenset({"email"}))
    )

    def load_scanner(
        path: str | Path,
        selected_profile: str | None,
        *,
        scanner_name: str,
    ) -> DeterministicEmailScanner:
        calls.append((str(path), selected_profile, scanner_name))
        return scanner

    monkeypatch.setattr(server_module.RegexScanner, "from_yaml", load_scanner)
    monkeypatch.setattr(MiddlewareServer, "serve", lambda self, listen: None)
    arguments = ["regex", "--config", "scanner-config.yaml"]
    if profile is not None:
        arguments.extend(("--profile", profile))

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0
    assert calls == [("scanner-config.yaml", profile, "regex")]


def test_cli_forwards_custom_scanner_name_to_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str]] = []
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="custom-regex", entity_types=frozenset({"email"}))
    )

    def load_scanner(
        path: str | Path,
        selected_profile: str | None,
        *,
        scanner_name: str,
    ) -> DeterministicEmailScanner:
        calls.append((str(path), selected_profile, scanner_name))
        return scanner

    monkeypatch.setattr(server_module.RegexScanner, "from_yaml", load_scanner)
    monkeypatch.setattr(MiddlewareServer, "serve", lambda self, listen: None)

    result = CliRunner().invoke(
        app,
        [
            "regex",
            "--config",
            "scanner-config.yaml",
            "--scanner-name",
            "custom-regex",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("scanner-config.yaml", None, "custom-regex")]


def test_cli_logs_server_start_without_configuration_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="custom-regex", entity_types=frozenset({"email"}))
    )
    monkeypatch.setattr(
        server_module.RegexScanner,
        "from_yaml",
        lambda *args, **kwargs: scanner,
    )
    monkeypatch.setattr(MiddlewareServer, "serve", lambda self, listen: None)

    with caplog.at_level(logging.INFO, logger="privacy_guard.service.server"):
        result = CliRunner().invoke(
            app,
            [
                "regex",
                "--config",
                "sensitive-config-8472.yaml",
                "--listen",
                "127.0.0.1:50051",
                "--scanner-name",
                "custom-regex",
            ],
        )

    assert result.exit_code == 0
    assert "privacy_guard_server_starting" in caplog.text
    assert "scanner=custom-regex" in caplog.text
    assert "listen=127.0.0.1:50051" in caplog.text
    assert "8472" not in caplog.text


def test_cli_always_requires_scanner_configuration() -> None:
    result = CliRunner().invoke(app, ["regex"])

    assert result.exit_code == 2
    assert "--config" in _plain_output(result)


def test_cli_exposes_root_and_builtin_scanner_help() -> None:
    root_help = CliRunner().invoke(app, ["--help"])

    assert root_help.exit_code == 0
    plain_root_help = _plain_output(root_help)
    assert "privacy-guard" in plain_root_help
    assert "regex" in plain_root_help
    assert "--debug" in plain_root_help
    assert "--debug-log-content" in plain_root_help

    scanner_help = CliRunner().invoke(app, ["regex", "--help"])

    assert scanner_help.exit_code == 0
    plain_scanner_help = _plain_output(scanner_help)
    assert "built-in RegexScanner" in plain_scanner_help
    assert "--config" in plain_scanner_help
    assert "--listen" in plain_scanner_help
    assert "--profile" in plain_scanner_help
    assert "--scanner-name" in plain_scanner_help


def test_cli_enables_explicit_request_content_logging(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    processor_options: list[bool] = []
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="regex", entity_types=frozenset({"email"}))
    )
    real_request_processor = server_module.RequestProcessor

    def create_processor(
        scanners: Sequence[DeterministicEmailScanner],
        *,
        log_request_content: bool = False,
    ) -> RequestProcessor:
        processor_options.append(log_request_content)
        return real_request_processor(
            scanners,
            log_request_content=log_request_content,
        )

    monkeypatch.setattr(
        server_module.RegexScanner,
        "from_yaml",
        lambda *args, **kwargs: scanner,
    )
    monkeypatch.setattr(server_module, "RequestProcessor", create_processor)
    monkeypatch.setattr(MiddlewareServer, "serve", lambda self, listen: None)

    with caplog.at_level(logging.WARNING, logger="privacy_guard.service.server"):
        result = CliRunner().invoke(
            app,
            [
                "--debug-log-content",
                "regex",
                "--config",
                "scanner-config.yaml",
            ],
        )

    assert result.exit_code == 0
    assert processor_options == [True]
    assert "privacy_guard_request_content_logging_enabled" in caplog.text
    assert "complete_request_bodies_may_contain_secrets" in caplog.text


def test_cli_rejects_builtin_options_before_subcommand() -> None:
    result = CliRunner().invoke(
        app,
        [
            "--profile",
            "customer-support",
            "regex",
        ],
    )

    assert result.exit_code == 2
    assert "--profile" in _plain_output(result)


@pytest.mark.asyncio
async def test_create_server_accepts_injected_servicer_and_serves_loopback_rpcs() -> (
    None
):
    class FalseyMiddleware(PrivacyGuardMiddleware):
        def __bool__(self) -> bool:
            return False

        @override
        async def Describe(
            self,
            request: object,
            context: grpc.aio.ServicerContext[object, pb2.MiddlewareManifest],
        ) -> pb2.MiddlewareManifest:
            manifest = await super().Describe(request, context)
            manifest.name = "injected-falsey-middleware"
            return manifest

    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )
    middleware = FalseyMiddleware(RequestProcessor([scanner]))
    server = create_server(middleware)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port != 0
    await server.start()

    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb2_grpc.SupervisorMiddlewareStub(channel)
            empty_message_type: object = message_factory.GetMessageClass(
                empty_pb2.DESCRIPTOR.message_types_by_name["Empty"]
            )
            if not isinstance(empty_message_type, type):
                raise AssertionError("Empty factory returned a non-type")
            empty_message: object = empty_message_type()
            if not isinstance(empty_message, Message):
                raise AssertionError("Empty factory returned a non-message")
            if (
                empty_message.DESCRIPTOR
                is not empty_pb2.DESCRIPTOR.message_types_by_name["Empty"]
            ):
                raise AssertionError("Empty factory returned the wrong message type")
            manifest = await stub.Describe(empty_message)
            result = await stub.EvaluateHttpRequest(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    body=b'{"message":"user@example.com"}',
                )
            )

        assert manifest.name == "injected-falsey-middleware"
        assert result.decision == pb2.DECISION_ALLOW
        assert result.has_body is True
        assert result.body == b'{"message":"[email]"}'
    finally:
        await server.stop(grace=0)
        await middleware.close()


@pytest.mark.asyncio
async def test_loopback_accepts_body_at_advertised_limit_and_rejects_larger_body() -> (
    None
):
    middleware = _middleware()
    server = create_server(middleware)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port != 0
    await server.start()

    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb2_grpc.SupervisorMiddlewareStub(channel)
            at_limit = b'"' + (b"x" * (MAX_BODY_BYTES - 2)) + b'"'
            allowed = await stub.EvaluateHttpRequest(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    body=at_limit,
                )
            )

            assert allowed.decision == pb2.DECISION_ALLOW

            with pytest.raises(grpc.aio.AioRpcError) as exception_info:
                await stub.EvaluateHttpRequest(
                    pb2.HttpRequestEvaluation(
                        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                        body=at_limit + b" ",
                    )
                )

        assert exception_info.value.code() is grpc.StatusCode.INVALID_ARGUMENT
        details = exception_info.value.details()
        assert details is not None
        assert ErrorCode.REQUEST_BODY_TOO_LARGE.value in details
    finally:
        await server.stop(grace=0)
        await middleware.close()


@pytest.mark.asyncio
async def test_loopback_real_findings_cover_observe_redact_and_block() -> None:
    scanner = DeterministicEmailScanner(
        ScannerConfig(name="test_email", entity_types=frozenset({"email"}))
    )
    middleware = PrivacyGuardMiddleware(RequestProcessor([scanner]))
    server = create_server(middleware)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    body = b'{"first":"a@example.com","second":"b@example.com"}'
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb2_grpc.SupervisorMiddlewareStub(channel)
            observe = await stub.EvaluateHttpRequest(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    config={"on_finding": {"action": "observe"}},
                    body=body,
                )
            )
            redact = await stub.EvaluateHttpRequest(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    config={"on_finding": {"action": "redact"}},
                    body=body,
                )
            )
            block = await stub.EvaluateHttpRequest(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    config={"on_finding": {"action": "block"}},
                    body=body,
                )
            )

        assert observe.decision == pb2.DECISION_ALLOW
        assert not observe.has_body
        assert [(item.type, item.label, item.count) for item in observe.findings] == [
            ("test_email", "email", 2)
        ]
        assert redact.decision == pb2.DECISION_ALLOW
        assert redact.has_body
        assert redact.body == b'{"first":"[email]","second":"[email]"}'
        assert redact.findings[0].count == 2
        assert block.decision == pb2.DECISION_DENY
        assert block.reason_code == "privacy_guard_blocked"
        assert not block.has_body and block.body == b""
        assert block.findings[0].count == 2
    finally:
        await server.stop(grace=0)
        await middleware.close()


@pytest.mark.asyncio
async def test_serve_rejects_bind_failure_and_stops_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BindFailureServer(LifecycleServerFake):
        @override
        def add_insecure_port(self, address: str) -> int:
            return 0

        @override
        async def start(self) -> None:
            raise AssertionError("start must not run after bind failure")

        @override
        async def wait_for_termination(self, timeout: float | None = None) -> bool:
            raise AssertionError("wait must not run after bind failure")

    fake_server = BindFailureServer()
    monkeypatch.setattr(
        server_module,
        "create_server",
        lambda _: fake_server,
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        await serve(_middleware(), "invalid-sensitive-listen-8472")

    assert exception_info.value.code is ErrorCode.SERVER_BIND_FAILED
    assert "Hint:" in str(exception_info.value)
    assert "8472" not in str(exception_info.value)
    assert fake_server.stopped is True


@pytest.mark.asyncio
async def test_serve_translates_runtime_bind_failure_and_stops_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BindFailureServer(LifecycleServerFake):
        @override
        def add_insecure_port(self, address: str) -> int:
            raise RuntimeError("invalid-sensitive-listen-8472")

        @override
        async def start(self) -> None:
            raise AssertionError("start must not run after bind failure")

        @override
        async def wait_for_termination(self, timeout: float | None = None) -> bool:
            raise AssertionError("wait must not run after bind failure")

    fake_server = BindFailureServer()
    monkeypatch.setattr(server_module, "create_server", lambda _: fake_server)

    with pytest.raises(PrivacyGuardError) as exception_info:
        await serve(_middleware(), "invalid-sensitive-listen-8472")

    assert exception_info.value.code is ErrorCode.SERVER_BIND_FAILED
    assert exception_info.value.__cause__ is None
    assert "8472" not in str(exception_info.value)
    assert fake_server.stopped is True


@pytest.mark.asyncio
async def test_startup_failure_stops_server_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StartFailureServer(LifecycleServerFake):
        @override
        def add_insecure_port(self, address: str) -> int:
            return 12345

        @override
        async def start(self) -> None:
            raise RuntimeError("startup failed")

        @override
        async def wait_for_termination(self, timeout: float | None = None) -> bool:
            raise AssertionError("wait must not run after startup failure")

    fake_server = StartFailureServer()
    monkeypatch.setattr(
        server_module,
        "create_server",
        lambda _: fake_server,
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        await serve(_middleware(), "127.0.0.1:12345")

    assert fake_server.stopped is True
