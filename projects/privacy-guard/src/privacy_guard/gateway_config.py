"""OpenShell gateway configuration updates for the command-line application."""

from __future__ import annotations

import copy
import os
import re
import stat
import tempfile
import tomllib
from enum import Enum
from pathlib import Path


class GatewayConfigUpdate(Enum):
    """Result of writing one Privacy Guard middleware registration."""

    CREATED = "created"
    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class GatewayConfigRemoval(Enum):
    """Result of removing one Privacy Guard middleware registration."""

    REMOVED = "removed"
    UNCHANGED = "unchanged"


class GatewayConfigError(ValueError):
    """A safe, actionable gateway configuration update error."""


# Mirrors OpenShell's stable-identifier byte limit for external middleware
# registrations.
MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES = 128


def default_gateway_config_path() -> Path:
    """Resolve OpenShell's config override or standard per-user gateway path."""
    # Mirrors the OpenShell gateway's `--config` environment override and XDG
    # fallback. Package-specific deployments can still pass --config explicitly.
    configured_path = os.environ.get("OPENSHELL_GATEWAY_CONFIG")
    if configured_path:
        return Path(configured_path)
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "openshell" / "gateway.toml"
    return Path.home() / ".config" / "openshell" / "gateway.toml"


def update_gateway_config(
    path: Path,
    *,
    middleware_name: str,
    host_ip: str,
    port: int,
) -> GatewayConfigUpdate:
    """Add or update one named Privacy Guard middleware registration."""
    validate_middleware_name(middleware_name)
    endpoint = f"http://{host_ip}:{port}"
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        updated = _new_gateway_config(
            middleware_name=middleware_name,
            endpoint=endpoint,
        )
        _write_atomically(path, updated)
        return GatewayConfigUpdate.CREATED
    except (OSError, UnicodeError) as error:
        raise GatewayConfigError(
            f"Could not read {path}. Check that the file is readable UTF-8 TOML."
        ) from error

    if not original.strip():
        updated = _new_gateway_config(
            middleware_name=middleware_name,
            endpoint=endpoint,
        )
        _write_atomically(path, updated)
        return GatewayConfigUpdate.CREATED

    values = _load_gateway_config(original, path)
    middleware = _middleware_entries(values, path)
    matching_indexes = [
        index
        for index, entry in enumerate(middleware)
        if entry.get("name") == middleware_name
    ]
    if len(matching_indexes) > 1:
        raise GatewayConfigError(
            f"{path} contains multiple middleware registrations named "
            f"{middleware_name!r}. Remove the duplicate entries, then retry."
        )

    blocks = list(_MIDDLEWARE_BLOCK_PATTERN.finditer(original))
    if len(blocks) != len(middleware):
        raise GatewayConfigError(
            f"Could not safely locate every middleware registration in {path}. "
            "Format the file as standard TOML tables, then retry."
        )

    if matching_indexes:
        block = blocks[matching_indexes[0]]
        replacement = _update_middleware_block(
            block.group(0),
            endpoint=endpoint,
        )
        updated = original[: block.start()] + replacement + original[block.end() :]
        result = GatewayConfigUpdate.UPDATED
    else:
        updated = _append_middleware_block(
            original,
            middleware_name=middleware_name,
            endpoint=endpoint,
        )
        result = GatewayConfigUpdate.ADDED

    _load_gateway_config(updated, path)
    if updated == original:
        return GatewayConfigUpdate.UNCHANGED
    _write_atomically(path, updated)
    return result


def remove_gateway_config(
    path: Path,
    *,
    middleware_name: str,
) -> GatewayConfigRemoval:
    """Remove one named Privacy Guard middleware registration."""
    validate_middleware_name(middleware_name)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return GatewayConfigRemoval.UNCHANGED
    except (OSError, UnicodeError) as error:
        raise GatewayConfigError(
            f"Could not read {path}. Check that the file is readable UTF-8 TOML."
        ) from error

    if not original.strip():
        return GatewayConfigRemoval.UNCHANGED

    values = _load_gateway_config(original, path)
    middleware = _middleware_entries(values, path)
    matching_indexes = [
        index
        for index, entry in enumerate(middleware)
        if entry.get("name") == middleware_name
    ]
    if len(matching_indexes) > 1:
        raise GatewayConfigError(
            f"{path} contains multiple middleware registrations named "
            f"{middleware_name!r}. Remove the duplicate entries, then retry."
        )
    if not matching_indexes:
        return GatewayConfigRemoval.UNCHANGED

    blocks = list(_MIDDLEWARE_BLOCK_PATTERN.finditer(original))
    if len(blocks) != len(middleware):
        raise GatewayConfigError(
            f"Could not safely locate every middleware registration in {path}. "
            "Format the file as standard TOML tables, then retry."
        )

    matching_index = matching_indexes[0]
    block = blocks[matching_index]
    updated = (
        original[: block.start()]
        + _trailing_middleware_block_layout(block.group(0))
        + original[block.end() :]
    )
    unsafe_removal_message = (
        f"Could not safely remove the middleware registration from {path}. "
        "Format it as a standard TOML array table without child tables, then retry."
    )
    try:
        updated_values = _load_gateway_config(updated, path)
        updated_middleware = _middleware_entries(updated_values, path)
    except GatewayConfigError as error:
        raise GatewayConfigError(unsafe_removal_message) from error
    expected_middleware = [
        entry for index, entry in enumerate(middleware) if index != matching_index
    ]
    if updated_middleware != expected_middleware or _without_middleware_entries(
        updated_values
    ) != _without_middleware_entries(values):
        raise GatewayConfigError(unsafe_removal_message)
    _write_atomically(path, updated)
    return GatewayConfigRemoval.REMOVED


def validate_middleware_name(name: str) -> str:
    """Validate OpenShell's external middleware registration-name contract."""
    if (
        not name
        or len(name.encode("utf-8")) > MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES
        or any(character not in _MIDDLEWARE_NAME_CHARACTERS for character in name)
    ):
        raise GatewayConfigError(
            "Middleware names must use "
            f"1-{MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES} ASCII bytes containing "
            "only letters, digits, '.', '_', '-', or '/'."
        )
    if name.startswith("openshell/"):
        raise GatewayConfigError(
            "Operator-owned middleware names cannot start with 'openshell/'."
        )
    return name


def _new_gateway_config(*, middleware_name: str, endpoint: str) -> str:
    return "[openshell]\nversion = 1\n\n" + _middleware_block(
        middleware_name=middleware_name,
        endpoint=endpoint,
    )


def _load_gateway_config(contents: str, path: Path) -> dict[str, object]:
    try:
        values = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as error:
        raise GatewayConfigError(
            f"{path} is not valid TOML. Fix the reported TOML syntax, then retry."
        ) from error
    openshell = values.get("openshell")
    if not isinstance(openshell, dict) or openshell.get("version") != 1:
        raise GatewayConfigError(
            f"{path} must contain [openshell] with version = 1. "
            "Fix the gateway config version, then retry."
        )
    return values


def _middleware_entries(
    values: dict[str, object],
    path: Path,
) -> list[dict[str, object]]:
    openshell = values["openshell"]
    if not isinstance(openshell, dict):
        raise AssertionError("validated OpenShell table is unavailable")
    supervisor = openshell.get("supervisor")
    if supervisor is None:
        return []
    if not isinstance(supervisor, dict):
        raise GatewayConfigError(
            f"{path} has an invalid [openshell.supervisor] value. "
            "Replace it with a TOML table, then retry."
        )
    middleware = supervisor.get("middleware")
    if middleware is None:
        return []
    if not isinstance(middleware, list):
        raise GatewayConfigError(
            f"{path} has an invalid openshell.supervisor.middleware value. "
            "Use [[openshell.supervisor.middleware]] tables, then retry."
        )
    entries: list[dict[str, object]] = []
    for entry in middleware:
        if not isinstance(entry, dict) or not all(
            isinstance(key, str) for key in entry
        ):
            raise GatewayConfigError(
                f"{path} has an invalid openshell.supervisor.middleware value. "
                "Use [[openshell.supervisor.middleware]] tables, then retry."
            )
        entries.append({str(key): value for key, value in entry.items()})
    return entries


def _append_middleware_block(
    contents: str,
    *,
    middleware_name: str,
    endpoint: str,
) -> str:
    return (
        contents.rstrip()
        + "\n\n"
        + _middleware_block(
            middleware_name=middleware_name,
            endpoint=endpoint,
        )
    )


def _middleware_block(*, middleware_name: str, endpoint: str) -> str:
    return (
        "[[openshell.supervisor.middleware]]\n"
        f'name = "{middleware_name}"\n'
        f'grpc_endpoint = "{endpoint}"\n'
        "max_body_bytes = 4194304\n"
        'timeout = "5s"\n'
    )


def _update_middleware_block(block: str, *, endpoint: str) -> str:
    updated = _replace_or_append_assignment(
        block,
        key="grpc_endpoint",
        value=f'"{endpoint}"',
    )
    updated = _replace_or_append_assignment(
        updated,
        key="max_body_bytes",
        value="4194304",
    )
    return _replace_or_append_assignment(
        updated,
        key="timeout",
        value='"5s"',
    )


def _replace_or_append_assignment(block: str, *, key: str, value: str) -> str:
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}[ \t]*=.*?$",
        flags=re.MULTILINE,
    )
    if pattern.search(block):
        return pattern.sub(
            lambda match: f"{match.group('indent')}{key} = {value}",
            block,
            count=1,
        )
    return block.rstrip() + f"\n{key} = {value}\n"


def _trailing_middleware_block_layout(block: str) -> str:
    lines = block.splitlines(keepends=True)
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].lstrip()
        if stripped.strip() and not stripped.startswith("#"):
            return "".join(lines[index + 1 :])
    raise AssertionError("middleware block header is unavailable")


def _without_middleware_entries(values: dict[str, object]) -> dict[str, object]:
    copied_values = copy.deepcopy(values)
    openshell = copied_values["openshell"]
    if not isinstance(openshell, dict):
        raise AssertionError("validated OpenShell table is unavailable")
    supervisor = openshell.get("supervisor")
    if not isinstance(supervisor, dict):
        return copied_values
    supervisor.pop("middleware", None)
    if not supervisor:
        openshell.pop("supervisor", None)
    return copied_values


def _write_atomically(path: Path, contents: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    except OSError as error:
        raise GatewayConfigError(
            f"Could not write {path}. Check that its directory is writable, then retry."
        ) from error


_MIDDLEWARE_BLOCK_PATTERN = re.compile(
    r"(?ms)^[ \t]*\[\[openshell\.supervisor\.middleware\]\][^\n]*\n"
    r".*?(?=^[ \t]*\[\[?[A-Za-z0-9_-]|\Z)"
)

_MIDDLEWARE_NAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/"
)


__all__ = [
    "GatewayConfigError",
    "GatewayConfigRemoval",
    "GatewayConfigUpdate",
    "MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES",
    "default_gateway_config_path",
    "remove_gateway_config",
    "update_gateway_config",
    "validate_middleware_name",
]
