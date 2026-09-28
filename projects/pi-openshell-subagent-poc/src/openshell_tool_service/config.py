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
        raise ValueError(f"{name} must be one of: {', '.join(sorted(LOG_LEVELS))}")
    return value


@dataclass(frozen=True)
class Settings:
    """Trusted worker settings that sandbox requests cannot override."""

    token: str = field(repr=False)
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
    max_workers: int = 8
    graceful_shutdown_seconds: int = 2
    log_level: str = "INFO"
    prover_bin: str = ".state/bin/openshell-prover"
    policy_composer_bin: str = ".state/bin/poc-policy-compose"
    policy_review_timeout_seconds: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("OPENSHELL_TOOL_SERVICE_TOKEN", "").strip()
        if not token:
            raise ValueError("OPENSHELL_TOOL_SERVICE_TOKEN is required")

        gateway = _optional("OPENSHELL_GATEWAY")
        gateway_endpoint = _optional("OPENSHELL_GATEWAY_ENDPOINT")
        if gateway and gateway_endpoint:
            raise ValueError("set only one of OPENSHELL_GATEWAY or OPENSHELL_GATEWAY_ENDPOINT")

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
            max_workers=_positive_int("POC_MAX_WORKERS", 8),
            graceful_shutdown_seconds=_positive_int("POC_GRACEFUL_SHUTDOWN_SECONDS", 2),
            log_level=_log_level("OPENSHELL_TOOL_SERVICE_LOG_LEVEL", "INFO"),
            prover_bin=os.environ.get("OPENSHELL_PROVER_BIN", ".state/bin/openshell-prover"),
            policy_composer_bin=os.environ.get(
                "OPENSHELL_POLICY_COMPOSER_BIN", ".state/bin/poc-policy-compose"
            ),
            policy_review_timeout_seconds=_positive_int(
                "OPENSHELL_POLICY_REVIEW_TIMEOUT_SECONDS", 10
            ),
        )
