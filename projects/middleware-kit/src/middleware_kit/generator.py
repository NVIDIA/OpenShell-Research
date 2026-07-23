"""Safe, version-matched project creation and updates."""

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

from middleware_kit import __version__

_REPOSITORY_URL = "https://github.com/NVIDIA/OpenShell"
_RAW_URL = "https://raw.githubusercontent.com/NVIDIA/OpenShell"
_PROTO_PATH = "proto/supervisor_middleware.proto"
_GRPCIO_TOOLS_VERSION = "1.81.1"
_VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(?:[+-][0-9A-Za-z._-]+)?$")
_PYTHON_PACKAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_NETWORK_ATTEMPTS = 4
_TOOL_NAME = "middleware-kit"
_LEGACY_TOOL_NAMES = {"middleware-project", "openshell-middleware-init"}
_STAGING_IGNORED_ROOT_ENTRIES = {
    ".coverage",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ty_cache",
    ".venv",
    "dist",
    "htmlcov",
    "target",
}
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
    """A user-actionable middleware project operation failure."""


class PublicationRollbackError(InitializationError):
    """An update failure whose recovery artifacts must be preserved."""


@dataclass(frozen=True)
class InitializationResult:
    """Details about a successful middleware project operation."""

    destination: Path
    language: str
    openshell_version: str
    run_command: str


@dataclass(frozen=True)
class ProjectMetadata:
    """Metadata needed to refresh a generated middleware project."""

    language: str
    python_package: str | None


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
    lock_path = destination.parent / f".{destination.name}.{_TOOL_NAME}.lock"
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


def update_project(
    *,
    project_dir: Path,
    requested_version: str = "latest",
    download_proto: DownloadProto | None = None,
    command_runner: CommandRunner | None = None,
) -> InitializationResult:
    """Refresh generator-owned artifacts and atomically publish a validated update."""
    _validate_platform()
    project_dir = project_dir.expanduser()
    _validate_existing_project(project_dir)
    project_dir = project_dir.resolve()
    project_stat = project_dir.stat(follow_symlinks=False)
    metadata = _read_project_metadata(project_dir)
    if command_runner is None:
        _preflight_language(metadata.language)
    version = _normalize_version(requested_version)
    downloader = download_proto if download_proto is not None else _download_proto
    runner = command_runner if command_runner is not None else _prepare_project

    lock_path = project_dir.parent / f".{project_dir.name}.{_TOOL_NAME}.lock"
    lock_token = secrets.token_hex(16)
    reservation = _acquire_lock(lock_path, lock_token, project_dir, version)
    staging_path: Path | None = None
    published = False
    preserve_recovery = False
    try:
        _validate_existing_project(project_dir)
        _copy_project_to_staging(project_dir, reservation.staging_path)
        staging_path = reservation.staging_path
        proto, proto_url = downloader(version)
        _validate_proto(proto, version)
        _refresh_generated_artifacts(
            staging_path,
            metadata=metadata,
            version=version,
            proto_url=proto_url,
            proto=proto,
        )
        runner(metadata.language, staging_path, metadata.python_package or "unused")
        _verify_lock(reservation)
        _verify_project_identity(project_dir, project_stat.st_dev, project_stat.st_ino)
        _validate_refresh_targets(
            staging_path,
            metadata.language,
            metadata.python_package,
        )
        _validate_refresh_targets(
            project_dir,
            metadata.language,
            metadata.python_package,
        )
        _publish_generated_artifacts(staging_path, project_dir, metadata)
        published = True
    except PublicationRollbackError:
        preserve_recovery = True
        raise
    except InitializationError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise InitializationError(str(error)) from error
    finally:
        if preserve_recovery:
            with suppress(OSError):
                os.close(reservation.directory_fd)
        else:
            if staging_path is not None:
                shutil.rmtree(staging_path, ignore_errors=True)
            _release_lock(reservation)

    if not published:  # pragma: no cover - defensive; failures raise above
        raise AssertionError("updated project was not published")
    return InitializationResult(
        destination=project_dir,
        language=metadata.language,
        openshell_version=version,
        run_command="",
    )


def _validate_platform() -> None:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise InitializationError(
            f"{_TOOL_NAME} supports Linux and macOS; unsupported platform: {sys.platform}"
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
        headers={"User-Agent": f"{_TOOL_NAME}/{__version__}"},
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
        headers={"User-Agent": f"{_TOOL_NAME}/{__version__}"},
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


def _validate_existing_project(project_dir: Path) -> None:
    if project_dir.is_symlink():
        raise InitializationError(f"project path must not be a symlink: {project_dir}")
    if not project_dir.is_dir():
        raise InitializationError(f"project path must be an existing directory: {project_dir}")


def _verify_project_identity(project_dir: Path, device: int, inode: int) -> None:
    try:
        current = project_dir.stat(follow_symlinks=False)
    except OSError as error:
        raise InitializationError(
            "project path changed during update; refusing to publish"
        ) from error
    if not stat.S_ISDIR(current.st_mode) or current.st_dev != device or current.st_ino != inode:
        raise InitializationError("project path changed during update; refusing to publish")


def _read_project_metadata(project_dir: Path) -> ProjectMetadata:
    manifest_path = project_dir / "middleware-dev-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise InitializationError(
            f"not a generated middleware project; missing regular manifest: {manifest_path}"
        )
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InitializationError(f"could not read project manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise InitializationError("project manifest must contain a JSON object")

    generator = manifest.get("generator")
    generator_name = generator.get("name") if isinstance(generator, dict) else None
    if generator_name != _TOOL_NAME and generator_name not in _LEGACY_TOOL_NAMES:
        raise InitializationError(
            "project manifest was not created by middleware-kit, middleware-project, "
            "or openshell-middleware-init"
        )
    languages = manifest.get("languages")
    if languages not in (["python"], ["rust"]):
        raise InitializationError("project manifest must identify exactly one supported language")
    language = languages[0]
    python_package = manifest.get("python_package")
    if language == "python":
        if not isinstance(python_package, str) or not _PYTHON_PACKAGE_PATTERN.fullmatch(
            python_package
        ):
            raise InitializationError(
                "Python project manifest has an invalid or missing python_package"
            )
    elif python_package is not None:
        raise InitializationError("Rust project manifest must set python_package to null")

    _validate_refresh_targets(project_dir, language, python_package)
    return ProjectMetadata(language=language, python_package=python_package)


def _validate_refresh_targets(project_dir: Path, language: str, python_package: str | None) -> None:
    regular_files = [
        project_dir / "middleware-dev-manifest.json",
        project_dir / "proto" / "supervisor_middleware.proto",
    ]
    regular_files.append(project_dir / ("uv.lock" if language == "python" else "Cargo.lock"))
    for path in regular_files:
        _reject_symlink_components(project_dir, path)
        if path.is_symlink() or not path.is_file():
            raise InitializationError(f"generated artifact must be a regular file: {path}")

    if language == "python":
        if python_package is None:  # pragma: no cover - checked by caller
            raise AssertionError("Python package is required")
        bindings_dir = project_dir / "src" / python_package / "bindings"
        _reject_symlink_components(project_dir, bindings_dir)
        if bindings_dir.is_symlink() or not bindings_dir.is_dir():
            raise InitializationError(
                f"generated bindings must be an existing directory: {bindings_dir}"
            )


def _reject_symlink_components(project_dir: Path, target: Path) -> None:
    relative_target = target.relative_to(project_dir)
    current = project_dir
    for component in relative_target.parts:
        current /= component
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current_stat.st_mode):
            raise InitializationError(
                f"generated artifact path must not contain symlinks: {current}"
            )


def _copy_project_to_staging(project_dir: Path, staging_path: Path) -> None:
    def ignore_disposable_entries(directory: str, names: list[str]) -> set[str]:
        ignored = {"__pycache__"} & set(names)
        if Path(directory) == project_dir:
            ignored.update(_STAGING_IGNORED_ROOT_ENTRIES & set(names))
        return ignored

    shutil.copytree(
        project_dir,
        staging_path,
        symlinks=True,
        ignore=ignore_disposable_entries,
    )
    staging_path.chmod(0o700)


def _refresh_generated_artifacts(
    project_dir: Path,
    *,
    metadata: ProjectMetadata,
    version: str,
    proto_url: str,
    proto: bytes,
) -> None:
    _validate_refresh_targets(
        project_dir,
        metadata.language,
        metadata.python_package,
    )
    proto_path = project_dir / "proto" / "supervisor_middleware.proto"
    proto_path.write_bytes(proto)
    if metadata.language == "python":
        if metadata.python_package is None:  # pragma: no cover - checked during discovery
            raise AssertionError("Python package is required")
        bindings_dir = project_dir / "src" / metadata.python_package / "bindings"
        shutil.rmtree(bindings_dir)
        bindings_dir.mkdir()
        bindings_dir.joinpath("__init__.py").write_text(
            '"""Generated OpenShell supervisor middleware bindings. Do not edit."""\n'
        )
    _write_manifest(
        project_dir,
        version=version,
        proto_url=proto_url,
        proto=proto,
        language=metadata.language,
        python_package=metadata.python_package,
    )


def _acquire_lock(
    lock_path: Path, token: str, destination: Path, version: str
) -> OutputReservation:
    try:
        lock_path.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InitializationError(
            f"project path is reserved by another {_TOOL_NAME} process: {destination}; "
            f"inspect {lock_path / 'metadata.json'} and follow the stale-reservation "
            f"recovery steps in the {_TOOL_NAME} README"
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
        staging_path=(destination.parent / f".{destination.name}.{_TOOL_NAME}.{token}"),
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


def _publish_exchange(source: Path, destination: Path) -> None:
    """Atomically exchange a validated staged project with its existing project."""
    source_parent_fd = _open_parent_directory_no_follow(source)
    destination_parent_fd = _open_parent_directory_no_follow(destination)
    try:
        _validate_exchange_entries(
            source_parent_fd,
            source.name,
            destination_parent_fd,
            destination.name,
        )
        source_name = os.fsencode(source.name)
        destination_name = os.fsencode(destination.name)
        result = _exchange_at(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
        )
        if result != 0:
            _raise_exchange_error(destination)
        source_attached = _directory_fd_matches_path(source.parent, source_parent_fd)
        destination_attached = _directory_fd_matches_path(
            destination.parent,
            destination_parent_fd,
        )
        if source_attached and destination_attached:
            return

        reverse_result = _exchange_at(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
        )
        if reverse_result != 0:
            error_number = ctypes.get_errno()
            raise PublicationRollbackError(
                "an artifact parent changed during publication and the anchored exchange "
                f"could not be reversed: {os.strerror(error_number)}"
            )
        raise InitializationError(
            "an artifact parent changed during publication; the exchange was reversed"
        )
    finally:
        os.close(source_parent_fd)
        os.close(destination_parent_fd)


def _exchange_at(
    source_parent_fd: int,
    source_name: bytes,
    destination_parent_fd: int,
    destination_name: bytes,
) -> int:
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        try:
            rename = library.renameat2
        except AttributeError as error:  # pragma: no cover - old Linux libc
            raise InitializationError(
                "this Linux runtime cannot atomically publish a project update"
            ) from error
        exchange_flag = 2
    elif sys.platform == "darwin":  # pragma: no cover - platform-specific
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameatx_np
        exchange_flag = 0x00000002
    else:  # pragma: no cover - unsupported platform
        raise InitializationError("this platform cannot atomically publish a project update")
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    return rename(
        source_parent_fd,
        source_name,
        destination_parent_fd,
        destination_name,
        exchange_flag,
    )


def _raise_exchange_error(destination: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
        raise InitializationError(
            "the project filesystem does not support atomic update publication"
        )
    raise OSError(error_number, os.strerror(error_number), destination)


def _open_parent_directory_no_follow(path: Path) -> int:
    return _open_directory_no_follow(path.parent)


def _open_directory_no_follow(path: Path) -> int:
    if not path.is_absolute():
        raise InitializationError(f"artifact path must be absolute: {path}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _directory_fd_matches_path(path: Path, expected_descriptor: int) -> bool:
    try:
        current_descriptor = _open_directory_no_follow(path)
    except (InitializationError, OSError):
        return False
    try:
        expected_stat = os.fstat(expected_descriptor)
        current_stat = os.fstat(current_descriptor)
        return (
            expected_stat.st_dev == current_stat.st_dev
            and expected_stat.st_ino == current_stat.st_ino
        )
    finally:
        os.close(current_descriptor)


def _validate_exchange_entries(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    source_stat = os.stat(source_name, dir_fd=source_parent_fd, follow_symlinks=False)
    destination_stat = os.stat(
        destination_name,
        dir_fd=destination_parent_fd,
        follow_symlinks=False,
    )
    source_is_directory = stat.S_ISDIR(source_stat.st_mode)
    destination_is_directory = stat.S_ISDIR(destination_stat.st_mode)
    source_is_regular = stat.S_ISREG(source_stat.st_mode)
    destination_is_regular = stat.S_ISREG(destination_stat.st_mode)
    if not (
        (source_is_directory and destination_is_directory)
        or (source_is_regular and destination_is_regular)
    ):
        raise InitializationError(
            "generated artifacts changed type during update; refusing to publish"
        )


def _publish_generated_artifacts(
    staged_project: Path, project_dir: Path, metadata: ProjectMetadata
) -> None:
    """Exchange refreshed artifacts in place, rolling back a normal publication failure."""
    relative_paths = [
        Path("proto/supervisor_middleware.proto"),
        Path("uv.lock" if metadata.language == "python" else "Cargo.lock"),
    ]
    if metadata.language == "python":
        if metadata.python_package is None:  # pragma: no cover - checked during discovery
            raise AssertionError("Python package is required")
        relative_paths.append(Path("src") / metadata.python_package / "bindings")
    relative_paths.append(Path("middleware-dev-manifest.json"))

    exchanged: list[Path] = []
    try:
        for relative_path in relative_paths:
            _publish_exchange(
                staged_project / relative_path,
                project_dir / relative_path,
            )
            exchanged.append(relative_path)
    except (InitializationError, OSError) as publish_error:
        try:
            for relative_path in reversed(exchanged):
                _publish_exchange(
                    staged_project / relative_path,
                    project_dir / relative_path,
                )
        except (InitializationError, OSError) as rollback_error:
            raise PublicationRollbackError(
                "artifact publication and rollback both failed; inspect the project and "
                f"{staged_project} before removing the reservation"
            ) from rollback_error
        raise publish_error


def _render_project(destination: Path, language: str, context: TemplateContext) -> None:
    template_root = files("middleware_kit").joinpath("templates").joinpath(language)
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
            "name": _TOOL_NAME,
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
        raise InitializationError(f"'{command}' is required to prepare this project")
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

    with tempfile.TemporaryDirectory(prefix=f"{_TOOL_NAME}-python-") as environment:
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
                "pytest",
            ),
            cwd=project_dir,
            environment=process_environment,
        )


def _prepare_rust_project(project_dir: Path) -> None:
    cargo = _require_command("cargo")
    with tempfile.TemporaryDirectory(prefix=f"{_TOOL_NAME}-rust-") as target_dir:
        process_environment = os.environ.copy()
        process_environment["CARGO_TARGET_DIR"] = target_dir
        _run(
            (cargo, "test", "--manifest-path", str(project_dir / "Cargo.toml")),
            cwd=project_dir,
            environment=process_environment,
        )
