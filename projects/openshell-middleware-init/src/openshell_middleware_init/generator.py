"""Safe, version-matched project generation."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
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
_NETWORK_ATTEMPTS = 4
_RUST_KEYWORDS = {
    "abstract",
    "as",
    "async",
    "await",
    "become",
    "box",
    "break",
    "const",
    "continue",
    "crate",
    "do",
    "dyn",
    "else",
    "enum",
    "extern",
    "false",
    "final",
    "fn",
    "for",
    "gen",
    "if",
    "impl",
    "in",
    "let",
    "loop",
    "macro",
    "match",
    "mod",
    "move",
    "mut",
    "override",
    "priv",
    "pub",
    "ref",
    "return",
    "self",
    "Self",
    "static",
    "struct",
    "super",
    "trait",
    "true",
    "try",
    "type",
    "typeof",
    "union",
    "unsized",
    "unsafe",
    "use",
    "virtual",
    "where",
    "while",
    "yield",
}
_RUST_RESERVED_IDENTIFIERS = _RUST_KEYWORDS | {
    "alloc",
    "build",
    "core",
    "deps",
    "examples",
    "incremental",
    "proc_macro",
    "std",
    "test",
}


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
class OutputReservation:
    """Identity and recovery data for an output-path reservation."""

    path: Path
    token: str
    directory_fd: int
    device: int
    inode: int
    destination: Path
    staging_path: Path
    version: str
    started_at: str


@dataclass(frozen=True)
class TemplateContext:
    """Normalized names used while rendering a project."""

    project_name: str
    distribution_name: str
    package_name: str
    rust_crate_name: str
    rust_lib_name: str
    service_name: str

    @property
    def replacements(self) -> Mapping[str, str]:
        return {
            "__PROJECT_NAME__": self.project_name,
            "__DISTRIBUTION_NAME__": self.distribution_name,
            "__PACKAGE_NAME__": self.package_name,
            "__RUST_CRATE_NAME__": self.rust_crate_name,
            "__RUST_LIB_NAME__": self.rust_lib_name,
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
    _validate_platform()
    context = _template_context(name, language, package_name)
    if command_runner is None:
        _preflight_language(language)
    version = _normalize_version(requested_version)
    destination = destination.expanduser()
    _validate_destination(destination)
    destination = destination.parent.resolve() / destination.name
    _validate_destination(destination)
    downloader = download_proto if download_proto is not None else _download_proto
    runner = command_runner if command_runner is not None else _prepare_project

    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.openshell-middleware-init.lock"
    lock_token = secrets.token_hex(16)
    reservation = _acquire_lock(lock_path, lock_token, destination, version)
    staging_path: Path | None = None
    try:
        _validate_destination(destination)
        reservation.staging_path.mkdir(mode=0o700)
        staging_path = reservation.staging_path
        staging_path.chmod(0o755)
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
        _verify_lock(reservation)
        _publish_no_replace(staging_path, destination)
        staging_path = None
    except InitializationError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise InitializationError(str(error)) from error
    finally:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        _release_lock(reservation)

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


def _validate_platform() -> None:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise InitializationError(
            "openshell-middleware-init supports Linux and macOS; "
            f"unsupported platform: {sys.platform}"
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
    identifier = re.sub(r"[^a-z0-9]+", "_", normalized_name).strip("_")
    derived_package = identifier
    if not derived_package or not derived_package[0].isalpha():
        derived_package = f"middleware_{derived_package}".rstrip("_")
    effective_package = package_name if package_name is not None else derived_package
    if not _PYTHON_PACKAGE_PATTERN.fullmatch(effective_package):
        raise InitializationError(
            "Python package name must start with a lowercase letter and contain only "
            "lowercase letters, digits, and underscores"
        )
    service_name = normalized_name.replace("_", "-").replace(".", "-")
    rust_lib_name = identifier
    rust_crate_name = distribution_name
    if not rust_lib_name[0].isalpha() or rust_lib_name in _RUST_RESERVED_IDENTIFIERS:
        rust_lib_name = f"middleware_{rust_lib_name}"
        rust_crate_name = f"middleware-{distribution_name}"
    return TemplateContext(
        project_name=normalized_name,
        distribution_name=distribution_name,
        package_name=effective_package,
        rust_crate_name=rust_crate_name,
        rust_lib_name=rust_lib_name,
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
        _, resolved_url = _fetch_url(request)
    except (OSError, urllib.error.URLError, http.client.IncompleteRead) as error:
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
        body, _ = _fetch_url(request)
        return body, url
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise InitializationError(
                f"{version} does not expose {_PROTO_PATH}; choose a middleware-capable release"
            ) from error
        raise InitializationError(
            f"could not download {_PROTO_PATH} for {version}: HTTP {error.code}"
        ) from error
    except (OSError, urllib.error.URLError, http.client.IncompleteRead) as error:
        raise InitializationError(
            f"could not download {_PROTO_PATH} for {version}: {_network_error_reason(error)}"
        ) from error


def _fetch_url(request: urllib.request.Request) -> tuple[bytes, str]:
    """Fetch a complete response with the same transfer retries as the spike."""
    for attempt in range(_NETWORK_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), response.geturl()
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 429} or 500 <= error.code < 600
            if not retryable or attempt == _NETWORK_ATTEMPTS - 1:
                raise
        except (OSError, urllib.error.URLError, http.client.IncompleteRead):
            if attempt == _NETWORK_ATTEMPTS - 1:
                raise
        time.sleep(0.25 * (2**attempt))
    raise AssertionError("network retry loop exhausted without returning or raising")


def _network_error_reason(
    error: OSError | urllib.error.URLError | http.client.IncompleteRead,
) -> str:
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error)


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


def _acquire_lock(
    lock_path: Path, token: str, destination: Path, version: str
) -> OutputReservation:
    try:
        lock_path.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InitializationError(
            f"output path is reserved by another initializer: {destination}; "
            f"inspect {lock_path / 'metadata.json'} and follow the stale-reservation "
            "recovery steps in the openshell-middleware-init README"
        ) from error
    directory_fd = -1
    try:
        directory_fd = os.open(
            lock_path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        lock_stat = os.fstat(directory_fd)
    except OSError:
        with suppress(OSError):
            lock_path.rmdir()
        raise
    reservation = OutputReservation(
        path=lock_path,
        token=token,
        directory_fd=directory_fd,
        device=lock_stat.st_dev,
        inode=lock_stat.st_ino,
        destination=destination,
        staging_path=(
            destination.parent / f".{destination.name}.openshell-middleware-init.{token}"
        ),
        version=version,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        _write_reservation_file(reservation, "owner", token)
        _write_reservation_metadata(reservation)
    except OSError:
        _cleanup_reservation(reservation)
        raise
    return reservation


def _write_reservation_file(reservation: OutputReservation, name: str, content: str) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=reservation.directory_fd,
    )
    with os.fdopen(descriptor, "w") as reservation_file:
        reservation_file.write(content)


def _write_reservation_metadata(reservation: OutputReservation) -> None:
    _write_reservation_file(
        reservation,
        "metadata.json",
        json.dumps(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": reservation.started_at,
                "target_version": reservation.version,
                "final_output": str(reservation.destination),
                "staging_output": str(reservation.staging_path),
            },
            indent=2,
        )
        + "\n",
    )


def _verify_lock(reservation: OutputReservation) -> None:
    try:
        lock_stat = os.fstat(reservation.directory_fd)
        path_stat = reservation.path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(lock_stat.st_mode)
            or lock_stat.st_dev != reservation.device
            or lock_stat.st_ino != reservation.inode
            or not stat.S_ISDIR(path_stat.st_mode)
            or path_stat.st_dev != reservation.device
            or path_stat.st_ino != reservation.inode
        ):
            raise OSError("reservation identity changed")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open("owner", flags, dir_fd=reservation.directory_fd)
        with os.fdopen(descriptor) as owner:
            owner_stat = os.fstat(owner.fileno())
            if not stat.S_ISREG(owner_stat.st_mode):
                raise OSError("reservation owner is not a regular file")
            recorded = owner.read()
    except OSError as error:
        raise InitializationError("output reservation was lost; refusing to publish") from error
    if recorded != reservation.token:
        raise InitializationError("output reservation ownership changed; refusing to publish")


def _remove_reservation_files(reservation: OutputReservation) -> bool:
    known_names = {"owner", "metadata.json"}
    try:
        if set(os.listdir(reservation.directory_fd)) - known_names:
            return False
    except OSError:
        return False
    for name in known_names:
        try:
            os.unlink(name, dir_fd=reservation.directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            return False
    return True


def _cleanup_reservation(reservation: OutputReservation) -> None:
    files_removed = _remove_reservation_files(reservation)
    try:
        path_stat = reservation.path.stat(follow_symlinks=False)
        path_is_same = (
            stat.S_ISDIR(path_stat.st_mode)
            and path_stat.st_dev == reservation.device
            and path_stat.st_ino == reservation.inode
        )
    except OSError:
        path_is_same = False
    with suppress(OSError):
        os.close(reservation.directory_fd)
    if files_removed and path_is_same:
        with suppress(OSError):
            reservation.path.rmdir()


def _release_lock(reservation: OutputReservation) -> None:
    try:
        _verify_lock(reservation)
    except InitializationError:
        with suppress(OSError):
            os.close(reservation.directory_fd)
        return
    _cleanup_reservation(reservation)


def _publish_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish ``source`` without replacing any destination entry."""
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        try:
            rename = library.renameat2
        except AttributeError as error:  # pragma: no cover - old Linux libc
            raise InitializationError(
                "this Linux runtime cannot publish atomically without replacing an output"
            ) from error
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 1)
    elif sys.platform == "darwin":  # pragma: no cover - platform-specific
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)
    else:  # pragma: no cover - unsupported platform
        raise InitializationError(
            "this platform cannot publish atomically without replacing an output"
        )

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise InitializationError(
            f"output path appeared during setup; refusing to overwrite it: {destination}"
        )
    if error_number in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
        raise InitializationError(
            "the output filesystem does not support atomic no-replace publication"
        )
    raise OSError(error_number, os.strerror(error_number), destination)


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


def _preflight_language(language: str) -> None:
    _require_command("uv" if language == "python" else "cargo")


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
