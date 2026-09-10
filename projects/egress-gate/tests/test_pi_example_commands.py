# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ipaddress
import json
import os
import runpy
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


def test_native_model_selection_keeps_only_selected_configuration(
    tmp_path: Path,
) -> None:
    catalog = json.loads((EXAMPLE / "models.json.example").read_text())
    provider = catalog["providers"]["example"]
    provider["apiKey"] = "!do-not-execute-or-copy"
    provider["models"].append(
        {
            "id": "vendor/second",
            "baseUrl": "https://selected.example/custom/v1",
            "api": "openai-completions",
        }
    )
    provider["modelOverrides"] = {"vendor/second": {"maxTokens": 2048}}
    catalog["providers"]["unselected"] = {
        "apiKey": "private",
        "models": [{"id": "third"}],
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(catalog))
    select = runpy.run_path(str(EXAMPLE / "prepare.py"))["select_model"]
    for selection in ("", "example/missing"):
        with pytest.raises(ValueError, match="PI_MODEL"):
            select(path, selection)
    staged, selection, endpoint = select(path, "example/vendor/second")
    assert selection == {"provider": "example", "id": "vendor/second"}
    assert endpoint == "https://selected.example/custom/v1"
    assert staged == {
        "providers": {
            "example": {
                "api": provider["api"],
                "baseUrl": provider["baseUrl"],
                "compat": provider["compat"],
                "models": [provider["models"][1]],
                "modelOverrides": provider["modelOverrides"],
            }
        }
    }


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
        "register",
        "unregister",
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
    assert "gateway-registration.py" in output
    assert (
        "brew services restart openshell"
        if sys.platform == "darwin"
        else "systemctl --user restart openshell-gateway"
    ) in output


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("", 0),
        (
            "code: 'Some requested entity was not found', "
            'message: "sandbox not found"',
            0,
        ),
        ("code: 'Permission denied', message: \"sandbox not found\"", 7),
        (
            "code: 'Some requested entity was not found', "
            'message: "workspace not found"',
            7,
        ),
        ("Connection refused", 7),
    ],
)
def test_cleanup_after_partial_setup(
    tmp_path: Path, failure: str, expected_status: int
) -> None:
    example = tmp_path / "examples/demo"
    example.mkdir(parents=True)
    script = example / "demo.sh"
    shutil.copyfile(EXAMPLE / "demo.sh", script)
    commands = tmp_path / "commands"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # Exercise the real shell flow without deleting a live sandbox or registration.
    stub = f"""#!{sys.executable}
import os, pathlib, sys
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(pathlib.Path(sys.argv[0]).name + " " + " ".join(sys.argv[1:]) + "\\n")
if "sandbox" in sys.argv and os.environ["SANDBOX_FAILURE"]:
    print(os.environ["SANDBOX_FAILURE"], file=sys.stderr)
    sys.exit(7)
"""
    for name in ("openshell", "uv"):
        executable = binaries / name
        executable.write_text(stub)
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "cleanup"],
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "OPENSHELL_GATEWAY": "test-gateway",
            "COMMAND_LOG": str(commands),
            "SANDBOX_FAILURE": failure,
        },
    )
    assert result.returncode == expected_status
    recorded = commands.read_text().splitlines()
    assert recorded[0] == "openshell --gateway test-gateway sandbox delete pi-admission"
    if expected_status:
        assert len(recorded) == 1
        assert failure in result.stderr
        assert "sessions removed" not in result.stdout
    else:
        assert len(recorded) == 7
        assert "provider delete pi-admission-model" in recorded[1]
        assert "provider profile delete pi-admission-admission" in recorded[4]
        assert "gateway-registration.py unregister" in recorded[-1]
        if failure:
            assert "already absent; continuing cleanup" in result.stdout


@pytest.mark.parametrize(
    "installation", ["homebrew-prefix", "homebrew-user", "systemd", "systemd-defaults"]
)
def test_installer_registration_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installation: str
) -> None:
    configure = runpy.run_path(str(EXAMPLE / "gateway-registration.py"))["configure"]
    homebrew = installation.startswith("homebrew")
    monkeypatch.setattr(sys, "platform", "darwin" if homebrew else "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OPENSHELL_GATEWAY_CONFIG", raising=False)
    prefix_config = tmp_path / "brew/var/openshell/gateway.toml"
    prefix_config.parent.mkdir(parents=True)
    original = (
        "# Keep my comments\n[openshell]\nversion = 1\n"
        '[openshell.gateway]\nbind_address = "127.0.0.1:17670"\n'
        '[[openshell.supervisor.middleware]]\nname = "other"\n'
        'grpc_endpoint = "http://localhost:1234"\n'
    )
    prefix_config.write_text(original)
    config = prefix_config
    if installation != "homebrew-prefix":
        config = tmp_path / "config/openshell/gateway.toml"
        config.parent.mkdir(parents=True)
        config.write_text(original)
    if installation == "systemd-defaults":
        config.unlink()
        original = "[openshell]\nversion = 1\n"
    state = tmp_path / "state"
    state.mkdir()
    fragment = (
        '[[openshell.supervisor.middleware]]\nname = "pi-egress"\n'
        'grpc_endpoint = "https://service.example:50051"\n'
        'tls_ca_cert_path = "/demo/tls/ca.crt"\n'
        'audience = "urn:openshell:extension:middleware:pi-egress"\n'
        'max_payload_bytes = 4194304\ntimeout = "10s"\n'
    )
    (state / "middleware.toml").write_text(fragment)
    commands: list[tuple[str, ...]] = []
    endpoint = "https://localhost:17670"
    fail_restart = False
    restart = (
        ["brew", "services", "restart", "openshell"]
        if homebrew
        else ["systemctl", "--user", "restart", "openshell-gateway"]
    )

    def output(command: tuple[str, ...], **_kwargs: object) -> str:
        commands.append(command)
        if command == ("brew", "--prefix"):
            assert homebrew
            return str(tmp_path / "brew")
        if command == (
            "systemctl",
            "--user",
            "show",
            "openshell-gateway",
            "--property=LoadState",
            "--value",
        ):
            assert not homebrew
            return "loaded\n"
        assert command == ("openshell", "gateway", "list", "--output", "json")
        return json.dumps([{"name": "openshell", "endpoint": endpoint}])

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        if command == restart:
            if fail_restart:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        assert command == [
            "openshell",
            "--gateway",
            "openshell",
            "gateway",
            "info",
            "--output",
            "json",
        ]
        return subprocess.CompletedProcess(command, 0, '{"status":"healthy"}')

    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", run)
    # Never modify a local service when the selected gateway is remote.
    endpoint = "https://remote.example:17670"
    with pytest.raises(ValueError, match="local installer-managed gateway"):
        configure("register", state, "openshell")
    if installation == "systemd-defaults":
        assert not config.exists()
    else:
        assert config.read_text() == original
    endpoint = "https://localhost:17670"
    # Refuse to take over an operator's pre-existing registration, even if identical.
    config.write_text(original + fragment)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        configure("register", state, "openshell")
    config.write_text(original)
    if installation == "systemd-defaults":
        config.unlink()
    fail_restart = True
    with pytest.raises(subprocess.CalledProcessError):
        configure("register", state, "openshell")
    assert (state / "gateway-registration.json").exists()
    fail_restart = False
    configure("register", state, "openshell")
    registered = config.read_text()
    assert registered.startswith(original)
    assert registered.count('name = "pi-egress"') == 1
    assert (
        tomllib.loads(registered)["openshell"]["supervisor"]["middleware"][-1]
        == (tomllib.loads(fragment)["openshell"]["supervisor"]["middleware"][0])
    )
    fail_restart = True
    with pytest.raises(subprocess.CalledProcessError):
        configure("unregister", state, "openshell")
    assert (state / "gateway-registration.json").exists()
    fail_restart = False
    configure("unregister", state, "openshell")
    assert config.read_text().strip() == original.strip()
    assert not (state / "gateway-registration.json").exists()
    before = len(commands)
    configure("unregister", state, "openshell")
    assert len(commands) == before
    if installation == "homebrew-user":
        assert prefix_config.read_text() == original


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
    assert "Create models.json from models.json.example" in result.stderr
    assert not (tmp_path / "models.json").exists()


def test_image_suppresses_only_the_proxy_agent_warning() -> None:
    dockerfile = (EXAMPLE / "sandbox/Dockerfile").read_text()
    options = next(
        line.removeprefix("ENV NODE_OPTIONS=").strip('"')
        for line in dockerfile.splitlines()
        if line.startswith("ENV NODE_OPTIONS=")
    )
    result = subprocess.run(
        [
            "node",
            "-e",
            "process.emitWarning('proxy notice', {code: 'UNDICI-EHPA'});"
            "process.emitWarning('unrelated notice', {code: 'OTHER_WARNING'});",
        ],
        env=os.environ | {"NODE_OPTIONS": options},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "UNDICI-EHPA" not in result.stderr
    assert "proxy notice" not in result.stderr
    assert "OTHER_WARNING" in result.stderr
    assert "unrelated notice" in result.stderr


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
        ignore=shutil.ignore_patterns(
            ".env", "model.json", "models.json", "node_modules", "dist"
        ),
    )
    catalog = json.loads((example / "models.json.example").read_text())
    provider = catalog["providers"]["example"]
    provider["apiKey"] = "must-not-enter-image"
    provider["models"][0]["baseUrl"] = provider["baseUrl"]
    provider["baseUrl"] = "https://unselected.example/v1"
    provider["models"].append({"id": "unselected"})
    (example / "models.json").write_text(json.dumps(catalog))
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
        "--model",
        "example/YOUR_MODEL_ID",
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
    catalog = json.loads((image / "models.json").read_text())
    assert catalog["providers"]["example"]["models"][0]["id"] == "YOUR_MODEL_ID"
    assert len(catalog["providers"]["example"]["models"]) == 1
    assert "must-not-enter-image" not in (image / "models.json").read_text()
    assert json.loads((image / "model-selection.json").read_text()) == {
        "provider": "example",
        "id": "YOUR_MODEL_ID",
    }
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
