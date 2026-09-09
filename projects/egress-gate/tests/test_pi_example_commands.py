# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from egress_gate.service.admission import AdmissionServerConfig

PROJECT = Path(__file__).parents[1]
EXAMPLE = PROJECT / "examples/pi-attested-admission"


@pytest.fixture
def gateway_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[dict[str, str], bytes]]:
    """Real mTLS with OpenShell-style certs (no AKI or CA Key Usage extension)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    tls = tmp_path / "config/openshell/gateways/test-gateway/mtls"
    tls.mkdir(parents=True)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test CA")])
    now = datetime.now(UTC)
    for name in ("ca", "tls"):
        key = ca_key if name == "ca" else ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                ca_name
                if name == "ca"
                else x509.Name(
                    [x509.NameAttribute(x509.NameOID.COMMON_NAME, "test-gateway")]
                )
            )
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=name == "ca", path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (tls / f"{name}.crt").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        if name == "tls":
            (tls / "tls.key").write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
    signing_key = Ed25519PrivateKey.generate().public_key()
    gateway = {"name": "test-gateway", "auth": "mtls"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/.well-known/openid-configuration":
                body = {
                    "issuer": "existing-gateway-issuer",
                    "jwks_uri": gateway.get(
                        "jwks_uri", gateway["endpoint"] + "/.well-known/jwks.json"
                    ),
                }
            else:
                assert self.path == "/.well-known/jwks.json"
                body = {"keys": [json.loads(OKPAlgorithm.to_jwk(signing_key))]}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls / "tls.crt", tls / "tls.key")
    context.load_verify_locations(tls / "ca.crt")
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    gateway["endpoint"] = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            gateway,
            signing_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
    assert "gateway list --output json" in output
    assert "--gateway-public-key" not in output and "--gateway-issuer" not in output
    assert "17672" not in output and "XDG_CONFIG_HOME" not in output
    assert "docker build" in output
    assert "--admission-config" in output
    assert "provider create" in output
    assert "--credential PI_MODEL_API_KEY" in output
    assert "--credential EGRESS_ADMISSION_TOKEN" in output
    assert "sandbox create" in output and "--from pi-admission:local" in output
    assert "/app/dist/src/cli.js" in output and "/app/dist/src/verify.js" in output
    assert "sandbox delete pi-admission" in output


def test_prepare_requires_operator_model_configuration(tmp_path: Path) -> None:
    script = tmp_path / "demo.sh"
    shutil.copyfile(EXAMPLE / "demo.sh", script)
    result = subprocess.run(
        ["bash", str(script), "prepare"],
        env=os.environ | {"OPENSHELL_GATEWAY": "test", "EGRESS_GATE_HOST": "localhost"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Create model.json from model.json.example" in result.stderr
    assert not (tmp_path / "model.json").exists()


@pytest.mark.parametrize("host", ["192.0.2.10", "host.docker.internal"])
def test_preparation_uses_existing_gateway_and_excludes_private_material(
    tmp_path: Path,
    host: str,
    gateway_discovery: tuple[dict[str, str], bytes],
) -> None:
    state = tmp_path / "state"
    example = tmp_path / "example"
    shutil.copytree(
        EXAMPLE,
        example,
        ignore=shutil.ignore_patterns(".env", "model.json", "node_modules", "dist"),
    )
    shutil.copyfile(example / "model.json.example", example / "model.json")
    gateway, public = gateway_discovery
    public_path = state / "gateway-public.pem"
    command = [
        sys.executable,
        str(example / "prepare.py"),
        "--state",
        str(state),
        "--host",
        host,
        "--gateway",
        gateway["name"],
    ]
    subprocess.run(command, input=json.dumps([gateway]), text=True, check=True)
    config = AdmissionServerConfig.model_validate_json(
        (state / "admission.json").read_bytes()
    )
    assert config.provider_target.scheme == "https"
    assert config.provider_target.host == "api.example.com"
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
    subprocess.run(command, input=json.dumps([gateway]), text=True, check=True)
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
    assert json.loads((image / "model.json").read_text())["id"] == "YOUR_MODEL_ID"
    middleware = tomllib.loads((state / "middleware.toml").read_text())
    registration = middleware["openshell"]["supervisor"]["middleware"][0]
    assert registration["grpc_endpoint"] == f"https://{host}:50051"
    assert registration["tls_ca_cert_path"] == str(state / "tls/ca.crt")
    assert "gateway" not in middleware["openshell"]
    assert "drivers" not in middleware["openshell"]
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
    subprocess.run(command, input=json.dumps([gateway]), text=True, check=True)
    assert (state / "tls/server/tls.crt").read_bytes() != certificate_bytes
    assert public_path.read_bytes() == public


@pytest.mark.parametrize(
    "failure", ["plaintext", "foreign-key-url", "untrusted-ca", "wrong-hostname"]
)
def test_discovery_rejects_untrusted_gateway_before_preparation(
    tmp_path: Path, gateway_discovery: tuple[dict[str, str], bytes], failure: str
) -> None:
    gateway, _ = gateway_discovery
    if failure == "plaintext":
        gateway["endpoint"] = gateway["endpoint"].replace("https:", "http:")
    elif failure == "foreign-key-url":
        gateway["jwks_uri"] = "https://untrusted.example/keys"
    elif failure == "wrong-hostname":
        gateway["endpoint"] = gateway["endpoint"].replace("127.0.0.1", "localhost")
    else:
        # Keep the client identity, but remove its trust in the server's CA.
        key = Ed25519PrivateKey.generate()
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Wrong CA")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, None)
        )
        (tmp_path / "config/openshell/gateways/test-gateway/mtls/ca.crt").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "prepare.py"),
            "--state",
            str(tmp_path / "state"),
            "--host",
            "127.0.0.1",
            "--gateway",
            gateway["name"],
        ],
        input=json.dumps([gateway]),
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert {
        "plaintext": "registered HTTPS/mTLS gateway",
        "foreign-key-url": "same HTTPS origin",
        "untrusted-ca": "CERTIFICATE_VERIFY_FAILED",
        "wrong-hostname": "Hostname mismatch",
    }[failure] in result.stderr
    assert not (tmp_path / "state").exists()


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
