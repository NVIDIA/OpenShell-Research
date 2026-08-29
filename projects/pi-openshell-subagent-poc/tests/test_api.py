from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import (
    PolicyReviewer,
    PolicyReviewError,
    PolicyReviewRequest,
    PolicyReviewResult,
)
from openshell_tool_service.runtime import (
    ExecutionResult,
    ParentPolicyUnavailableError,
    RuntimeExecutionError,
)
from openshell_tool_service.service import Runtime
from openshell_tool_service.store import Job


class SuccessfulRuntime:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def run(self, job: Job) -> ExecutionResult:
        self.jobs.append(job)
        return ExecutionResult(output="OPEN_SHELL_CHILD_OK", stderr="", exit_code=0)


class FailedRuntime:
    def run(self, _job: Job) -> ExecutionResult:
        raise RuntimeExecutionError("child failed", code="child-exit", exit_code=7)


class AllowingReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(
            decision="allow",
            reason="Child policy is no more permissive than the parent policy.",
            violations=(),
            task_alignment="aligned",
            task_alignment_reason="The policy matches the task.",
            reviewer="test",
        )


class DenyingReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(
            decision="deny",
            reason="Child policy adds email access.",
            violations=("email access is absent from the parent policy",),
            task_alignment="warning",
            task_alignment_reason="Email access is not required by the task.",
            reviewer="test",
        )


class BrokenReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        raise PolicyReviewError("review provider timed out")


class ParentPolicySource:
    def __init__(self, policy: str = "active-parent-policy") -> None:
        self.policy = policy
        self.sandbox_names: list[str] = []

    def get(self, sandbox_name: str) -> str:
        self.sandbox_names.append(sandbox_name)
        return self.policy


class BrokenParentPolicySource:
    def get(self, _sandbox_name: str) -> str:
        raise ParentPolicyUnavailableError("parent sandbox is unavailable")


def settings(tmp_path: Path) -> Settings:
    return Settings(token="test-token", database_path=tmp_path / "jobs.sqlite3")


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def build_app(
    tmp_path: Path,
    runtime: Runtime,
    reviewer: PolicyReviewer | None = None,
    parent_policy_source: ParentPolicySource | BrokenParentPolicySource | None = None,
) -> FastAPI:
    return create_app(
        settings(tmp_path),
        runtime,
        reviewer or AllowingReviewer(),
        parent_policy_source or ParentPolicySource(),
    )


def payload() -> dict[str, object]:
    return {
        "idempotencyKey": "request-1",
        "caller": {"sandboxName": "pi-parent"},
        "prompt": "Return OPEN_SHELL_CHILD_OK",
        "resources": {
            "childPolicy": "version: 1\nnetwork_policies: {}",
        },
    }


def wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/v1/jobs/{job_id}", headers=headers())
        body = response.json()
        if body["state"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not become terminal")


def test_health_and_authentication(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/v1/jobs", json=payload()).status_code == 401


def test_job_completes_and_submission_is_idempotent(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    parent_policy_source = ParentPolicySource()
    app = build_app(tmp_path, runtime, parent_policy_source=parent_policy_source)
    with TestClient(app) as client:
        first = client.post("/v1/jobs", json=payload(), headers=headers())
        second = client.post("/v1/jobs", json=payload(), headers=headers())
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["providerJobId"] == second.json()["providerJobId"]

        job_id = first.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "completed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers())
        assert result.json() == {
            "providerJobId": job_id,
            "state": "completed",
            "output": "OPEN_SHELL_CHILD_OK",
        }
        assert len(runtime.jobs) == 1
        assert runtime.jobs[0].caller_id == "pi-parent"
        assert runtime.jobs[0].child_policy == "version: 1\nnetwork_policies: {}"
        assert parent_policy_source.sandbox_names == ["pi-parent"]


def test_job_failure_is_returned_as_an_external_job_result(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, FailedRuntime())) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert result["failureCode"] == "child-exit"
        assert result["failureMessage"] == "child failed"
        assert result["output"] == "child-exit: child failed"


def test_unknown_job_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.get("/v1/jobs/missing", headers=headers()).status_code == 404


def test_reusing_an_idempotency_key_for_different_content_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.post("/v1/jobs", json=payload(), headers=headers()).status_code == 202
        changed = payload()
        changed["prompt"] = "Do something different"
        response = client.post("/v1/jobs", json=changed, headers=headers())
        assert response.status_code == 409
        assert "already used" in response.json()["detail"]


def test_parent_authored_child_policy_is_required(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        missing = payload()
        missing["resources"] = {}
        assert client.post("/v1/jobs", json=missing, headers=headers()).status_code == 422

        empty = payload()
        empty["resources"] = {"childPolicy": "   "}
        assert client.post("/v1/jobs", json=empty, headers=headers()).status_code == 400


def test_caller_sandbox_name_is_required_and_validated(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        missing = payload()
        missing.pop("caller")
        assert client.post("/v1/jobs", json=missing, headers=headers()).status_code == 422

        invalid = payload()
        invalid["caller"] = {"sandboxName": "--global"}
        assert client.post("/v1/jobs", json=invalid, headers=headers()).status_code == 422


def test_policy_review_denial_creates_no_sandbox_job(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(build_app(tmp_path, runtime, DenyingReviewer())) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert result["failureCode"] == "policy-review-denied"
        assert "adds email access" in result["output"]
        assert "POLICY_ADVISOR_ACTION_REQUIRED" in result["output"]
        assert "/etc/openshell/skills/policy-advisor/SKILL.md" in result["output"]
        assert "http://policy.local/v1/proposals" in result["output"]
        assert "launching a new openshell-worker" in result["output"]
        assert runtime.jobs == []


def test_policy_review_failure_fails_closed(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(build_app(tmp_path, runtime, BrokenReviewer())) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert result["failureCode"] == "policy-review-unavailable"
        assert runtime.jobs == []


def test_parent_policy_lookup_failure_fails_closed(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(
        build_app(
            tmp_path,
            runtime,
            parent_policy_source=BrokenParentPolicySource(),
        )
    ) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert result["failureCode"] == "parent-policy-unavailable"
        assert runtime.jobs == []
