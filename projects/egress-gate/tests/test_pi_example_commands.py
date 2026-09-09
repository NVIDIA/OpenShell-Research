# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from egress_gate.service.admission import AdmissionServerConfig

PROJECT = Path(__file__).parents[1]
EXAMPLE = PROJECT / "examples/pi-attested-admission"


def test_every_action_prints_without_secrets_or_side_effects(tmp_path: Path) -> None:
    example = tmp_path / "examples/demo"
    example.mkdir(parents=True)
    script = example / "demo.sh"
    shutil.copyfile(EXAMPLE / "demo.sh", script)
    marker = tmp_path / "must-not-exist"
    (example / ".env").write_text(
        f"touch {marker}\nPI_MODEL_API_KEY=PRIVATE_TEST_VALUE\n"
    )
    output = ""
    for action in (
        "prepare",
        "serve",
        "registration",
        "setup",
        "launch",
        "verify",
        "cleanup",
    ):
        result = subprocess.run(
            ["bash", str(script), "--print", action],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "PI_MODEL_API_KEY": "PRIVATE_TEST_VALUE",
                "OPENSHELL_GATEWAY": "test-gateway",
                "EGRESS_GATE_HOST": "service.example",
            },
        )
        output += result.stdout
        assert result.stderr == ""
    assert "PRIVATE_TEST_VALUE" not in output
    assert not marker.exists()
    assert not (tmp_path / ".workspaces").exists()
    assert "git clone" not in output
    assert "openshell-gateway" not in output
    assert "sha256sum" not in output and "curl" not in output
    assert "--gateway test-gateway" in output
    assert "https://service.example:5443/v1/admission" in output
    assert "middleware.toml" in output
    assert "17672" not in output and "XDG_CONFIG_HOME" not in output
    assert "docker build" in output
    assert "--admission-config" in output
    assert "provider create" in output
    assert "--credential PI_MODEL_API_KEY" in output
    assert "--credential EGRESS_ADMISSION_TOKEN" in output
    assert "sandbox create" in output and "--from pi-admission:local" in output
    assert "/app/dist/src/cli.js" in output and "/app/dist/src/verify.js" in output
    assert "sandbox delete pi-admission" in output


@pytest.mark.parametrize("host", ["192.0.2.10", "host.docker.internal"])
def test_preparation_uses_existing_gateway_and_excludes_private_material(
    tmp_path: Path,
    host: str,
) -> None:
    state = tmp_path / "state"
    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    public_path = tmp_path / "gateway-public.pem"
    public_path.write_bytes(public)
    command = [
        sys.executable,
        str(EXAMPLE / "prepare.py"),
        "--state",
        str(state),
        "--host",
        host,
        "--gateway-public-key",
        str(public_path),
        "--gateway-issuer",
        "existing-gateway-issuer",
    ]
    subprocess.run(command, check=True)
    config = AdmissionServerConfig.model_validate_json(
        (state / "admission.json").read_bytes()
    )
    assert config.provider_target.scheme == "https"
    assert config.gateway_public_key == public_path
    assert config.gateway_issuer == "existing-gateway-issuer"
    assert public_path.read_bytes() == public
    assert not (state / "tls/jwt").exists()
    assert not (state / "tls/client").exists()
    assert not (state / "gateway.toml").exists()
    certificate_bytes = (state / "tls/server/tls.crt").read_bytes()
    certificate = x509.load_pem_x509_certificate(certificate_bytes)
    names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert host in [str(name.value) for name in names]
    assert config.provider_target.path == "/v1/chat/completions"
    assert not config.sandbox_id_file.exists()
    token = config.bearer_token.get_secret_value()
    assert len(token) >= 32
    (state / "image/stale-config.json").write_text("{}")
    subprocess.run(command, check=True)
    again = AdmissionServerConfig.model_validate_json(
        (state / "admission.json").read_bytes()
    )
    assert again.bearer_token == config.bearer_token
    assert (state / "tls/server/tls.crt").read_bytes() == certificate_bytes
    for name in ("model", "admission"):
        profile = yaml.safe_load((state / f"{name}-provider.yaml").read_text())
        credential = profile["credentials"][0]
        assert set(credential) == {"name", "env_vars", "required"}
        assert token not in json.dumps(profile)
    image = state / "image"
    assert not (image / "stale-config.json").exists()
    assert not list(image.rglob("*.key"))
    assert not list(image.rglob("*.pem"))
    assert not list(image.rglob(".env"))
    assert not (image / "admission.json").exists()
    assert not (image / "app/node_modules").exists()
    assert (image / "project/.pi/skills/review/SKILL.md").is_file()
    gateway = tomllib.loads((state / "middleware.toml").read_text())
    registration = gateway["openshell"]["supervisor"]["middleware"][0]
    assert registration["grpc_endpoint"] == f"https://{host}:50051"
    assert registration["tls_ca_cert_path"] == str(state / "tls/ca.crt")
    assert "gateway" not in gateway["openshell"]
    assert "drivers" not in gateway["openshell"]
    policy = yaml.safe_load((state / "policy.yaml").read_text())
    assert policy["network_middlewares"]["pi_egress_gate"]["on_error"] == "fail_closed"
    model_endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    assert model_endpoint["rules"] == [
        {"allow": {"method": "POST", "path": "/v1/chat/completions"}}
    ]
    assert "access" not in model_endpoint
    assert policy["network_policies"]["admission"]["endpoints"][0]["port"] == 5443
    assert policy["network_policies"]["admission"]["endpoints"][0]["host"] == host
    admission_profile = yaml.safe_load((state / "admission-provider.yaml").read_text())
    assert admission_profile["endpoints"][0]["host"] == host
    command[command.index("--host") + 1] = "new-service.example"
    subprocess.run(command, check=True)
    assert (state / "tls/server/tls.crt").read_bytes() != certificate_bytes
    assert public_path.read_bytes() == public


def test_sandbox_binding_accepts_only_operator_cli_output(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "bind-sandbox.py"), "--state", str(tmp_path)],
        input=json.dumps({"id": "actual-sandbox-id"}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert (tmp_path / "sandbox-id").read_text().strip() == "actual-sandbox-id"
    assert "bound" in result.stdout


def test_pi_dependencies_are_exact_upstream_packages() -> None:
    package = json.loads((EXAMPLE / "app/package.json").read_text())
    lock = json.loads((EXAMPLE / "app/package-lock.json").read_text())
    for name, version in package["dependencies"].items():
        if name.startswith("@earendil-works/"):
            assert version == "0.85.1"
        resolved = lock["packages"][f"node_modules/{name}"]
        assert resolved["version"] == version
        assert resolved["resolved"].startswith("https://registry.npmjs.org/")
        assert resolved["integrity"].startswith("sha512-")
