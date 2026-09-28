"""Explicit opt-in: creates one real sandbox, runs Pi, then deletes it."""

import logging
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings


@pytest.mark.skipif(
    os.environ.get("OPENSHELL_TEST_LIVE_DELEGATION") != "1",
    reason="set OPENSHELL_TEST_LIVE_DELEGATION=1 to create a real Pi child",
)
def test_live_provider_delegation(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="httpx")
    settings = replace(Settings.from_env(), database_path=tmp_path / "jobs.sqlite3")
    assert settings.child_provider, "live test expects the configured inference provider"
    body = {
        "idempotencyKey": "live-provider-smoke",
        "caller": {"sandboxName": "pi-parent"},
        "worker": {
            "stepIndex": 0,
            "prompt": "Run hostname and return exactly OPEN_SHELL_CHILD_OK followed by its output.",
            "resources": {"childPolicy": Path("policies/parent-smoke.yaml").read_text()},
        },
    }
    headers = {"Authorization": f"Bearer {settings.token}"}
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/jobs", json=body, headers=headers)
        assert response.status_code < 300, response.text
        job_id = response.json()["providerJobId"]
        deadline = (
            time.monotonic() + settings.create_timeout_seconds + settings.job_timeout_seconds + 90
        )
        while time.monotonic() < deadline:
            result = client.get(f"/v1/jobs/{job_id}/result", headers=headers).json()
            if result.get("state") in {"completed", "failed"}:
                break
            time.sleep(0.25)
        assert result.get("state") == "completed", result
        assert "OPEN_SHELL_CHILD_OK" in result["output"], result
        assert not result.get("cleanupError"), result
        print(result["output"])
