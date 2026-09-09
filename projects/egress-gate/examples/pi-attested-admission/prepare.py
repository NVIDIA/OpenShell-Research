# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare one host-owned demo configuration. Never run inside the sandbox."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def prepare(
    example: Path, state: Path, host: str, gateway_public_key: Path, gateway_issuer: str
) -> None:
    """Keep keys outside the image; copy only the public CA and explicit demo files."""
    endpoint = urlparse(f"https://{host}:5443")
    if endpoint.hostname != host or endpoint.port != 5443 or endpoint.path:
        raise ValueError("Use a DNS hostname or IPv4 address, without a URL or port")
    public_key = serialization.load_pem_public_key(gateway_public_key.read_bytes())
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise ValueError("Provide the gateway's Ed25519 public signing key")
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    tls = state / "tls"
    certificate = tls / "server/tls.crt"
    if (
        not certificate.exists()
        or (state / "service-host").read_text() != host
        or x509.load_pem_x509_certificate(certificate.read_bytes()).not_valid_after_utc
        <= datetime.now(UTC)
    ):
        _create_certificates(tls, host)
        print("Service TLS created: install tls/ca.crt in the gateway's trust config.")
    (state / "service-host").write_text(host)
    model = json.loads((example / "model.json").read_text())
    target = urlparse(model["baseUrl"])
    if target.scheme != "https" or not target.hostname or target.username:
        raise ValueError("The model must use an HTTPS endpoint without credentials")
    if target.hostname == host:
        raise ValueError("Model and admission endpoints must be separate")
    model_path = target.path.rstrip("/") + "/chat/completions"
    policy = yaml.safe_load((example / "policy.yaml").read_text())
    model_endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    model_endpoint.update(host=target.hostname, port=target.port or 443)
    model_endpoint["rules"][0]["allow"]["path"] = model_path
    policy["network_policies"]["admission"]["endpoints"][0]["host"] = host
    binding = policy["network_middlewares"]["pi_egress_gate"]
    binding["endpoints"]["include"] = [target.hostname]
    (state / "policy.yaml").write_text(yaml.safe_dump(policy, sort_keys=False))
    for name, provider_host, port, variable in [
        ("model", target.hostname, target.port or 443, "PI_MODEL_API_KEY"),
        ("admission", host, 5443, "EGRESS_ADMISSION_TOKEN"),
    ]:
        profile = {
            "id": f"pi-admission-{name}",
            "display_name": f"Pi example {name}",
            "category": "inference" if name == "model" else "other",
            "credentials": [
                {"name": "token", "env_vars": [variable], "required": True}
            ],
            "discovery": {"credentials": ["token"]},
            "endpoints": [
                {
                    "host": provider_host,
                    "port": port,
                    "protocol": "rest",
                    "access": "read-write",
                    "enforcement": "enforce",
                }
            ],
            "binaries": ["/usr/local/bin/node", "/usr/bin/curl"],
        }
        (state / f"{name}-provider.yaml").write_text(yaml.safe_dump(profile))
    config_path = state / "admission.json"
    token = (
        json.loads(config_path.read_text())["bearer_token"]
        if config_path.exists()
        else secrets.token_urlsafe(32)
    )
    audience = "urn:openshell:extension:middleware:pi-egress"
    config = {
        "listen": "0.0.0.0:5443",
        "tls_certificate": str(tls / "server/tls.crt"),
        "tls_private_key": str(tls / "server/tls.key"),
        "gateway_public_key": str(gateway_public_key.resolve()),
        "gateway_issuer": gateway_issuer,
        "gateway_audience": audience,
        "middleware_name": "pi-egress",
        "bearer_token": token,
        "sandbox_id_file": str(state / "sandbox-id"),
        "provider_target": {
            "scheme": "https",
            "host": target.hostname,
            "port": target.port or 443,
            "method": "POST",
            "path": model_path,
            "query": "",
        },
        "policy": binding["config"],
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    # JSON string quoting is also valid for these TOML basic string values.
    quote = json.dumps
    registration = f"""[[openshell.supervisor.middleware]]
name = "pi-egress"
grpc_endpoint = "https://{host}:50051"
tls_ca_cert_path = {quote(str(tls / "ca.crt"))}
audience = "{audience}"
max_payload_bytes = 4194304
timeout = "10s"
"""
    (state / "middleware.toml").write_text(registration)
    image = state / "image"
    # Recreate only this generated build context, so removed source/config files
    # cannot survive a subsequent prepare. Host keys and runtime state stay put.
    if image.exists():
        shutil.rmtree(image)
    image.mkdir()
    for directory in ("app/src", "app/test"):
        shutil.copytree(example / directory, image / directory, dirs_exist_ok=True)
    for name in ("package.json", "package-lock.json", "tsconfig.json"):
        shutil.copyfile(example / "app" / name, image / "app" / name)
    # This is one explicit project, not a recursive upload of the operator's cwd.
    for name in ("AGENTS.md", "notes.txt", ".pi/skills/review/SKILL.md"):
        destination = image / "project" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example / "project" / name, destination)
    shutil.copyfile(example / "model.json", image / "model.json")
    shutil.copyfile(example / "sandbox/Dockerfile", image / "Dockerfile")
    shutil.copyfile(tls / "ca.crt", image / "admission-ca.crt")


def _create_certificates(tls: Path, host: str) -> None:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Pi admission demo CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    tls.mkdir(exist_ok=True)
    (tls / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # The CA key is not needed again; each setup has a 30-day local trust bundle.
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pi-admission-service")])
        )
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), _service_name(host)]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    directory = tls / "server"
    directory.mkdir(exist_ok=True)
    (directory / "tls.crt").write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "tls.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _service_name(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--gateway-public-key", type=Path, required=True)
    parser.add_argument("--gateway-issuer", required=True)
    args = parser.parse_args()
    prepare(
        Path(__file__).resolve().parent,
        args.state.resolve(),
        args.host,
        args.gateway_public_key.resolve(),
        args.gateway_issuer,
    )
