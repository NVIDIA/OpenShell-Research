# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Register this demo with a local, installer-managed OpenShell gateway."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from egress_gate.gateway_config import (
    default_gateway_config_path,
    list_gateway_registrations,
    remove_gateway_config,
)


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
            r for r in list_gateway_registrations(config) if r.name == "pi-egress"
        ]
        if matches:
            entries = tomllib.loads(original)["openshell"]["supervisor"]["middleware"]
            existing = [entry for entry in entries if entry.get("name") == "pi-egress"]
            if existing != [desired] or saved is None or saved.get("entry") != desired:
                raise ValueError(
                    "pi-egress is already registered; refusing to overwrite it."
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
        print(f"Registered pi-egress in {config}", flush=True)
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
        existing = [entry for entry in entries if entry.get("name") == "pi-egress"]
        if existing and (saved is None or existing != [saved.get("entry")]):
            raise ValueError("pi-egress registration changed; refusing to remove it.")
        remove_gateway_config(config, middleware_name="pi-egress")
        print(f"Removed pi-egress from {config}", flush=True)

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
