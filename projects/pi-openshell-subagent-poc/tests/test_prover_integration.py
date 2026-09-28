"""Opt-in checks with a real prover; no gateway, model, or sandbox is contacted."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import PolicyReviewRequest
from openshell_tool_service.runtime import ExecutionResult

PROVER = os.environ.get("OPENSHELL_TEST_PROVER_BIN")
pytestmark = pytest.mark.skipif(
    not PROVER, reason="set OPENSHELL_TEST_PROVER_BIN for real CLI tests"
)


def policy(*, host: str | None = None, path: str = "/tmp") -> str:
    return json.dumps(
        {
            "version": 1,
            "filesystem_policy": {"include_workdir": False, "read_write": [path]},
            "network_policies": {}
            if host is None
            else {
                "test": {
                    "endpoints": [{"host": host, "port": 443}],
                    "binaries": [{"path": "/usr/bin/curl"}],
                },
            },
        }
    )


@pytest.mark.parametrize(
    "case",
    [
        "contained",
        "equal",
        "expanded",
        "pi_baseline",
        "pi_worker_no_network",
        "process_expansion",
        "landlock_expansion",
        "ip_narrowing",
        "ip_expansion",
        "unknown_process_identity",
        "identical_unsupported_identity",
        "unknown_policy_field",
        "unresolved_path",
        "provider_allowed",
        "provider_expanded",
        "github_clone",
        "github_push",
    ],
)
def test_real_prover_gates_the_job_before_runtime(tmp_path: Path, case: str, caplog) -> None:
    caplog.set_level(logging.INFO)
    parent, child = policy(host="example.com"), policy()
    expected = None
    provider = None
    if case == "equal":
        parent = child
    elif case in {"github_clone", "github_push"}:
        parent = (Path(__file__).parents[1] / "policies/parent-github-read.yaml").read_text()
        baseline = parent.split("network_policies:", 1)[0]
        clone_rule = parent.split("  github_repo_clone:", 1)[1]
        child = baseline + "network_policies:\n  github_repo_clone:" + clone_rule
        if case == "github_push":
            # Same discovery method/path, different query value. Retain fetch POSTs
            # so the new push discovery permission is the distinguishing witness.
            child = child.replace("service: git-upload-pack", "service: git-receive-pack")
            expected = "policy-review-denied"
    elif case == "expanded":
        parent, child = child, parent
        expected = "policy-review-denied"
    elif case in {"pi_baseline", "pi_worker_no_network"}:
        parent = child = (Path(__file__).parents[1] / "policies/parent-smoke.yaml").read_text()
        if case == "pi_worker_no_network":
            child = parent.split("network_policies:", 1)[0] + "network_policies: {}\n"
    elif case in {"process_expansion", "unknown_process_identity"}:
        parent_data, child_data = json.loads(parent), json.loads(child)
        parent_data["process"] = {"run_as_user": "sandbox", "run_as_group": "sandbox"}
        child_data["process"] = {
            "run_as_user": "root" if case == "process_expansion" else "other-user",
            "run_as_group": "sandbox",
        }
        parent, child = json.dumps(parent_data), json.dumps(child_data)
        expected = (
            "policy-review-denied"
            if case == "process_expansion"
            else "policy-review-unsupported"
        )
    elif case == "landlock_expansion":
        parent_data, child_data = json.loads(parent), json.loads(child)
        parent_data["landlock"] = {"compatibility": "hard_requirement"}
        child_data["landlock"] = {"compatibility": "best_effort"}
        parent, child = json.dumps(parent_data), json.dumps(child_data)
        expected = "policy-review-denied"
    elif case in {"ip_narrowing", "ip_expansion"}:
        parent_data = json.loads(policy(host="example.com"))
        child_data = json.loads(policy(host="example.com"))
        parent_data["network_policies"]["test"]["endpoints"][0]["allowed_ips"] = [
            "10.0.0.0/8"
        ]
        child_data["network_policies"]["test"]["endpoints"][0]["allowed_ips"] = [
            "10.1.0.0/16" if case == "ip_narrowing" else "192.168.0.0/16"
        ]
        parent, child = json.dumps(parent_data), json.dumps(child_data)
        if case == "ip_expansion":
            expected = "policy-review-denied"
    elif case == "unresolved_path":
        child = policy(path="/tmp/worker")
        expected = "policy-review-unsupported"
    elif case == "identical_unsupported_identity":
        data = json.loads(child)
        data["process"] = {"run_as_user": "other-user", "run_as_group": "sandbox"}
        parent = child = json.dumps(data)
        expected = "policy-review-unsupported"
    elif case == "unknown_policy_field":
        data = json.loads(child)
        data["unknown_authority"] = True
        parent = child = json.dumps(data)
        expected = "policy-review-unavailable"
    elif case in {"provider_allowed", "provider_expanded"}:
        provider = "nv-inference"
        if case == "provider_expanded":
            expected = "policy-review-denied"

    class ParentSource:
        def get(self, _name):
            return parent

    class Runtime:
        def __init__(self):
            self.jobs = []

        def run(self, job, **approved):
            if provider:
                assert "expected_policy" in approved
            self.jobs.append(job)
            return ExecutionResult("CHILD_OK", "", 0)

        def cleanup(self, _job):
            raise AssertionError("No real sandbox should be created in this test")

    runtime = Runtime()
    config = Settings(
        token="test-token",
        database_path=tmp_path / "jobs.sqlite3",
        prover_bin=str(PROVER),
        child_provider=provider,
    )
    class Composer:
        def prepare(self, job):
            # Simulate native composition while retaining the real prover.
            effective = policy(host="example.com" if case == "provider_allowed" else "other.com")
            return PolicyReviewRequest(parent, effective, job.prompt)

    app = create_app(
        config, runtime, parent_policy_source=ParentSource(), policy_composer=Composer()
    )
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "idempotencyKey": case,
                "caller": {"sandboxName": "pi-parent"},
                "worker": {
                    "stepIndex": 0,
                    "prompt": "Run hostname",
                    "resources": {"childPolicy": child},
                },
            },
        ).json()
        job_id = created["providerJobId"]
        for _ in range(1500):
            status = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
            if status["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        else:
            pytest.fail("prover-backed job did not finish")
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers).json()
    if expected:
        assert status["state"] == "failed", result
        assert status["failureCode"] == expected, result
        assert runtime.jobs == []
        assert ("POLICY_ADVISOR_ACTION_REQUIRED" in result["output"]) == (
            expected == "policy-review-denied"
        )
        if case == "github_push":
            assert "git-receive-pack" in result["output"]
            assert "query_params" in result["output"]
            assert "service=git-receive-pack (push)" in caplog.text
            assert "VERIFIED" not in caplog.text
    else:
        assert status["state"] == "completed", result
        assert result["output"] == "CHILD_OK"
        assert len(runtime.jobs) == 1
        assert runtime.jobs[0].child_policy == child.strip()
