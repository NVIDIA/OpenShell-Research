# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

PROJECT = Path(__file__).parents[1]


def test_every_demo_action_has_a_side_effect_free_print_mode() -> None:
    for action in (
        "prepare",
        "serve",
        "register",
        "unregister",
        "registration",
        "setup",
        "launch",
        "verify",
        "cleanup",
    ):
        result = subprocess.run(
            ["bash", "demo.sh", "--print", action],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "your-provider-key" not in result.stdout


def test_gateway_helper_removes_only_its_owned_registration(tmp_path: Path) -> None:
    module = _load("gateway_registration", PROJECT / "gateway-registration.py")
    config = tmp_path / "gateway.toml"
    config.write_text(
        """[openshell]
version = 1

[[openshell.supervisor.middleware]]
name = "unrelated"
grpc_endpoint = "https://elsewhere:50051"

[[openshell.supervisor.middleware]]
name = "pi-admission"
grpc_endpoint = "https://service:50051"
"""
    )
    module.remove_gateway_config(config, middleware_name="pi-admission")
    updated = config.read_text()
    assert 'name = "unrelated"' in updated
    assert 'name = "pi-admission"' not in updated


def test_model_selection_keeps_one_native_pi_model(tmp_path: Path) -> None:
    module = _load("prepare", PROJECT / "prepare.py")
    catalog = tmp_path / "models.json"
    catalog.write_text(
        """{"providers":{"demo":{"api":"openai-completions","baseUrl":"https://api.example.test/v1","models":[{"id":"one"},{"id":"two"}]}}}"""
    )
    selected, identity, endpoint = module.select_model(catalog, "demo/two")
    assert identity == {"provider": "demo", "id": "two"}
    assert selected["providers"]["demo"]["models"] == [{"id": "two"}]
    assert endpoint == "https://api.example.test/v1"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
