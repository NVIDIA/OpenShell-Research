from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from openshell_middleware_init import generator
from openshell_middleware_init.generator import InitializationError, initialize_project

PROTO = b"""syntax = "proto3";
package openshell.middleware.v1;
service SupervisorMiddleware {
  rpc EvaluateHttpRequest(HttpRequestEvaluation) returns (HttpRequestResult);
}
message HttpRequestEvaluation {}
message HttpRequestResult {}
"""


def local_proto(version: str) -> tuple[bytes, str]:
    return PROTO, f"https://example.test/OpenShell/{version}/supervisor_middleware.proto"


def no_op_runner(language: str, project: Path, package: str) -> None:
    assert language in {"python", "rust"}
    assert project.is_dir()
    assert package
    lock_name = "uv.lock" if language == "python" else "Cargo.lock"
    (project / lock_name).write_text("test lock\n")


def test_generates_python_project_with_provenance(tmp_path: Path) -> None:
    destination = tmp_path / "audit-headers"

    result = initialize_project(
        name="audit-headers",
        language="python",
        requested_version="0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert result.destination == destination
    assert result.openshell_version == "v0.0.86"
    assert result.run_command == "uv run audit-headers"
    assert (destination / "src/audit_headers/server.py").is_file()
    assert (destination / "tests/test_server.py").is_file()
    assert (destination / "proto/supervisor_middleware.proto").read_bytes() == PROTO
    assert "__PACKAGE_NAME__" not in (destination / "pyproject.toml").read_text()
    manifest = json.loads((destination / "middleware-dev-manifest.json").read_text())
    assert manifest["openshell_version"] == "v0.0.86"
    assert manifest["languages"] == ["python"]
    assert manifest["python_package"] == "audit_headers"
    assert len(manifest["proto_sha256"]) == 64
    assert not (tmp_path / ".audit-headers.openshell-middleware-init.lock").exists()


def test_generates_rust_project_with_normalized_crate_name(tmp_path: Path) -> None:
    destination = tmp_path / "request.audit"

    result = initialize_project(
        name="request.audit",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert result.run_command == "cargo run -- 127.0.0.1:50051"
    assert 'name = "request-audit"' in (destination / "Cargo.toml").read_text()
    assert "use request_audit::" in (destination / "src/main.rs").read_text()
    manifest = json.loads((destination / "middleware-dev-manifest.json").read_text())
    assert manifest["languages"] == ["rust"]
    assert manifest["python_package"] is None


def test_python_package_name_can_be_overridden(tmp_path: Path) -> None:
    destination = tmp_path / "project"

    initialize_project(
        name="project",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        package_name="custom_package",
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert (destination / "src/custom_package/server.py").is_file()


def test_numeric_project_name_gets_importable_python_package(tmp_path: Path) -> None:
    destination = tmp_path / "123"

    initialize_project(
        name="123",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert (destination / "src/middleware_123/server.py").is_file()


def test_unsupported_platform_fails_before_filesystem_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "output"
    monkeypatch.setattr(generator.sys, "platform", "win32")

    with pytest.raises(InitializationError, match="supports Linux and macOS"):
        initialize_project(
            name="project",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "language", "package_name", "message"),
    [
        ("Bad Name", "python", None, "project name"),
        ("project", "go", None, "language"),
        ("project", "rust", "irrelevant", "only valid"),
        ("project", "python", "bad-package", "Python package name"),
    ],
)
def test_rejects_invalid_project_choices(
    tmp_path: Path,
    name: str,
    language: str,
    package_name: str | None,
    message: str,
) -> None:
    with pytest.raises(InitializationError, match=message):
        initialize_project(
            name=name,
            language=language,
            requested_version="v0.0.86",
            destination=tmp_path / "output",
            package_name=package_name,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


@pytest.mark.parametrize("version", ["", "main", "v1", "v1.2", "v1.2.x"])
def test_rejects_invalid_versions(tmp_path: Path, version: str) -> None:
    with pytest.raises(InitializationError, match="invalid OpenShell version"):
        initialize_project(
            name="project",
            language="python",
            requested_version=version,
            destination=tmp_path / "output",
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_refuses_an_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()

    with pytest.raises(InitializationError, match="must not already exist"):
        initialize_project(
            name="existing",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_refuses_a_reserved_destination(tmp_path: Path) -> None:
    destination = tmp_path / "reserved"
    (tmp_path / ".reserved.openshell-middleware-init.lock").mkdir()

    with pytest.raises(InitializationError, match="reserved by another initializer"):
        initialize_project(
            name="reserved",
            language="rust",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_concurrent_destination_is_not_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "contended"
    publish_no_replace = generator._publish_no_replace

    def collide_before_publish(source: Path, final_output: Path) -> None:
        final_output.mkdir()
        (final_output / "owned-by-other-process").write_text("keep me\n")
        publish_no_replace(source, final_output)

    monkeypatch.setattr(generator, "_publish_no_replace", collide_before_publish)

    with pytest.raises(InitializationError, match="appeared during setup"):
        initialize_project(
            name="contended",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )

    assert (destination / "owned-by-other-process").read_text() == "keep me\n"
    assert not (tmp_path / ".contended.openshell-middleware-init.lock").exists()


def test_failure_cleans_staging_and_owned_reservation(tmp_path: Path) -> None:
    destination = tmp_path / "failing"

    def fail_runner(language: str, project: Path, package: str) -> None:
        del language, project, package
        raise InitializationError("validation failed")

    with pytest.raises(InitializationError, match="validation failed"):
        initialize_project(
            name="failing",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=fail_runner,
        )

    assert not destination.exists()
    assert not (tmp_path / ".failing.openshell-middleware-init.lock").exists()
    assert not list(tmp_path.glob(".failing.openshell-middleware-init.*"))


def test_rejects_an_unexpected_proto(tmp_path: Path) -> None:
    def invalid_proto(version: str) -> tuple[bytes, str]:
        return b"not a proto", f"https://example.test/{version}"

    with pytest.raises(InitializationError, match="not a supported"):
        initialize_project(
            name="invalid-proto",
            language="rust",
            requested_version="v0.0.86",
            destination=tmp_path / "invalid-proto",
            download_proto=invalid_proto,
            command_runner=no_op_runner,
        )


def test_missing_required_command_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator.shutil, "which", lambda command: None)

    with pytest.raises(InitializationError, match="'uv' is required"):
        generator._require_command("uv")


def test_failed_subprocess_is_translated(tmp_path: Path) -> None:
    with pytest.raises(InitializationError, match="validation command failed"):
        generator._run(
            (sys.executable, "-c", "raise SystemExit(7)"),
            cwd=tmp_path,
        )


def test_unexpected_subprocess_error_is_wrapped(tmp_path: Path) -> None:
    def fail_runner(language: str, project: Path, package: str) -> None:
        del language, project, package
        raise subprocess.SubprocessError("tool failed")

    with pytest.raises(InitializationError, match="tool failed"):
        initialize_project(
            name="failure",
            language="rust",
            requested_version="v0.0.86",
            destination=tmp_path / "failure",
            download_proto=local_proto,
            command_runner=fail_runner,
        )


class FakeResponse:
    def __init__(self, *, body: bytes = b"", url: str = "") -> None:
        self.body = body
        self.url = url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exception: object) -> None:
        del exception

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def test_latest_version_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeResponse(url="https://github.com/NVIDIA/OpenShell/releases/tag/v1.2.3")
    monkeypatch.setattr(generator.urllib.request, "urlopen", lambda *args, **kwargs: response)

    assert generator._normalize_version("latest") == "v1.2.3"


@pytest.mark.parametrize(
    ("resolved_url", "message"),
    [
        ("https://example.test/releases/tag/v1.2.3", "unexpected latest-release redirect"),
        ("https://github.com/NVIDIA/OpenShell/releases/tag/nightly", "unexpected tag"),
    ],
)
def test_latest_version_rejects_unexpected_redirects(
    monkeypatch: pytest.MonkeyPatch, resolved_url: str, message: str
) -> None:
    response = FakeResponse(url=resolved_url)
    monkeypatch.setattr(generator.urllib.request, "urlopen", lambda *args, **kwargs: response)

    with pytest.raises(InitializationError, match=message):
        generator._resolve_latest_version()


def test_latest_version_translates_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(generator.urllib.request, "urlopen", fail)

    with pytest.raises(InitializationError, match="could not resolve"):
        generator._resolve_latest_version()


def test_download_proto_returns_content_and_source(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeResponse(body=PROTO)
    monkeypatch.setattr(generator.urllib.request, "urlopen", lambda *args, **kwargs: response)

    content, source = generator._download_proto("v1.2.3")

    assert content == PROTO
    assert source.endswith("/v1.2.3/proto/supervisor_middleware.proto")


def test_download_proto_translates_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise urllib.error.URLError("missing")

    monkeypatch.setattr(generator.urllib.request, "urlopen", fail)

    with pytest.raises(InitializationError, match="middleware-capable release"):
        generator._download_proto("v1.2.3")


def test_lock_verification_detects_loss_and_changed_owner(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    missing = generator.OutputReservation(
        path=lock,
        token="mine",
        directory_fd=-1,
        device=0,
        inode=0,
        destination=tmp_path / "output",
        version="v0.0.86",
        started_at="2026-07-22T00:00:00+00:00",
    )

    with pytest.raises(InitializationError, match="reservation was lost"):
        generator._verify_lock(missing)

    reservation = generator._acquire_lock(lock, "mine", tmp_path / "output", "v0.0.86")
    (lock / "owner").write_text("theirs")
    with pytest.raises(InitializationError, match="ownership changed"):
        generator._verify_lock(reservation)

    generator._release_lock(reservation)
    assert lock.exists()


def test_reservation_metadata_supports_safe_recovery(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    destination = tmp_path / "output"
    staging = tmp_path / ".output.staging"
    reservation = generator._acquire_lock(lock, "mine", destination, "v0.0.86")

    generator._write_reservation_metadata(reservation, staging)

    metadata = json.loads((lock / "metadata.json").read_text())
    assert metadata["pid"] == os.getpid()
    assert metadata["host"]
    assert metadata["started_at"]
    assert metadata["final_output"] == str(destination)
    assert metadata["staging_output"] == str(staging)
    generator._release_lock(reservation)


def test_reservation_cleanup_leaves_unrecognized_contents(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    reservation = generator._acquire_lock(lock, "mine", tmp_path / "output", "v0.0.86")
    (lock / "unexpected").write_text("not ours\n")

    generator._release_lock(reservation)

    assert lock.is_dir()
    assert (lock / "unexpected").read_text() == "not ours\n"
    assert (lock / "owner").read_text() == "mine"
    assert (lock / "metadata.json").is_file()


def test_reservation_verification_rejects_changed_directory_identity(tmp_path: Path) -> None:
    reservation = generator._acquire_lock(tmp_path / "lock", "mine", tmp_path / "output", "v0.0.86")
    changed_identity = generator.OutputReservation(
        path=reservation.path,
        token=reservation.token,
        directory_fd=reservation.directory_fd,
        device=reservation.device,
        inode=reservation.inode + 1,
        destination=reservation.destination,
        version=reservation.version,
        started_at=reservation.started_at,
    )

    with pytest.raises(InitializationError, match="reservation was lost"):
        generator._verify_lock(changed_identity)
    generator._cleanup_reservation(reservation)


def test_reservation_verification_rejects_non_file_owner(tmp_path: Path) -> None:
    reservation = generator._acquire_lock(tmp_path / "lock", "mine", tmp_path / "output", "v0.0.86")
    (reservation.path / "owner").unlink()
    (reservation.path / "owner").mkdir()

    with pytest.raises(InitializationError, match="reservation was lost"):
        generator._verify_lock(reservation)
    generator._release_lock(reservation)


def test_reservation_cleanup_does_not_touch_path_swapped_after_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = tmp_path / "lock"
    moved_lock = tmp_path / "moved-lock"
    reservation = generator._acquire_lock(lock, "mine", tmp_path / "output", "v0.0.86")
    verify_lock = generator._verify_lock

    def verify_then_swap(current: generator.OutputReservation) -> None:
        verify_lock(current)
        lock.rename(moved_lock)
        lock.mkdir()
        (lock / "owner").write_text("replacement owner\n")
        (lock / "metadata.json").write_text("replacement metadata\n")

    monkeypatch.setattr(generator, "_verify_lock", verify_then_swap)

    generator._release_lock(reservation)

    assert (lock / "owner").read_text() == "replacement owner\n"
    assert (lock / "metadata.json").read_text() == "replacement metadata\n"
    assert moved_lock.is_dir()
    assert list(moved_lock.iterdir()) == []


def test_acquisition_failure_removes_only_known_reservation_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = tmp_path / "lock"

    def fail_metadata(reservation: generator.OutputReservation, staging_path: Path | None) -> None:
        del reservation, staging_path
        raise OSError("metadata failed")

    monkeypatch.setattr(generator, "_write_reservation_metadata", fail_metadata)

    with pytest.raises(OSError, match="metadata failed"):
        generator._acquire_lock(lock, "mine", tmp_path / "output", "v0.0.86")

    assert not lock.exists()


def test_publish_no_replace_moves_into_absent_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "generated").write_text("ready\n")

    generator._publish_no_replace(source, destination)

    assert not source.exists()
    assert (destination / "generated").read_text() == "ready\n"


class FakeRename:
    def __init__(self, error_number: int) -> None:
        self.error_number = error_number
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *arguments: object) -> int:
        del arguments
        ctypes.set_errno(self.error_number)
        return -1


class FakeLibrary:
    def __init__(self, error_number: int) -> None:
        self.renameat2 = FakeRename(error_number)


@pytest.mark.parametrize("error_number", [errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP])
def test_publish_reports_filesystem_without_no_replace_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error_number: int,
) -> None:
    monkeypatch.setattr(generator.ctypes, "CDLL", lambda *args, **kwargs: FakeLibrary(error_number))

    with pytest.raises(InitializationError, match="does not support"):
        generator._publish_no_replace(tmp_path / "source", tmp_path / "destination")


def test_publish_translates_unexpected_rename_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(generator.ctypes, "CDLL", lambda *args, **kwargs: FakeLibrary(errno.EPERM))

    with pytest.raises(OSError) as exception_info:
        generator._publish_no_replace(tmp_path / "source", tmp_path / "destination")

    assert exception_info.value.errno == errno.EPERM


def test_prepare_project_dispatches_by_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, Path, str | None]] = []
    monkeypatch.setattr(
        generator,
        "_prepare_python_project",
        lambda path, package: calls.append(("python", path, package)),
    )
    monkeypatch.setattr(
        generator,
        "_prepare_rust_project",
        lambda path: calls.append(("rust", path, None)),
    )

    generator._prepare_project("python", tmp_path, "my_package")
    generator._prepare_project("rust", tmp_path, "ignored")

    assert calls == [
        ("python", tmp_path, "my_package"),
        ("rust", tmp_path, None),
    ]


def test_require_command_returns_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator.shutil, "which", lambda command: f"/tools/{command}")

    assert generator._require_command("cargo") == "/tools/cargo"


def test_run_passes_environment_to_subprocess(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["MIDDLEWARE_INIT_TEST_VALUE"] = "present"

    generator._run(
        (
            sys.executable,
            "-c",
            "import os; assert os.environ['MIDDLEWARE_INIT_TEST_VALUE'] == 'present'",
        ),
        cwd=tmp_path,
        environment=environment,
    )


def test_prepare_python_generates_relative_import_and_smoke_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bindings = tmp_path / "src" / "audit_headers" / "bindings"
    bindings.mkdir(parents=True)
    (tmp_path / "proto").mkdir()
    (tmp_path / "proto/supervisor_middleware.proto").write_bytes(PROTO)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(generator, "_require_command", lambda command: f"/tools/{command}")

    def fake_run(command, *, cwd, environment=None) -> None:
        del cwd, environment
        calls.append(tuple(command))
        if "grpc_tools.protoc" in command:
            (bindings / "supervisor_middleware_pb2_grpc.py").write_text(
                "import supervisor_middleware_pb2 as supervisor__middleware__pb2\n"
            )

    monkeypatch.setattr(generator, "_run", fake_run)

    generator._prepare_python_project(tmp_path, "audit_headers")

    generated = (bindings / "supervisor_middleware_pb2_grpc.py").read_text()
    assert generated.startswith("from . import supervisor_middleware_pb2")
    assert len(calls) == 3
    assert calls[1][1] == "sync"


def test_prepare_python_rejects_unexpected_generated_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bindings = tmp_path / "src" / "audit" / "bindings"
    bindings.mkdir(parents=True)
    (tmp_path / "proto").mkdir()
    (tmp_path / "proto/supervisor_middleware.proto").write_bytes(PROTO)
    monkeypatch.setattr(generator, "_require_command", lambda command: f"/tools/{command}")

    def fake_run(command, *, cwd, environment=None) -> None:
        del command, cwd, environment
        (bindings / "supervisor_middleware_pb2_grpc.py").write_text("unexpected\n")

    monkeypatch.setattr(generator, "_run", fake_run)

    with pytest.raises(InitializationError, match="unexpected import layout"):
        generator._prepare_python_project(tmp_path, "audit")


def test_prepare_rust_runs_cargo_with_temporary_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(generator, "_require_command", lambda command: f"/tools/{command}")

    def fake_run(command, *, cwd, environment=None) -> None:
        observed.update(command=command, cwd=cwd, environment=environment)

    monkeypatch.setattr(generator, "_run", fake_run)

    generator._prepare_rust_project(tmp_path)

    assert observed["command"] == (
        "/tools/cargo",
        "check",
        "--manifest-path",
        str(tmp_path / "Cargo.toml"),
    )
    assert observed["cwd"] == tmp_path
    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert "CARGO_TARGET_DIR" in environment
