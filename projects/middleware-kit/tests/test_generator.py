from __future__ import annotations

import ctypes
import email.message
import errno
import http.client
import json
import os
import stat
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from middleware_kit import generator
from middleware_kit.generator import ProjectError, create_project, update_project

PROTO = b"""syntax = "proto3";
package openshell.middleware.v1;
service SupervisorMiddleware {
  rpc EvaluateHttpRequest(HttpRequestEvaluation) returns (HttpRequestResult);
}
message HttpRequestEvaluation {}
message HttpRequestResult {}
"""
UPDATED_PROTO = PROTO + b"// refreshed contract\n"


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

    result = create_project(
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
    assert not (tmp_path / ".audit-headers.middleware-kit.lock").exists()


def test_generates_rust_project_with_normalized_crate_name(tmp_path: Path) -> None:
    destination = tmp_path / "request.audit"

    result = create_project(
        name="request.audit",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert result.run_command == "cargo run -- 127.0.0.1:50051"
    cargo = (destination / "Cargo.toml").read_text()
    assert 'name = "request-audit"' in cargo
    assert '[lib]\nname = "request_audit"' in cargo
    assert (
        ".add_service(request_audit::middleware_service())"
        in (destination / "src/main.rs").read_text()
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755
    manifest = json.loads((destination / "middleware-dev-manifest.json").read_text())
    assert manifest["languages"] == ["rust"]
    assert manifest["python_package"] is None


def test_updates_python_generated_artifacts_and_preserves_user_code(tmp_path: Path) -> None:
    destination = tmp_path / "audit-headers"
    create_project(
        name="audit-headers",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    server_path = destination / "src/audit_headers/server.py"
    server_path.write_text(server_path.read_text() + "\n# user policy\n")
    old_binding = destination / "src/audit_headers/bindings/old_generated.py"
    old_binding.write_text("old generated code\n")
    project_inode = destination.stat().st_ino

    def updated_proto(version: str) -> tuple[bytes, str]:
        return UPDATED_PROTO, f"https://example.test/OpenShell/{version}/proto"

    result = update_project(
        project_dir=destination,
        requested_version="1.2.3",
        download_proto=updated_proto,
        command_runner=no_op_runner,
    )

    assert result.destination == destination
    assert result.language == "python"
    assert result.openshell_version == "v1.2.3"
    assert destination.stat().st_ino == project_inode
    assert server_path.read_text().endswith("# user policy\n")
    assert (destination / "proto/supervisor_middleware.proto").read_bytes() == UPDATED_PROTO
    assert not old_binding.exists()
    assert (destination / "src/audit_headers/bindings/__init__.py").is_file()
    manifest = json.loads((destination / "middleware-dev-manifest.json").read_text())
    assert manifest["openshell_version"] == "v1.2.3"
    assert manifest["generator"]["name"] == "middleware-kit"
    assert not (tmp_path / ".audit-headers.middleware-kit.lock").exists()
    assert not list(tmp_path.glob(".audit-headers.middleware-kit.*"))


def test_failed_update_keeps_original_project_unchanged(tmp_path: Path) -> None:
    destination = tmp_path / "audit"
    create_project(
        name="audit",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    original_manifest = (destination / "middleware-dev-manifest.json").read_bytes()
    original_proto = (destination / "proto/supervisor_middleware.proto").read_bytes()

    def fail_runner(language: str, project: Path, package: str) -> None:
        del language, project, package
        raise ProjectError("validation failed")

    with pytest.raises(ProjectError, match="validation failed"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=fail_runner,
        )

    assert (destination / "middleware-dev-manifest.json").read_bytes() == original_manifest
    assert (destination / "proto/supervisor_middleware.proto").read_bytes() == original_proto
    assert not (tmp_path / ".audit.middleware-kit.lock").exists()
    assert not list(tmp_path.glob(".audit.middleware-kit.*"))


def test_publication_failure_rolls_back_exchanged_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "audit"
    create_project(
        name="audit",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    original_manifest = (destination / "middleware-dev-manifest.json").read_bytes()
    original_proto = (destination / "proto/supervisor_middleware.proto").read_bytes()
    original_exchange = generator._publish_exchange
    calls = 0

    def fail_second_exchange(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProjectError("publication failed")
        original_exchange(source, target)

    monkeypatch.setattr(generator, "_publish_exchange", fail_second_exchange)

    with pytest.raises(ProjectError, match="publication failed"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=no_op_runner,
        )

    assert calls == 3
    assert (destination / "middleware-dev-manifest.json").read_bytes() == original_manifest
    assert (destination / "proto/supervisor_middleware.proto").read_bytes() == original_proto


def test_failed_publication_rollback_preserves_recovery_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "audit"
    create_project(
        name="audit",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    original_exchange = generator._publish_exchange
    calls = 0

    def fail_publication_and_rollback(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ProjectError("exchange failed")
        original_exchange(source, target)

    monkeypatch.setattr(generator, "_publish_exchange", fail_publication_and_rollback)

    with pytest.raises(generator.PublicationRollbackError, match="rollback both failed"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=no_op_runner,
        )

    assert calls == 3
    assert (tmp_path / ".audit.middleware-kit.lock").is_dir()
    assert len(list(tmp_path.glob(".audit.middleware-kit.*"))) == 2


def test_update_rejects_non_generated_project(tmp_path: Path) -> None:
    destination = tmp_path / "not-generated"
    destination.mkdir()

    with pytest.raises(ProjectError, match="missing regular manifest"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_update_rejects_symlink_and_missing_project_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    symlink = tmp_path / "symlink"
    symlink.symlink_to(missing, target_is_directory=True)

    with pytest.raises(ProjectError, match="must not be a symlink"):
        update_project(
            project_dir=symlink,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )
    with pytest.raises(ProjectError, match="existing directory"):
        update_project(
            project_dir=missing,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("not json", "could not read"),
        ("[]", "JSON object"),
        ('{"generator": {"name": "other"}}', "generator must be middleware-kit"),
        (
            '{"generator": {"name": "middleware-kit"}, "languages": ["python", "rust"]}',
            "exactly one",
        ),
        (
            '{"generator": {"name": "middleware-kit"}, '
            '"languages": ["python"], "python_package": "Bad-Package"}',
            "invalid or missing",
        ),
        (
            '{"generator": {"name": "middleware-kit"}, '
            '"languages": ["rust"], "python_package": "unexpected"}',
            "must set python_package",
        ),
    ],
)
def test_update_rejects_invalid_manifest(tmp_path: Path, manifest: str, message: str) -> None:
    destination = tmp_path / "project"
    destination.mkdir()
    (destination / "middleware-dev-manifest.json").write_text(manifest)

    with pytest.raises(ProjectError, match=message):
        update_project(
            project_dir=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_update_requires_regular_generated_artifacts(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    destination.mkdir()
    (destination / "middleware-dev-manifest.json").write_text(
        json.dumps(
            {
                "generator": {"name": "middleware-kit"},
                "languages": ["rust"],
                "python_package": None,
            }
        )
    )

    with pytest.raises(ProjectError, match="regular file"):
        update_project(
            project_dir=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_update_requires_python_bindings_directory(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    (destination / "proto").mkdir(parents=True)
    (destination / "proto/supervisor_middleware.proto").write_bytes(PROTO)
    (destination / "uv.lock").write_text("test lock\n")
    (destination / "middleware-dev-manifest.json").write_text(
        json.dumps(
            {
                "generator": {"name": "middleware-kit"},
                "languages": ["python"],
                "python_package": "audit",
            }
        )
    )

    with pytest.raises(ProjectError, match="bindings must be"):
        update_project(
            project_dir=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_update_rejects_symlinked_proto_directory_without_touching_target(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "project"
    create_project(
        name="project",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    (destination / "proto").rename(destination / "original-proto")
    external_proto = tmp_path / "external-proto"
    external_proto.mkdir()
    sentinel = external_proto / "supervisor_middleware.proto"
    sentinel.write_text("external contract\n")
    (destination / "proto").symlink_to(external_proto, target_is_directory=True)

    with pytest.raises(ProjectError, match="must not contain symlinks"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=no_op_runner,
        )

    assert sentinel.read_text() == "external contract\n"


def test_update_rejects_symlinked_python_package_without_touching_target(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "project"
    create_project(
        name="project",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    package_dir = destination / "src/project"
    package_dir.rename(destination / "original-package")
    external_package = tmp_path / "external-package"
    bindings = external_package / "bindings"
    bindings.mkdir(parents=True)
    sentinel = bindings / "keep.txt"
    sentinel.write_text("external binding\n")
    package_dir.symlink_to(external_package, target_is_directory=True)

    with pytest.raises(ProjectError, match="must not contain symlinks"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=no_op_runner,
        )

    assert sentinel.read_text() == "external binding\n"


def test_update_omits_disposable_directories_from_staging(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    create_project(
        name="project",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    for directory_name in (".git", ".venv", ".pytest_cache", "dist", "target"):
        disposable = destination / directory_name
        disposable.mkdir()
        (disposable / "large-cache").write_text("not needed\n")
    nested_cache = destination / "src/project/__pycache__"
    nested_cache.mkdir()
    (nested_cache / "server.pyc").write_bytes(b"cache")
    user_file = destination / "policy-notes.txt"
    user_file.write_text("preserve me\n")

    def inspect_staging(language: str, project: Path, package: str) -> None:
        assert language == "python"
        assert package == "project"
        assert user_file.name in {path.name for path in project.iterdir()}
        for directory_name in (".git", ".venv", ".pytest_cache", "dist", "target"):
            assert not (project / directory_name).exists()
        assert not (project / "src/project/__pycache__").exists()
        no_op_runner(language, project, package)

    update_project(
        project_dir=destination,
        requested_version="v1.2.3",
        download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
        command_runner=inspect_staging,
    )

    assert user_file.read_text() == "preserve me\n"
    assert (destination / ".venv/large-cache").read_text() == "not needed\n"


@pytest.mark.parametrize("replace_live_path", [False, True])
def test_update_revalidates_symlink_ancestors_after_project_validation(
    tmp_path: Path, replace_live_path: bool
) -> None:
    destination = tmp_path / "project"
    create_project(
        name="project",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    original_manifest = (destination / "middleware-dev-manifest.json").read_bytes()
    external_proto = tmp_path / "external-proto"
    external_proto.mkdir()
    sentinel = external_proto / "supervisor_middleware.proto"
    sentinel.write_text("external contract\n")

    def replace_proto_ancestor(language: str, staged_project: Path, package: str) -> None:
        no_op_runner(language, staged_project, package)
        project_to_change = destination if replace_live_path else staged_project
        (project_to_change / "proto").rename(project_to_change / "original-proto")
        (project_to_change / "proto").symlink_to(external_proto, target_is_directory=True)

    with pytest.raises(ProjectError, match="must not contain symlinks"):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=replace_proto_ancestor,
        )

    assert sentinel.read_text() == "external contract\n"
    assert (destination / "middleware-dev-manifest.json").read_bytes() == original_manifest


def test_exchange_refuses_symlinked_parent_created_immediately_before_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "project"
    create_project(
        name="project",
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )
    external_proto = tmp_path / "external-proto"
    external_proto.mkdir()
    sentinel = external_proto / "supervisor_middleware.proto"
    sentinel.write_text("external contract\n")
    original_exchange = generator._publish_exchange
    first_exchange = True

    def replace_parent_then_exchange(source: Path, target: Path) -> None:
        nonlocal first_exchange
        if first_exchange:
            first_exchange = False
            target.parent.rename(destination / "original-proto")
            target.parent.symlink_to(external_proto, target_is_directory=True)
        original_exchange(source, target)

    monkeypatch.setattr(generator, "_publish_exchange", replace_parent_then_exchange)

    with pytest.raises(ProjectError):
        update_project(
            project_dir=destination,
            requested_version="v1.2.3",
            download_proto=lambda version: (UPDATED_PROTO, f"https://example.test/{version}"),
            command_runner=no_op_runner,
        )

    assert sentinel.read_text() == "external contract\n"


def test_update_detects_replaced_project_before_publication(tmp_path: Path) -> None:
    project = tmp_path / "project"
    replacement = tmp_path / "replacement"
    project.mkdir()
    original = project.stat()
    project.rename(replacement)
    project.mkdir()

    with pytest.raises(ProjectError, match="changed during update"):
        generator._verify_project_identity(project, original.st_dev, original.st_ino)

    project.rmdir()
    with pytest.raises(ProjectError, match="changed during update"):
        generator._verify_project_identity(project, original.st_dev, original.st_ino)


def test_python_package_name_can_be_overridden(tmp_path: Path) -> None:
    destination = tmp_path / "project"

    create_project(
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

    create_project(
        name="123",
        language="python",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    assert (destination / "src/middleware_123/server.py").is_file()


@pytest.mark.parametrize(
    ("name", "crate", "library"),
    [
        ("123", "middleware-123", "middleware_123"),
        ("type", "middleware-type", "middleware_type"),
        ("std", "middleware-std", "middleware_std"),
        ("build", "middleware-build", "middleware_build"),
    ],
)
def test_rust_project_names_get_valid_explicit_library_names(
    tmp_path: Path, name: str, crate: str, library: str
) -> None:
    destination = tmp_path / name

    create_project(
        name=name,
        language="rust",
        requested_version="v0.0.86",
        destination=destination,
        download_proto=local_proto,
        command_runner=no_op_runner,
    )

    cargo = (destination / "Cargo.toml").read_text()
    assert f'name = "{crate}"' in cargo
    assert f'[lib]\nname = "{library}"' in cargo
    assert (
        f".add_service({library}::middleware_service())"
        in (destination / "src/main.rs").read_text()
    )


def test_unsupported_platform_fails_before_filesystem_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "output"
    monkeypatch.setattr(generator.sys, "platform", "win32")

    with pytest.raises(ProjectError, match="supports Linux and macOS"):
        create_project(
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
    with pytest.raises(ProjectError, match=message):
        create_project(
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
    with pytest.raises(ProjectError, match="invalid OpenShell version"):
        create_project(
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

    with pytest.raises(ProjectError, match="must not already exist"):
        create_project(
            name="existing",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )


def test_refuses_a_dangling_destination_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "requested"
    target = tmp_path / "symlink-target"
    destination.symlink_to(target, target_is_directory=True)

    with pytest.raises(ProjectError, match="must not already exist"):
        create_project(
            name="project",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )

    assert destination.is_symlink()
    assert not target.exists()


def test_refuses_a_reserved_destination(tmp_path: Path) -> None:
    destination = tmp_path / "reserved"
    (tmp_path / ".reserved.middleware-kit.lock").mkdir()

    with pytest.raises(ProjectError, match="reserved by another middleware-kit"):
        create_project(
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

    with pytest.raises(ProjectError, match="appeared during setup"):
        create_project(
            name="contended",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=no_op_runner,
        )

    assert (destination / "owned-by-other-process").read_text() == "keep me\n"
    assert not (tmp_path / ".contended.middleware-kit.lock").exists()


def test_failure_cleans_staging_and_owned_reservation(tmp_path: Path) -> None:
    destination = tmp_path / "failing"

    def fail_runner(language: str, project: Path, package: str) -> None:
        del language, project, package
        raise ProjectError("validation failed")

    with pytest.raises(ProjectError, match="validation failed"):
        create_project(
            name="failing",
            language="python",
            requested_version="v0.0.86",
            destination=destination,
            download_proto=local_proto,
            command_runner=fail_runner,
        )

    assert not destination.exists()
    assert not (tmp_path / ".failing.middleware-kit.lock").exists()
    assert not list(tmp_path.glob(".failing.middleware-kit.*"))


def test_rejects_an_unexpected_proto(tmp_path: Path) -> None:
    def invalid_proto(version: str) -> tuple[bytes, str]:
        return b"not a proto", f"https://example.test/{version}"

    with pytest.raises(ProjectError, match="not a supported"):
        create_project(
            name="invalid-proto",
            language="rust",
            requested_version="v0.0.86",
            destination=tmp_path / "invalid-proto",
            download_proto=invalid_proto,
            command_runner=no_op_runner,
        )


def test_missing_required_command_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator.shutil, "which", lambda command: None)

    with pytest.raises(ProjectError, match="'uv' is required"):
        generator._require_command("uv")


def test_failed_subprocess_is_translated(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="validation command failed"):
        generator._run(
            (sys.executable, "-c", "raise SystemExit(7)"),
            cwd=tmp_path,
        )


def test_unexpected_subprocess_error_is_wrapped(tmp_path: Path) -> None:
    def fail_runner(language: str, project: Path, package: str) -> None:
        del language, project, package
        raise subprocess.SubprocessError("tool failed")

    with pytest.raises(ProjectError, match="tool failed"):
        create_project(
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

    with pytest.raises(ProjectError, match=message):
        generator._resolve_latest_version()


def test_latest_version_translates_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def fail(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(generator.urllib.request, "urlopen", fail)
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    with pytest.raises(ProjectError, match="could not resolve"):
        generator._resolve_latest_version()
    assert attempts == 4


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
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    with pytest.raises(ProjectError, match=r"could not download.*missing"):
        generator._download_proto("v1.2.3")


def test_network_error_reason_handles_plain_os_error() -> None:
    assert generator._network_error_reason(OSError("offline")) == "offline"


def test_download_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    response = FakeResponse(body=PROTO)

    def transient(*args: object, **kwargs: object) -> FakeResponse:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary")
        return response

    monkeypatch.setattr(generator.urllib.request, "urlopen", transient)
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    assert generator._download_proto("v1.2.3")[0] == PROTO
    assert attempts == 3


def test_download_retries_interrupted_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class InterruptedResponse(FakeResponse):
        def read(self) -> bytes:
            raise http.client.IncompleteRead(b"partial")

    def interrupted_then_complete(*args: object, **kwargs: object) -> FakeResponse:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        return InterruptedResponse() if attempts == 1 else FakeResponse(body=PROTO)

    monkeypatch.setattr(generator.urllib.request, "urlopen", interrupted_then_complete)
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    assert generator._download_proto("v1.2.3")[0] == PROTO
    assert attempts == 2


@pytest.mark.parametrize(("status", "expected_attempts"), [(503, 4), (404, 1)])
def test_download_retries_only_retryable_http_statuses(
    monkeypatch: pytest.MonkeyPatch, status: int, expected_attempts: int
) -> None:
    attempts = 0

    def fail(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise urllib.error.HTTPError(
            "https://example.test/proto",
            status,
            "failure",
            hdrs=email.message.Message(),
            fp=None,
        )

    monkeypatch.setattr(generator.urllib.request, "urlopen", fail)
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    expected_message = "middleware-capable release" if status == 404 else "HTTP 503"
    with pytest.raises(ProjectError, match=expected_message):
        generator._download_proto("v1.2.3")

    assert attempts == expected_attempts


@pytest.mark.parametrize(("language", "command"), [("python", "uv"), ("rust", "cargo")])
def test_toolchain_preflight_precedes_latest_resolution_and_output_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, command: str
) -> None:
    destination = tmp_path / "generated"
    monkeypatch.setattr(generator.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        generator,
        "_resolve_latest_version",
        lambda: pytest.fail("latest must not be resolved before toolchain preflight"),
    )

    with pytest.raises(ProjectError, match=rf"'{command}' is required"):
        generator.create_project(
            name="audit-headers",
            language=language,
            requested_version="latest",
            destination=destination,
            download_proto=local_proto,
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_lock_verification_detects_loss_and_changed_owner(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    missing = generator.OutputReservation(
        path=lock,
        token="mine",
        directory_fd=-1,
        device=0,
        inode=0,
        destination=tmp_path / "output",
        staging_path=tmp_path / ".output.staging",
        version="v0.0.86",
        started_at="2026-07-22T00:00:00+00:00",
    )

    with pytest.raises(ProjectError, match="reservation was lost"):
        generator._verify_lock(missing)

    reservation = generator._acquire_lock(lock, "mine", tmp_path / "output", "v0.0.86")
    (lock / "owner").write_text("theirs")
    with pytest.raises(ProjectError, match="ownership changed"):
        generator._verify_lock(reservation)

    generator._release_lock(reservation)
    assert lock.exists()


def test_reservation_metadata_supports_safe_recovery(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    destination = tmp_path / "output"
    reservation = generator._acquire_lock(lock, "mine", destination, "v0.0.86")

    metadata = json.loads((lock / "metadata.json").read_text())
    assert metadata["pid"] == os.getpid()
    assert metadata["host"]
    assert metadata["started_at"]
    assert metadata["final_output"] == str(destination)
    assert metadata["staging_output"] == str(reservation.staging_path)
    assert reservation.token in reservation.staging_path.name
    assert not reservation.staging_path.exists()
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
        staging_path=reservation.staging_path,
        version=reservation.version,
        started_at=reservation.started_at,
    )

    with pytest.raises(ProjectError, match="reservation was lost"):
        generator._verify_lock(changed_identity)
    generator._cleanup_reservation(reservation)


def test_reservation_verification_rejects_non_file_owner(tmp_path: Path) -> None:
    reservation = generator._acquire_lock(tmp_path / "lock", "mine", tmp_path / "output", "v0.0.86")
    (reservation.path / "owner").unlink()
    (reservation.path / "owner").mkdir()

    with pytest.raises(ProjectError, match="reservation was lost"):
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

    def fail_metadata(reservation: generator.OutputReservation) -> None:
        del reservation
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


def test_exchange_reverses_when_open_parent_is_detached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_parent = tmp_path / "staged/proto"
    destination_parent = tmp_path / "project/proto"
    source_parent.mkdir(parents=True)
    destination_parent.mkdir(parents=True)
    source = source_parent / "supervisor_middleware.proto"
    destination = destination_parent / "supervisor_middleware.proto"
    source.write_text("new contract\n")
    destination.write_text("old contract\n")
    moved_destination_parent = tmp_path / "project/moved-proto"
    validate_exchange_entries = generator._validate_exchange_entries

    def validate_then_detach(*arguments) -> None:
        validate_exchange_entries(*arguments)
        destination_parent.rename(moved_destination_parent)
        destination_parent.mkdir()
        (destination_parent / destination.name).write_text("concurrent replacement\n")

    monkeypatch.setattr(generator, "_validate_exchange_entries", validate_then_detach)

    with pytest.raises(ProjectError, match=r"parent changed.*exchange was reversed"):
        generator._publish_exchange(source, destination)

    assert source.read_text() == "new contract\n"
    assert (moved_destination_parent / destination.name).read_text() == "old contract\n"
    assert destination.read_text() == "concurrent replacement\n"


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

    with pytest.raises(ProjectError, match="does not support"):
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
    environment["MIDDLEWARE_KIT_TEST_VALUE"] = "present"

    generator._run(
        (
            sys.executable,
            "-c",
            "import os; assert os.environ['MIDDLEWARE_KIT_TEST_VALUE'] == 'present'",
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
    assert calls[2][-1] == "pytest"


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

    with pytest.raises(ProjectError, match="unexpected import layout"):
        generator._prepare_python_project(tmp_path, "audit")


def test_prepare_rust_runs_cargo_with_temporary_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
    monkeypatch.setattr(generator, "_require_command", lambda command: f"/tools/{command}")

    def fake_run(command, *, cwd, environment=None) -> None:
        assert environment is not None
        observed.append((tuple(command), cwd, environment))

    monkeypatch.setattr(generator, "_run", fake_run)

    generator._prepare_rust_project(tmp_path)

    assert [command for command, _, _ in observed] == [
        (
            "/tools/cargo",
            "test",
            "--manifest-path",
            str(tmp_path / "Cargo.toml"),
        ),
    ]
    assert all(cwd == tmp_path for _, cwd, _ in observed)
    assert all("CARGO_TARGET_DIR" in environment for _, _, environment in observed)
