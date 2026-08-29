"""Configuration loaded by the OpenShell Tool Service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _log_level(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip().upper()
    if value not in LOG_LEVELS:
        choices = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


@dataclass(frozen=True)
class Settings:
    """Trusted configuration for the generic worker execution envelope."""

    token: str
    database_path: Path
    workspace: str = "default"
    gateway: str | None = None
    gateway_endpoint: str | None = None
    gateway_insecure: bool = False
    openshell_bin: str = "openshell"
    child_image: str = "pi"
    child_provider: str | None = None
    child_models_file: Path | None = None
    child_workdir: str = "/sandbox"
    pi_provider: str = "openshell-inference"
    pi_model: str = "azure/openai/gpt-5.6-sol"
    create_timeout_seconds: int = 300
    job_timeout_seconds: int = 300
    delete_timeout_seconds: int = 60
    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "INFO"
    policy_review_base_url: str = "https://inference-api.nvidia.com/v1"
    policy_review_api_key: str = field(default="", repr=False)
    policy_review_model: str = "azure/openai/gpt-5.6-sol"
    policy_review_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> Settings:
        """Read the service configuration from environment variables."""

        token = os.environ.get("OPENSHELL_TOOL_SERVICE_TOKEN", "").strip()
        if not token:
            raise ValueError("OPENSHELL_TOOL_SERVICE_TOKEN is required")

        gateway = _optional("OPENSHELL_GATEWAY")
        gateway_endpoint = _optional("OPENSHELL_GATEWAY_ENDPOINT")
        if gateway and gateway_endpoint:
            raise ValueError("set only one of OPENSHELL_GATEWAY or OPENSHELL_GATEWAY_ENDPOINT")
        policy_review_api_key = _optional("NVIDIA_API_KEY")
        if not policy_review_api_key:
            raise ValueError("NVIDIA_API_KEY is required")

        return cls(
            token=token,
            database_path=Path(
                os.environ.get("OPENSHELL_TOOL_SERVICE_DATABASE", ".state/jobs.sqlite3")
            ),
            workspace=os.environ.get("OPENSHELL_WORKSPACE", "default"),
            gateway=gateway,
            gateway_endpoint=gateway_endpoint,
            gateway_insecure=os.environ.get("OPENSHELL_GATEWAY_INSECURE", "").lower()
            in {"1", "true", "yes"},
            openshell_bin=os.environ.get("OPENSHELL_BIN", "openshell"),
            child_image=os.environ.get("OPENSHELL_CHILD_IMAGE", "pi"),
            child_provider=_optional("OPENSHELL_CHILD_PROVIDER"),
            child_models_file=(
                Path(value) if (value := _optional("OPENSHELL_CHILD_MODELS_FILE")) else None
            ),
            child_workdir=os.environ.get("OPENSHELL_CHILD_WORKDIR", "/sandbox"),
            pi_provider=os.environ.get("PI_PROVIDER", "openshell-inference"),
            pi_model=os.environ.get("PI_MODEL", "azure/openai/gpt-5.6-sol"),
            create_timeout_seconds=_positive_int("OPENSHELL_CREATE_TIMEOUT_SECONDS", 300),
            job_timeout_seconds=_positive_int("OPENSHELL_JOB_TIMEOUT_SECONDS", 300),
            delete_timeout_seconds=_positive_int("OPENSHELL_DELETE_TIMEOUT_SECONDS", 60),
            host=os.environ.get("OPENSHELL_TOOL_SERVICE_HOST", "0.0.0.0"),
            port=_positive_int("OPENSHELL_TOOL_SERVICE_PORT", 8765),
            log_level=_log_level("OPENSHELL_TOOL_SERVICE_LOG_LEVEL", "INFO"),
            policy_review_base_url=os.environ.get(
                "OPENSHELL_POLICY_REVIEW_BASE_URL",
                "https://inference-api.nvidia.com/v1",
            ).rstrip("/"),
            policy_review_api_key=policy_review_api_key,
            policy_review_model=os.environ.get(
                "OPENSHELL_POLICY_REVIEW_MODEL", "azure/openai/gpt-5.6-sol"
            ),
            policy_review_timeout_seconds=_positive_int(
                "OPENSHELL_POLICY_REVIEW_TIMEOUT_SECONDS", 120
            ),
        )
