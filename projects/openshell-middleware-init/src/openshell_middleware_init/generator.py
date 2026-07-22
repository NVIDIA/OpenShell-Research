"""Safe, version-matched project generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from openshell_middleware_init import __version__

_REPOSITORY_URL = "https://github.com/NVIDIA/OpenShell"
_RAW_URL = "https://raw.githubusercontent.com/NVIDIA/OpenShell"
_PROTO_PATH = "proto/supervisor_middleware.proto"
_GRPCIO_TOOLS_VERSION = "1.81.1"
_VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(?:[+-][0-9A-Za-z._-]+)?$")
_PYTHON_PACKAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")


class InitializationError(RuntimeError):
    """A user-actionable project initialization failure."""


@dataclass(frozen=True)
class InitializationResult:
    """Details about a successfully generated project."""

    destination: Path
    language: str
    openshell_version: str
    run_command: str


@dataclass(frozen=True)
class TemplateContext:
    """Normalized names used while rendering a project."""

    project_name: str
    distribution_name: str
    package_name: str
    rust_crate_name: str
    service_name: str

    @property
    def replacements(self) -> Mapping[str, str]:
        return {
            "__PROJECT_NAME__": self.project_name,
            "__DISTRIBUTION_NAME__": self.distribution_name,
            "__PACKAGE_NAME__": self.package_name,
            "__RUST_CRATE_NAME__": self.rust_crate_name,
            "__SERVICE_NAME__": self.service_name,
        }


DownloadProto = Callable[[str], tuple[bytes, str]]
CommandRunner = Callable[[str, Path, str], None]


def initialize_project(
    *,
    name: str,
    language: str,
    requested_version: str,
    destination: Path,
    package_name: str | None = None,
    download_proto: DownloadProto | None = None,
    command_runner: CommandRunner | None = None,
) -> InitializationResult:
    """Generate and validate a project, then publish it atomically."""
    context = _template_context(name, language, package_name)
    version = _normalize_version(requested_version)
    destination = destination.expanduser().resolve()
    _validate_destination(destination)
    downloader = download_proto if download_proto is not None else _download_proto
    runner = command_runner if command_runner is not None else _prepare_project

    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.openshell-middleware-init.lock"
    lock_token = secrets.token_hex(16)
    _acquire_lock(lock_path, lock_token, destination, version)
    staging_path: Path | None = None
    try:
        _validate_destination(destination)
        staging_path = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.openshell-middleware-init.",
                dir=destination.parent,
            )
        )
        proto, proto_url = downloader(version)
        _validate_proto(proto, version)
        _render_project(staging_path, language, context)
        proto_path = staging_path / "proto" / "supervisor_middleware.proto"
        proto_path.parent.mkdir(parents=True, exist_ok=True)
        proto_path.write_bytes(proto)
        _write_manifest(
            staging_path,
            version=version,
            proto_url=proto_url,
            proto=proto,
            language=language,
            python_package=context.package_name if language == "python" else None,
        )
        runner(language, staging_path, context.package_name)
        _verify_lock(lock_path, lock_token)
        _validate_destination(destination)
        staging_path.replace(destination)
        staging_path = None
    except InitializationError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise InitializationError(str(error)) from error
    finally:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        _release_lock(lock_path, lock_token)

    return InitializationResult(
        destination=destination,
        language=language,
        openshell_version=version,
        run_command=(
            f"uv run {context.distribution_name}"
            if language == "python"
            else "cargo run -- 127.0.0.1:50051"
        ),
    )


def _template_context(name: str, language: str, package_name: str | None) -> TemplateContext:
    normalized_name = name.strip().lower()
    if not _PROJECT_NAME_PATTERN.fullmatch(normalized_name):
        raise InitializationError(
            "project name must use lowercase letters, digits, dots, hyphens, or underscores"
        )
    if language not in {"python", "rust"}:
        raise InitializationError("language must be 'python' or 'rust'")
    if language == "rust" and package_name is not None:
        raise InitializationError("--package-name is only valid with --language python")

    distribution_name = re.sub(r"[._]+", "-", normalized_name)
    derived_package = re.sub(r"[^a-z0-9]+", "_", normalized_name).strip("_")
    if not derived_package or not derived_package[0].isalpha():
        derived_package = f"middleware_{derived_package}".rstrip("_")
    effective_package = package_name if package_name is not None else derived_package
    if not _PYTHON_PACKAGE_PATTERN.fullmatch(effective_package):
        raise InitializationError(
            "Python package name must start with a lowercase letter and contain only "
            "lowercase letters, digits, and underscores"
        )
    service_name = normalized_name.replace("_", "-").replace(".", "-")
    return TemplateContext(
        project_name=normalized_name,
        distribution_name=distribution_name,
        package_name=effective_package,
        rust_crate_name=distribution_name,
        service_name=service_name,
    )


def _normalize_version(requested: str) -> str:
    value = requested.strip()
    if value == "latest":
        return _resolve_latest_version()
    if not value.startswith("v"):
        value = f"v{value}"
    if not _VERSION_PATTERN.fullmatch(value):
        raise InitializationError(
            f"invalid OpenShell version '{requested}'; expected a tag such as v0.0.86"
        )
    return value


def _resolve_latest_version() -> str:
    request = urllib.request.Request(
        f"{_REPOSITORY_URL}/releases/latest",
        headers={"User-Agent": f"openshell-middleware-init/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            resolved_url = response.geturl()
    except (OSError, urllib.error.URLError) as error:
        raise InitializationError("could not resolve OpenShell's latest release") from error
    prefix = f"{_REPOSITORY_URL}/releases/tag/"
    if not resolved_url.startswith(prefix):
        raise InitializationError(f"unexpected latest-release redirect: {resolved_url}")
    version = resolved_url.removeprefix(prefix)
    if not _VERSION_PATTERN.fullmatch(version):
        raise InitializationError(f"latest release has an unexpected tag: {version}")
    return version


def _download_proto(version: str) -> tuple[bytes, str]:
    url = f"{_RAW_URL}/{version}/{_PROTO_PATH}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"openshell-middleware-init/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(), url
    except (OSError, urllib.error.URLError) as error:
        raise InitializationError(
            f"{version} does not expose {_PROTO_PATH}; choose a middleware-capable release"
        ) from error


def _validate_proto(proto: bytes, version: str) -> None:
    required_fragments = (
        b"package openshell.middleware.v1;",
        b"service SupervisorMiddleware",
        b"rpc EvaluateHttpRequest",
    )
    if not proto or any(fragment not in proto for fragment in required_fragments):
        raise InitializationError(
            f"downloaded contract for {version} is not a supported supervisor middleware proto"
        )


def _validate_destination(destination: Path) -> None:
    if os.path.lexists(destination):
        raise InitializationError(f"output path must not already exist: {destination}")
    if destination.name in {"", ".", ".."}:
        raise InitializationError(f"invalid output path: {destination}")


def _acquire_lock(lock_path: Path, token: str, destination: Path, version: str) -> None:
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise InitializationError(
            f"output path is reserved by another initializer: {destination}; "
            f"inspect {lock_path} before removing a stale reservation"
        ) from error
    try:
        (lock_path / "owner").write_text(token)
        (lock_path / "metadata.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "target_version": version,
                    "final_output": str(destination),
                },
                indent=2,
            )
            + "\n"
        )
    except OSError:
        shutil.rmtree(lock_path, ignore_errors=True)
        raise


def _verify_lock(lock_path: Path, token: str) -> None:
    try:
        recorded = (lock_path / "owner").read_text()
    except OSError as error:
        raise InitializationError("output reservation was lost; refusing to publish") from error
    if recorded != token:
        raise InitializationError("output reservation ownership changed; refusing to publish")


def _release_lock(lock_path: Path, token: str) -> None:
    try:
        if (lock_path / "owner").read_text() != token:
            return
    except OSError:
        return
    shutil.rmtree(lock_path, ignore_errors=True)


def _render_project(destination: Path, language: str, context: TemplateContext) -> None:
    template_root = files("openshell_middleware_init").joinpath("templates").joinpath(language)
    template_paths = {
        "python": (
            ".gitignore",
            "README.md",
            "pyproject.toml",
            "src/package/__init__.py",
            "src/package/server.py",
            "src/package/bindings/__init__.py",
            "tests/test_server.py",
        ),
        "rust": (
            ".gitignore",
            "README.md",
            "Cargo.toml",
            "build.rs",
            "src/lib.rs",
            "src/main.rs",
        ),
    }
    for relative_name in template_paths[language]:
        rendered_name = relative_name.replace("src/package", f"src/{context.package_name}")
        target = destination / rendered_name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = template_root.joinpath(relative_name).read_text()
        for marker, replacement in context.replacements.items():
            content = content.replace(marker, replacement)
        target.write_text(content)


def _write_manifest(
    project_dir: Path,
    *,
    version: str,
    proto_url: str,
    proto: bytes,
    language: str,
    python_package: str | None,
) -> None:
    manifest = {
        "openshell_version": version,
        "proto_source": proto_url,
        "proto_sha256": hashlib.sha256(proto).hexdigest(),
        "languages": [language],
        "python_package": python_package,
        "generator": {
            "name": "openshell-middleware-init",
            "version": __version__,
        },
    }
    (project_dir / "middleware-dev-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def _prepare_project(language: str, project_dir: Path, package_name: str) -> None:
    if language == "python":
        _prepare_python_project(project_dir, package_name)
    else:
        _prepare_rust_project(project_dir)


def _require_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise InitializationError(f"'{command}' is required to initialize this project")
    return resolved


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> None:
    try:
        subprocess.run(
            command,
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise InitializationError(
            f"validation command failed with exit code {error.returncode}: {' '.join(command)}"
        ) from error


def _prepare_python_project(project_dir: Path, package_name: str) -> None:
    uv = _require_command("uv")
    bindings_dir = project_dir / "src" / package_name / "bindings"
    proto_path = project_dir / "proto" / "supervisor_middleware.proto"
    _run(
        (
            uv,
            "run",
            "--isolated",
            "--no-project",
            "--with",
            f"grpcio-tools=={_GRPCIO_TOOLS_VERSION}",
            "python",
            "-m",
            "grpc_tools.protoc",
            f"-I{proto_path.parent}",
            f"--python_out={bindings_dir}",
            f"--pyi_out={bindings_dir}",
            f"--grpc_python_out={bindings_dir}",
            str(proto_path),
        ),
        cwd=project_dir,
    )
    grpc_module = bindings_dir / "supervisor_middleware_pb2_grpc.py"
    generated = grpc_module.read_text()
    absolute_import = "import supervisor_middleware_pb2 as supervisor__middleware__pb2"
    relative_import = "from . import supervisor_middleware_pb2 as supervisor__middleware__pb2"
    if absolute_import not in generated:
        raise InitializationError(
            "generated gRPC module has an unexpected import layout; no project was published"
        )
    grpc_module.write_text(generated.replace(absolute_import, relative_import, 1))

    with tempfile.TemporaryDirectory(prefix="openshell-middleware-init-python-") as environment:
        process_environment = os.environ.copy()
        process_environment.pop("VIRTUAL_ENV", None)
        process_environment["UV_PROJECT_ENVIRONMENT"] = environment
        _run(
            (uv, "sync", "--project", str(project_dir)),
            cwd=project_dir,
            environment=process_environment,
        )
        _run(
            (
                uv,
                "run",
                "--project",
                str(project_dir),
                "python",
                "-c",
                f"from {package_name}.server import Middleware",
            ),
            cwd=project_dir,
            environment=process_environment,
        )


def _prepare_rust_project(project_dir: Path) -> None:
    cargo = _require_command("cargo")
    with tempfile.TemporaryDirectory(prefix="openshell-middleware-init-rust-") as target_dir:
        process_environment = os.environ.copy()
        process_environment["CARGO_TARGET_DIR"] = target_dir
        _run(
            (cargo, "check", "--manifest-path", str(project_dir / "Cargo.toml")),
            cwd=project_dir,
            environment=process_environment,
        )
