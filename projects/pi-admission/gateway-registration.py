# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Register this demo with a local, installer-managed OpenShell gateway."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GatewayRegistration:
    name: str


def configure(action: str, state: Path, gateway: str) -> None:
    record = state / "gateway-registration.json"
    if action == "unregister" and not record.exists():
        print("No demo-managed gateway registration to remove.")
        return
    gateways = json.loads(_output("openshell", "gateway", "list", "--output", "json"))
    selected = next((item for item in gateways if item["name"] == gateway), None)
    endpoint = urlsplit(selected["endpoint"] if selected else "")
    if (
        endpoint.scheme != "https"
        or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
        or endpoint.port != 17670
    ):
        raise ValueError(
            "Select the local installer-managed gateway on HTTPS port 17670."
        )

    config, restart = _local_service()
    registration = {"gateway": gateway, "config": str(config)}
    saved = json.loads(record.read_text()) if record.exists() else None
    if saved is not None and any(saved.get(k) != v for k, v in registration.items()):
        raise ValueError(
            "Gateway/config changed; restore the previous selection to clean up first."
        )

    if action == "register":
        fragment = (state / "middleware.toml").read_text()
        desired = tomllib.loads(fragment)["openshell"]["supervisor"]["middleware"][0]
        original = (
            config.read_text() if config.exists() else "[openshell]\nversion = 1\n"
        )
        # The shared reader validates the middleware table before we edit anything.
        matches = [
            r for r in list_gateway_registrations(config) if r.name == "pi-admission"
        ]
        if matches:
            entries = tomllib.loads(original)["openshell"]["supervisor"]["middleware"]
            existing = [
                entry for entry in entries if entry.get("name") == "pi-admission"
            ]
            if existing != [desired] or saved is None or saved.get("entry") != desired:
                raise ValueError(
                    "pi-admission is already registered; refusing to overwrite it."
                )
        else:
            updated = original.rstrip() + "\n\n" + fragment
            tomllib.loads(updated)
            # Remember ownership so cleanup also works after a failed restart.
            record.write_text(json.dumps({**registration, "entry": desired}) + "\n")
            config.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=config.parent, delete=False
            ) as temporary:
                try:
                    temporary.write(updated)
                    temporary.close()
                    if config.exists():
                        os.chmod(temporary.name, config.stat().st_mode & 0o777)
                    Path(temporary.name).replace(config)
                finally:
                    Path(temporary.name).unlink(missing_ok=True)
        print(f"Registered pi-admission in {config}", flush=True)
    else:
        entries = (
            (
                tomllib.loads(config.read_text())
                .get("openshell", {})
                .get("supervisor", {})
                .get("middleware", [])
            )
            if config.exists()
            else []
        )
        existing = [entry for entry in entries if entry.get("name") == "pi-admission"]
        if existing and (saved is None or existing != [saved.get("entry")]):
            raise ValueError(
                "pi-admission registration changed; refusing to remove it."
            )
        remove_gateway_config(config, middleware_name="pi-admission")
        print(f"Removed pi-admission from {config}", flush=True)

    # Restart even on a retry: the previous write may have succeeded but reload failed.
    print(
        f"+ {shlex.join(restart)} (briefly interrupts this gateway)",
        flush=True,
    )
    subprocess.run(restart, check=True)
    _wait_for_gateway(gateway)
    if action == "unregister":
        record.unlink()


def _output(*command: str) -> str:
    return subprocess.check_output(command, text=True, timeout=10)


def default_gateway_config_path() -> Path:
    configured = os.environ.get("OPENSHELL_GATEWAY_CONFIG")
    if configured:
        return Path(configured)
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "openshell/gateway.toml"


def list_gateway_registrations(path: Path) -> tuple[GatewayRegistration, ...]:
    if not path.exists() or not path.read_text().strip():
        return ()
    values = _load_gateway_config(path.read_text(), path)
    return tuple(GatewayRegistration(str(entry["name"])) for entry in _entries(values))


def remove_gateway_config(path: Path, *, middleware_name: str) -> None:
    if not path.exists() or not path.read_text().strip():
        return
    original = path.read_text()
    values = _load_gateway_config(original, path)
    entries = _entries(values)
    indexes = [
        index
        for index, entry in enumerate(entries)
        if entry.get("name") == middleware_name
    ]
    if len(indexes) > 1:
        raise ValueError(f"{path} contains duplicate {middleware_name!r} registrations")
    if not indexes:
        return
    blocks = list(_MIDDLEWARE_BLOCK_PATTERN.finditer(original))
    if len(blocks) != len(entries):
        raise ValueError(f"Could not safely locate middleware registrations in {path}")
    block = blocks[indexes[0]]
    updated = (
        original[: block.start()]
        + _trailing_layout(block.group(0))
        + original[block.end() :]
    )
    updated_values = _load_gateway_config(updated, path)
    expected = [entry for index, entry in enumerate(entries) if index != indexes[0]]
    if _entries(updated_values) != expected or _without_entries(
        updated_values
    ) != _without_entries(values):
        raise ValueError(
            f"Could not safely remove the middleware registration from {path}"
        )
    _write_atomically(path, updated)


def _load_gateway_config(contents: str, path: Path) -> dict[str, object]:
    try:
        values = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path} is not valid TOML") from error
    openshell = values.get("openshell")
    if not isinstance(openshell, dict) or openshell.get("version") != 1:
        raise ValueError(f"{path} must contain [openshell] with version = 1")
    return values


def _entries(values: dict[str, object]) -> list[dict[str, object]]:
    openshell = values["openshell"]
    if not isinstance(openshell, dict):
        raise ValueError("invalid openshell table")
    supervisor = openshell.get("supervisor", {})
    if not isinstance(supervisor, dict):
        raise ValueError("invalid openshell.supervisor table")
    entries = supervisor.get("middleware", [])
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) for entry in entries
    ):
        raise ValueError("invalid openshell.supervisor.middleware tables")
    return entries


def _without_entries(values: dict[str, object]) -> dict[str, object]:
    copied = copy.deepcopy(values)
    openshell = copied["openshell"]
    assert isinstance(openshell, dict)
    supervisor = openshell.get("supervisor")
    if isinstance(supervisor, dict):
        supervisor.pop("middleware", None)
        if not supervisor:
            openshell.pop("supervisor", None)
    return copied


def _trailing_layout(block: str) -> str:
    lines = block.splitlines(keepends=True)
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].lstrip()
        if stripped.strip() and not stripped.startswith("#"):
            return "".join(lines[index + 1 :])
    raise ValueError("middleware block header is unavailable")


def _write_atomically(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False
    ) as temporary:
        try:
            temporary.write(contents)
            temporary.close()
            Path(temporary.name).chmod(mode)
            Path(temporary.name).replace(path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


def _local_service() -> tuple[Path, list[str]]:
    config = default_gateway_config_path()
    service_env = config.with_name("gateway.env")
    if sys.platform == "darwin":
        # Match the Homebrew wrapper: user config, then prefix config.
        prefix = Path(_output("brew", "--prefix").strip()) / "var/openshell"
        if not service_env.is_file():
            service_env = prefix / "gateway.env"
        if not config.is_file():
            config = prefix / "gateway.toml"
        config = config.resolve(strict=True)
        restart = ["brew", "services", "restart", "openshell"]
    elif sys.platform == "linux":
        # DEB/RPM installations use defaults until a user config is created.
        if (
            _output(
                "systemctl",
                "--user",
                "show",
                "openshell-gateway",
                "--property=LoadState",
                "--value",
            ).strip()
            != "loaded"
        ):
            raise ValueError("No installer-managed OpenShell user service found.")
        restart = ["systemctl", "--user", "restart", "openshell-gateway"]
    else:
        raise ValueError("No supported local gateway service manager found.")
    # Custom service environments are operator-managed, not inferred from our shell.
    if os.environ.get("OPENSHELL_GATEWAY_CONFIG") or (
        service_env.is_file()
        and any(
            "OPENSHELL_GATEWAY_CONFIG" in line
            for line in service_env.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    ):
        raise ValueError(
            "Custom gateway config override: use operator-managed registration."
        )
    return config.resolve(), restart


def _wait_for_gateway(gateway: str) -> None:
    command = ["openshell", "--gateway", gateway, "gateway", "info", "--output", "json"]
    print(f"Waiting for healthy gateway: {shlex.join(command)}", flush=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if (
                result.returncode == 0
                and json.loads(result.stdout)["status"] == "healthy"
            ):
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)
    raise ValueError(
        "Gateway did not become healthy. Check the gateway service logs, then retry."
    )


_MIDDLEWARE_BLOCK_PATTERN = re.compile(
    r"(?ms)^[ \t]*\[\[openshell\.supervisor\.middleware\]\][^\n]*\n"
    r".*?(?=^[ \t]*\[\[?[A-Za-z0-9_-]|\Z)"
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "unregister"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    args = parser.parse_args()
    try:
        configure(args.action, args.state, args.gateway)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
