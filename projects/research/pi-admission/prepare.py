# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare the image, model profile, and built-in network-redaction policy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import yaml


def prepare(
    example: Path,
    state: Path,
    model_selection: str = "",
) -> None:
    """Stage public configuration; provider credentials remain host-owned."""
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
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    model_path = target.path.rstrip("/") + "/chat/completions"
    policy = yaml.safe_load((example / "policy.yaml").read_text())
    model_endpoint = policy["network_policies"]["model_provider"]["endpoints"][0]
    model_endpoint.update(host=target.hostname, port=target.port or 443)
    model_endpoint["rules"][0]["allow"]["path"] = model_path
    policy["network_middlewares"]["network_redaction"]["endpoints"]["include"] = [
        target.hostname
    ]
    (state / "policy.yaml").write_text(yaml.safe_dump(policy, sort_keys=False))
    profile = {
        "id": "pi-admission-model",
        "display_name": "Pi example model",
        "category": "inference",
        "credentials": [
            {"name": "token", "env_vars": ["PI_MODEL_API_KEY"], "required": True}
        ],
        "discovery": {"credentials": ["token"]},
        "endpoints": [
            {
                "host": target.hostname,
                "port": target.port or 443,
                "protocol": "rest",
                "access": "read-write",
                "enforcement": "enforce",
            }
        ],
        "binaries": ["/usr/local/bin/node"],
    }
    (state / "model-provider.yaml").write_text(yaml.safe_dump(profile))
    image = state / "image"
    if image.exists():
        shutil.rmtree(image)
    image.mkdir()
    shutil.copytree(example / "pi-harness/src", image / "pi-harness/src")
    for name in ("package.json", "package-lock.json", "tsconfig.json"):
        shutil.copyfile(example / "pi-harness" / name, image / "pi-harness" / name)
    (image / "models.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (image / "model-selection.json").write_text(json.dumps(selection) + "\n")
    shutil.copyfile(example / "sandbox/Dockerfile", image / "Dockerfile")
    shutil.copytree(example / "workspace", image / "workspace")
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
    if provider.get("oauth"):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    prepare(
        Path(__file__).resolve().parent,
        args.state.resolve(),
        args.model,
    )
