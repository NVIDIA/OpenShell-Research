# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import yaml


def test_pi_example_can_print_each_action_without_running_it(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"
    pi_repo = tmp_path / "pi"
    openshell_repo = tmp_path / "OpenShell"
    pack_dir = tmp_path / "pack"
    runtime_dir = tmp_path / "runtime"
    environment = os.environ | {
        "PI_REPO": str(pi_repo),
        "OPENSHELL_REPO": str(openshell_repo),
        "EGRESS_GATE_HOST_IP": "192.0.2.10",
        "PI_MODEL_BASE_URL": "https://models.example.test/v1",
        "PI_MODEL_ID": "example-model",
        "PI_EGRESS_PACK_DIR": str(pack_dir),
        "PI_EGRESS_RUNTIME_DIR": str(runtime_dir),
    }

    results = [
        subprocess.run(
            ["bash", str(script), "--print", action],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        for action in ("prepare", "serve", "gateway", "launch", "cleanup")
    ]
    output = "\n".join(result.stdout for result in results)

    assert "npm run build" in output
    assert "earendil-works-pi-agent-core-VERSION.tgz" in output
    assert "earendil-works-pi-coding-agent-VERSION.tgz" in output
    assert "npm pack --workspace @earendil-works/pi-agent-core" in output
    assert "npm pack --workspace @earendil-works/pi-coding-agent" in output
    assert "git clone --branch johnny/before-user-message-commit" in output
    assert "git clone --branch openshell/pi-egress-admission" in output
    assert (
        "git pull --no-rebase --ff-only origin johnny/before-user-message-commit"
        in output
    )
    assert (
        "git pull --no-rebase --ff-only origin openshell/pi-egress-admission" in output
    )
    assert "gateway-middleware.toml" in output
    assert "OPENSHELL_GATEWAY_CONFIG_FRAGMENT=" in output
    assert "render-runtime-config.mjs" in output
    assert "https://models.example.test/v1" in output
    assert "example-model" in output
    assert "egress-gate --debug serve" in output
    assert "CARGO_BUILD_JOBS=4" in output
    assert "OPENSHELL_GATEWAY_NAME=pi-egress-demo-gateway" in output
    assert "--gateway pi-egress-demo-gateway" in output
    assert "provider create" in output
    assert "provider profile import" in output
    assert "provider profile delete pi-attested-model" in output
    assert "provider delete pi-model" in output
    assert "provider profile update" not in output
    assert "--type pi-attested-model" in output
    assert "PI_MODEL_API_KEY" in output
    assert "OPENAI_API_KEY" not in output
    assert "api.openai.com" not in output
    assert "sandbox create" in output
    assert "--detach" in output
    assert "--no-git-ignore" in output
    assert f"{runtime_dir}/node_modules:/sandbox/pi-runtime" in output
    assert f"{runtime_dir}:/sandbox/pi-runtime" not in output
    assert "sandbox exec" in output
    assert "sandbox exec --tty" in output
    assert "PI_OFFLINE=1" in output
    assert "--no-extensions" in output
    assert "--provider" in output
    assert "--model" in output
    assert "OPENSHELL_AGENT_CONVERSATION_URL=" in output
    assert "managed-pi.ts:/sandbox/pi-runtime/managed-pi.ts" in output
    assert (
        "managed-pi-admission.ts:/sandbox/pi-runtime/managed-pi-admission.ts" in output
    )
    assert "--extension " not in output
    assert "sandbox delete" in output
    assert all(result.stderr == "" for result in results)
    assert not pi_repo.exists()
    assert not openshell_repo.exists()
    assert not pack_dir.exists()
    assert not runtime_dir.exists()

    managed_pi = (
        project_dir / "examples/pi-attested-admission/managed-pi.ts"
    ).read_text()
    assert "main(process.argv.slice(2)" in managed_pi
    assert "SessionManager" not in managed_pi
    assert "ModelRuntime.create" not in managed_pi
    assert "InteractiveMode" not in managed_pi
    assert "thinkingLevel" not in managed_pi
    assert 'readFileSync(3, "utf8")' in managed_pi
    assert "closeSync(3)" in managed_pi
    assert "process.env.PI_MODEL_API_KEY" not in managed_pi
    assert "await modelRuntime.setRuntimeApiKey(provider, modelApiKey)" in managed_pi
    assert "configureModelRuntime" in managed_pi
    assert "createContextAdmission" in managed_pi

    demo_script = (project_dir / "examples/pi-attested-admission/demo.sh").read_text()
    assert '"beforeToolResultAppend"' in demo_script
    assert "exec env -u PI_MODEL_API_KEY node" in demo_script
    assert '3<<<"$PI_MODEL_API_KEY"' in demo_script
    assert '"configureModelRuntime"' in demo_script
    assert '"createContextAdmission"' in demo_script


def test_pi_example_print_all_is_a_concise_walkthrough() -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"

    result = subprocess.run(
        ["bash", str(script), "--print", "all"],
        check=True,
        capture_output=True,
        env=os.environ
        | {
            "EGRESS_GATE_HOST_IP": "192.0.2.10",
            "PI_MODEL_BASE_URL": "https://models.example.test/v1",
            "PI_MODEL_ID": "example-model",
            "PI_MODEL_API_KEY": "secret-not-printed",
        },
        text=True,
    )

    assert "Pi attested-admission walkthrough" in result.stdout
    assert "Configuration visible to this shell" in result.stdout
    assert "Model credential:  set (value hidden)" in result.stdout
    assert "1. prepare" in result.stdout
    assert "6. cleanup" in result.stdout
    assert "secret-not-printed" not in result.stdout
    assert "working directory:" not in result.stdout


def test_pi_example_uses_terminal_colors_without_leaking_them_to_redirects() -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"
    environment = {
        name: value for name, value in os.environ.items() if name != "NO_COLOR"
    } | {"FORCE_COLOR": "1"}

    colored = subprocess.run(
        ["bash", str(script), "--print", "all"],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    uncolored = subprocess.run(
        ["bash", str(script), "--print", "all"],
        check=True,
        capture_output=True,
        env=environment | {"NO_COLOR": "1"},
        text=True,
    )
    assert "\x1b[36m" in colored.stdout
    assert "\x1b[" not in uncolored.stdout


def test_pi_example_defaults_to_an_ignored_external_workspace() -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"

    result = subprocess.run(
        ["bash", str(script), "--print", "prepare"],
        check=True,
        capture_output=True,
        env={
            name: value
            for name, value in os.environ.items()
            if name not in {"PI_REPO", "OPENSHELL_REPO", "PI_EGRESS_FORKS_DIR"}
        },
        text=True,
    )

    workspace = project_dir / ".workspaces/pi-attested-admission"
    assert str(workspace / "pi") in result.stdout
    assert str(workspace / "OpenShell") in result.stdout
    assert ".workspaces/" in (project_dir / ".gitignore").read_text().splitlines()


def test_pi_example_renders_provider_specific_runtime_configuration(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    example_dir = project_dir / "examples/pi-attested-admission"
    models_output = tmp_path / "models.json"
    policy_output = tmp_path / "policy.yaml"
    provider_profile_output = tmp_path / "provider-profile.yaml"
    gateway_output = tmp_path / "gateway-middleware.toml"

    subprocess.run(
        [
            "node",
            str(example_dir / "render-runtime-config.mjs"),
            "--base-url",
            "https://gateway.example.test:8443/models/v1",
            "--model-id",
            "custom-model",
            "--models-output",
            str(models_output),
            "--policy-output",
            str(policy_output),
            "--provider-profile-output",
            str(provider_profile_output),
            "--middleware-endpoint",
            "http://192.0.2.10:50051",
            "--gateway-output",
            str(gateway_output),
        ],
        check=True,
    )

    models = json.loads(models_output.read_text())
    provider = models["providers"]["attested-provider"]
    assert provider["baseUrl"] == "https://gateway.example.test:8443/models/v1"
    assert provider["api"] == "openai-completions"
    assert provider["apiKey"] == "$PI_MODEL_API_KEY"
    assert provider["models"][0]["id"] == "custom-model"
    assert provider["models"][0]["reasoning"] is True
    assert provider["models"][0]["compat"] == {"supportsReasoningEffort": True}

    provider_profile = yaml.safe_load(provider_profile_output.read_text())
    assert provider_profile["id"] == "pi-attested-model"
    assert provider_profile["credentials"][0]["env_vars"] == ["PI_MODEL_API_KEY"]
    assert provider_profile["endpoints"][0]["host"] == "gateway.example.test"
    assert provider_profile["endpoints"][0]["port"] == 8443

    policy = yaml.safe_load(policy_output.read_text())
    endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    assert endpoint["host"] == "gateway.example.test"
    assert endpoint["port"] == 8443
    middleware = policy["network_middlewares"]["pi_egress_gate"]
    assert middleware["endpoints"]["include"] == ["gateway.example.test"]

    gateway_fragment = tomllib.loads(gateway_output.read_text())
    registration = gateway_fragment["openshell"]["supervisor"]["middleware"][0]
    assert registration["name"] == "pi-egress"
    assert registration["grpc_endpoint"] == "http://192.0.2.10:50051"
    assert registration["allow_insecure_transport"] is True
    assert registration["max_payload_bytes"] == 4 * 1024 * 1024


def test_pi_example_reports_all_missing_configuration_before_work(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"
    environment = {
        name: value
        for name, value in os.environ.items()
        if name
        not in {
            "EGRESS_GATE_HOST_IP",
            "PI_MODEL_BASE_URL",
            "PI_MODEL_ID",
            "PI_MODEL_API_KEY",
        }
    }

    result = subprocess.run(
        ["bash", str(script), "prepare"],
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "The Pi attested-admission example is not configured." in result.stderr
    assert "EGRESS_GATE_HOST_IP" in result.stderr
    assert "PI_MODEL_BASE_URL" in result.stderr
    assert "PI_MODEL_ID" in result.stderr
    assert "PI_MODEL_API_KEY" in result.stderr
    assert "source .env" in result.stderr
    assert "git pull" not in result.stderr


def test_pi_example_reports_a_missing_compute_backend_before_mise(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"
    for command in ("docker", "podman"):
        stub = tmp_path / command
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), "gateway"],
        capture_output=True,
        env=os.environ
        | {
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "OPENSHELL_DRIVERS": "",
            "EGRESS_GATE_HOST_IP": "192.0.2.10",
            "PI_MODEL_BASE_URL": "https://models.example.test/v1",
            "PI_MODEL_ID": "example-model",
            "PI_MODEL_API_KEY": "test-key",
        },
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "No running OpenShell compute backend was detected." in result.stderr
    assert "docker info" in result.stderr
    assert "podman info" in result.stderr
    assert "mise" not in result.stderr
