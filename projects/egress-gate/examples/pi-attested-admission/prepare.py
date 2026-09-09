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


def prepare(example: Path, state: Path, host_ip: str) -> None:
    """Keep keys outside the image; copy only the public CA and explicit demo files."""
    ipaddress.IPv4Address(host_ip)
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    tls = state / "tls"
    if not tls.exists():
        _create_certificates(tls, host_ip)
    elif (state / "host-ip").read_text() != host_ip:
        raise ValueError(
            "Host IP changed: clean up and move aside the demo state first"
        )
    (state / "host-ip").write_text(host_ip)
    model = json.loads((example / "model.json").read_text())
    target = urlparse(model["baseUrl"])
    if target.scheme != "https" or not target.hostname or target.username:
        raise ValueError("The model must use an HTTPS endpoint without credentials")
    if target.hostname == "host.openshell.internal":
        raise ValueError("Model and admission endpoints must be separate")
    model_path = target.path.rstrip("/") + "/chat/completions"
    policy = yaml.safe_load((example / "policy.yaml").read_text())
    model_endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    model_endpoint.update(host=target.hostname, port=target.port or 443)
    model_endpoint["rules"][0]["allow"]["path"] = model_path
    binding = policy["network_middlewares"]["pi_egress_gate"]
    binding["endpoints"]["include"] = [target.hostname]
    (state / "policy.yaml").write_text(yaml.safe_dump(policy, sort_keys=False))
    for name, host, port, variable in [
        ("model", target.hostname, target.port or 443, "PI_MODEL_API_KEY"),
        ("admission", "host.openshell.internal", 5443, "EGRESS_ADMISSION_TOKEN"),
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
                    "host": host,
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
        "gateway_public_key": str(tls / "jwt/public.pem"),
        "gateway_issuer": "openshell-gateway:openshell",
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
    gateway = f"""[openshell]
version = 1
[openshell.gateway]
name = "pi-admission"
compute_drivers = ["docker"]
[openshell.drivers.docker]
supervisor_bin = {quote(str(state / "bin/openshell-sandbox"))}
[[openshell.supervisor.middleware]]
name = "pi-egress"
grpc_endpoint = "https://{host_ip}:50051"
tls_ca_cert_path = {quote(str(tls / "ca.crt"))}
audience = "{audience}"
max_payload_bytes = 4194304
timeout = "10s"
"""
    (state / "gateway.toml").write_text(gateway)
    client = state / "config/openshell/gateways/pi-admission/mtls"
    client.mkdir(parents=True, exist_ok=True)
    for source, name in [
        (tls / "ca.crt", "ca.crt"),
        (tls / "client/tls.crt", "tls.crt"),
        (tls / "client/tls.key", "tls.key"),
    ]:
        shutil.copyfile(source, client / name)
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


def _create_certificates(tls: Path, host_ip: str) -> None:
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
    tls.mkdir()
    (tls / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # The CA key is not needed again; each setup has a 30-day local trust bundle.
    for role in ("server", "client"):
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"pi-{role}")])
            )
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH,
                        ExtendedKeyUsageOID.CLIENT_AUTH,
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName("host.openshell.internal"),
                        x509.DNSName("host.docker.internal"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.IPAddress(ipaddress.ip_address(host_ip)),
                    ]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        directory = tls / role
        directory.mkdir()
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
    jwt_key = ed25519.Ed25519PrivateKey.generate()
    (tls / "jwt").mkdir()
    (tls / "jwt/signing.pem").write_bytes(
        jwt_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (tls / "jwt/public.pem").write_bytes(
        jwt_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    (tls / "jwt/kid").write_text(secrets.token_hex(16))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--host-ip", required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parent, args.state.resolve(), args.host_ip)
