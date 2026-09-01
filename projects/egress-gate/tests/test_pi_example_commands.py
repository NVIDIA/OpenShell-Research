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
    models_path = project_dir / "examples/pi-attested-admission/models.json"
    environment = os.environ | {
        "PI_REPO": str(pi_repo),
        "OPENSHELL_REPO": str(openshell_repo),
        "EGRESS_GATE_HOST_IP": "192.0.2.10",
        "PI_MODELS_PATH": str(models_path),
        "PI_EGRESS_PACK_DIR": str(pack_dir),
        "PI_EGRESS_RUNTIME_DIR": str(runtime_dir),
        "PI_WORKSPACE_PATH": str(tmp_path / "workspace"),
    }

    results = [
        subprocess.run(
            ["bash", str(script), "--print", action],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        for action in ("prepare", "serve", "gateway", "reset", "launch", "cleanup")
    ]
    output = "\n".join(result.stdout for result in results)

    assert "npm run build:offline" in output
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
    assert "gateway-middleware.toml.example" in output
    assert "render-runtime-config.mjs" not in output
    assert str(models_path) in output
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
    assert "--from" in output
    assert "pi-attested-admission/sandbox" in output
    assert "--detach" in output
    assert "--no-git-ignore" in output
    assert f"{runtime_dir}/node_modules:/sandbox/pi-runtime" in output
    assert f"{runtime_dir}:/sandbox/pi-runtime" not in output
    assert f"{models_path}:/sandbox/.pi/agent/models.json" in output
    assert "settings.json:/sandbox/.pi/agent/settings.json" in output
    assert "sandbox upload" in output
    assert "/sandbox/workspace" in output
    assert "sandbox exec" in output
    assert "sandbox exec --tty" in output
    assert "PI_OFFLINE=1" not in output
    assert "PI_CODING_AGENT_DIR=" not in output
    assert "--no-extensions" not in output
    assert "/sandbox/pi-runtime/node_modules/.bin/pi" in output
    assert "PI_OPENSHELL_CONTEXT_ADMISSION=1" in output
    assert "OPENSHELL_AGENT_CONVERSATION_URL=" in output
    assert "managed-pi" not in output
    assert "--extension " not in output
    assert "sandbox delete" in output
    assert all(result.stderr == "" for result in results)
    assert not pi_repo.exists()
    assert not openshell_repo.exists()
    assert not pack_dir.exists()
    assert not runtime_dir.exists()

    reset_output = results[3].stdout
    normalized_reset_output = " ".join(reset_output.replace("\\\n", " ").split())
    assert f"working directory: {tmp_path / 'workspace'}" in reset_output
    assert (
        "sandbox upload pi-egress-demo . /sandbox/workspace" in normalized_reset_output
    )
    assert (
        f"sandbox upload pi-egress-demo {tmp_path / 'workspace'}"
        not in normalized_reset_output
    )

    demo_script = (project_dir / "examples/pi-attested-admission/demo.sh").read_text()
    assert '"beforeToolResultAppend"' in demo_script
    assert "exec env -u PI_MODEL_API_KEY node" not in demo_script
    assert '3<<<"$PI_MODEL_API_KEY"' not in demo_script
    assert "render-runtime-config.mjs" not in demo_script


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
            "PI_MODELS_PATH": str(
                project_dir / "examples/pi-attested-admission/models.json"
            ),
            "PI_MODEL_API_KEY": "secret-not-printed",
            "PI_WORKSPACE_PATH": "/tmp/example-workspace",
        },
        text=True,
    )

    assert "Pi attested-admission walkthrough" in result.stdout
    assert "Configuration visible to this shell" in result.stdout
    assert "Model credential:  set (value hidden)" in result.stdout
    assert "1. prepare" in result.stdout
    assert "7. cleanup" in result.stdout
    assert "secret-not-printed" not in result.stdout
    assert "working directory:" not in result.stdout


def test_pi_example_launch_preserves_the_prepared_sandbox() -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"

    result = subprocess.run(
        ["bash", str(script), "--print", "launch"],
        check=True,
        capture_output=True,
        env=os.environ,
        text=True,
    )

    normalized_output = " ".join(result.stdout.replace("\\\n", " ").split())
    assert "sandbox exec --tty" in normalized_output
    assert "--workdir /sandbox/workspace" in normalized_output
    assert "sandbox delete" not in result.stdout
    assert "sandbox create" not in result.stdout
    assert "provider delete" not in result.stdout
    assert "provider create" not in result.stdout


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


def test_pi_example_uses_standard_checked_in_configuration() -> None:
    project_dir = Path(__file__).parents[1]
    example_dir = project_dir / "examples/pi-attested-admission"
    models = json.loads((example_dir / "models.json").read_text())
    provider = models["providers"]["attested-provider"]
    assert provider["baseUrl"] == "https://inference-api.nvidia.com/v1"
    assert provider["apiKey"] == "openshell-proxy"
    assert "api" not in provider
    configured_models = {model["id"]: model for model in provider["models"]}
    assert set(configured_models) == {
        "azure/anthropic/claude-opus-5",
        "azure/openai/gpt-5.6-sol",
        "nvidia/qwen/qwen3.8-flash-next",
    }

    opus = configured_models["azure/anthropic/claude-opus-5"]
    assert opus["api"] == "openai-completions"
    assert opus["reasoning"] is False
    assert opus["contextWindow"] == 1_000_000
    assert opus["maxTokens"] == 128_000

    gpt = configured_models["azure/openai/gpt-5.6-sol"]
    assert gpt["api"] == "openai-responses"
    assert gpt["reasoning"] is True
    assert gpt["contextWindow"] == 1_050_000
    assert gpt["maxTokens"] == 128_000

    qwen = configured_models["nvidia/qwen/qwen3.8-flash-next"]
    assert qwen["api"] == "openai-completions"
    assert qwen["reasoning"] is True
    assert qwen["contextWindow"] == 262_144
    assert qwen["maxTokens"] == 32_768
    assert qwen["compat"] == {
        "maxTokensField": "max_tokens",
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": True,
        "thinkingFormat": "qwen",
    }

    settings = json.loads((example_dir / "settings.json").read_text())
    assert settings == {
        "defaultProvider": "attested-provider",
        "defaultModel": "nvidia/qwen/qwen3.8-flash-next",
        "defaultThinkingLevel": "high",
    }

    provider_profile = yaml.safe_load(
        (example_dir / "provider-profile.yaml").read_text()
    )
    assert provider_profile["id"] == "pi-attested-model"
    assert provider_profile["credentials"][0]["env_vars"] == ["PI_MODEL_API_KEY"]
    assert provider_profile["credentials"][0]["delivery"] == "proxy"
    assert provider_profile["endpoints"][0]["host"] == "inference-api.nvidia.com"
    assert provider_profile["endpoints"][0]["port"] == 443

    policy = yaml.safe_load((example_dir / "policy.yaml").read_text())
    endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    assert endpoint["host"] == "inference-api.nvidia.com"
    assert endpoint["port"] == 443
    middleware = policy["network_middlewares"]["pi_egress_gate"]
    assert middleware["endpoints"]["include"] == ["inference-api.nvidia.com"]
    assert set(policy["network_policies"]) == {"model_provider"}

    sandbox_dockerfile = (example_dir / "sandbox/Dockerfile").read_text()
    assert "openshell-community/sandboxes/pi:latest" in sandbox_dockerfile
    assert "fd-find ripgrep" in sandbox_dockerfile

    gateway_template = (example_dir / "gateway-middleware.toml.example").read_text()
    gateway_fragment = tomllib.loads(
        gateway_template.replace("YOUR_HOST_IPV4", "192.0.2.10")
    )
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
            "PI_MODELS_PATH",
            "PI_MODEL_API_KEY",
            "PI_WORKSPACE_PATH",
        }
    }

    result = subprocess.run(
        ["bash", str(script), "reset"],
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "The Pi attested-admission example is not configured." in result.stderr
    assert "EGRESS_GATE_HOST_IP" in result.stderr
    assert "PI_MODELS_PATH" in result.stderr
    assert "PI_MODEL_API_KEY" in result.stderr
    assert "PI_WORKSPACE_PATH" in result.stderr
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
            "PI_MODELS_PATH": str(
                project_dir / "examples/pi-attested-admission/models.json"
            ),
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
