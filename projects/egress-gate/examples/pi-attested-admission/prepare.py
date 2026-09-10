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
import ssl
import sys
from datetime import UTC, datetime, timedelta
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse

import jwt
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def prepare(
    example: Path,
    state: Path,
    host: str,
    gateway_public_key: Path,
    gateway_issuer: str,
    model_selection: str = "",
) -> None:
    """Keep keys outside the image; copy only the public CA and explicit demo files."""
    endpoint = urlparse(f"https://{host}:5443")
    if endpoint.hostname != host or endpoint.port != 5443 or endpoint.path:
        raise ValueError("Use a DNS hostname or IPv4 address, without a URL or port")
    catalog, selection, base_url = select_model(
        example / "models.json", model_selection
    )
    target = urlparse(base_url)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username
        or target.query
        or target.fragment
    ):
        raise ValueError(
            "The model must use an HTTPS endpoint without credentials or query"
        )
    if target.hostname == host:
        raise ValueError("Model and admission endpoints must be separate")
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
    (image / "models.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (image / "model-selection.json").write_text(json.dumps(selection) + "\n")
    shutil.copyfile(example / "sandbox/Dockerfile", image / "Dockerfile")
    shutil.copyfile(tls / "ca.crt", image / "admission-ca.crt")
    print(f"Selected model: {selection['provider']}/{selection['id']}")


def select_model(
    path: Path, requested: str
) -> tuple[dict[str, object], dict[str, str], str]:
    """Stage one native Pi model; provider credentials remain owned by OpenShell."""
    providers = json.loads(path.read_text()).get("providers")
    if not isinstance(providers, dict):
        raise ValueError(
            "Use Pi's native models.json providers catalog; see models.json.example"
        )
    choices = [
        (provider_id, provider, model)
        for provider_id, provider in providers.items()
        for model in provider.get("models", [])
        if not requested or f"{provider_id}/{model['id']}" == requested
    ]
    if len(choices) != 1:
        raise ValueError(
            "Set PI_MODEL=provider/model to select exactly one declared model"
        )
    provider_id, provider, model = choices[0]
    overrides = provider.get("modelOverrides", {}).get(model["id"], {})
    if any(config.get("headers") for config in (provider, model, overrides)):
        raise ValueError("Custom model headers are unsupported; use PI_MODEL_API_KEY")
    if provider.get("oauth") or provider.get("authHeader"):
        raise ValueError(
            "Custom provider authentication is unsupported; use PI_MODEL_API_KEY"
        )
    if model.get("api", provider.get("api")) != "openai-completions":
        raise ValueError("Select an openai-completions model for this example")
    base_url = model.get("baseUrl", provider.get("baseUrl"))
    if not isinstance(base_url, str):
        raise ValueError("Declare the selected model's baseUrl in models.json")
    selected_provider = {
        key: provider[key] for key in ("api", "baseUrl", "compat") if key in provider
    }
    selected_provider["models"] = [model]
    if overrides:
        selected_provider["modelOverrides"] = {model["id"]: overrides}
    return (
        {"providers": {provider_id: selected_provider}},
        {"provider": provider_id, "id": model["id"]},
        base_url,
    )


def _discover_gateway(gateway: dict[str, str]) -> tuple[bytes, str]:
    """Use the CLI's registered endpoint and existing client TLS, never new keys."""
    endpoint = urlparse(gateway["endpoint"])
    name = gateway["name"]
    if endpoint.scheme != "https" or not endpoint.hostname or gateway["auth"] != "mtls":
        raise ValueError("This demo requires a registered HTTPS/mTLS gateway")
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("Invalid gateway name")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    tls = config / "openshell/gateways" / name / "mtls"
    context = ssl.create_default_context(cafile=str(tls / "ca.crt"))
    # OpenShell's generated certificates omit extensions required by Python 3.13's
    # strict X.509 mode. Retain CA/signature, expiry and hostname verification.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    context.load_cert_chain(tls / "tls.crt", tls / "tls.key")
    connection = HTTPSConnection(
        endpoint.hostname, endpoint.port, context=context, timeout=10
    )
    try:
        print(f"Discovering gateway identity from {gateway['endpoint']}")
        connection.request("GET", "/.well-known/openid-configuration")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Gateway discovery returned HTTP {response.status}")
        discovery = json.load(response)
        issuer = discovery["issuer"]
        if not isinstance(issuer, str) or not issuer:
            raise ValueError("Gateway discovery must provide a nonempty issuer")
        jwks = urlparse(discovery["jwks_uri"])
        if (jwks.scheme, jwks.netloc) != (endpoint.scheme, endpoint.netloc):
            raise ValueError(
                "Gateway signing keys must come from the same HTTPS origin"
            )
        connection.request("GET", jwks.path + (f"?{jwks.query}" if jwks.query else ""))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(
                f"Gateway signing-key discovery returned HTTP {response.status}"
            )
        keys = json.load(response)["keys"]
        if len(keys) != 1:
            raise ValueError("This demo expects one gateway signing key")
        key = jwt.PyJWK.from_dict(keys[0]).key
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise ValueError("Gateway must publish an Ed25519 public signing key")
        return key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ), issuer
    finally:
        connection.close()


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
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    gateways = json.load(sys.stdin)
    gateway = next((item for item in gateways if item["name"] == args.gateway), None)
    if gateway is None:
        parser.error("Gateway is not registered; use openshell gateway add first")
    public_key, issuer = _discover_gateway(gateway)
    os.umask(0o077)
    args.state.mkdir(parents=True, exist_ok=True)
    public_path = args.state.resolve() / "gateway-public.pem"
    public_path.write_bytes(public_key)
    prepare(
        Path(__file__).resolve().parent,
        args.state.resolve(),
        args.host,
        public_path,
        issuer,
        args.model,
    )
