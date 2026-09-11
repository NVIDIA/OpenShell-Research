from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import PolicyReviewRequest, PolicyReviewResult
from openshell_tool_service.runtime import ExecutionResult
from openshell_tool_service.store import Job


class SuccessfulRuntime:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def run(self, job: Job) -> ExecutionResult:
        self.jobs.append(job)
        return ExecutionResult("OPEN_SHELL_CHILD_OK", "", 0)

    def cleanup(self, _job: Job) -> str | None:
        return None


class AllowingReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(decision="allow", reason="subset", violations=[])


class DenyingReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(
            decision="deny", reason="network expansion", violations=["github.com"]
        )


class ParentPolicySource:
    def get(self, _sandbox_name: str) -> str:
        return "version: 1\nnetwork_policies: {}"


def settings(tmp_path: Path) -> Settings:
    return Settings(token="test-token", database_path=tmp_path / "jobs.sqlite3")


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def payload() -> dict[str, object]:
    return {
        "idempotencyKey": "request-1",
        "caller": {"sandboxName": "pi-parent"},
        "worker": {
            "stepIndex": 0,
            "prompt": "Return OPEN_SHELL_CHILD_OK",
            "resources": {"childPolicy": "version: 1\nnetwork_policies: {}"},
        },
    }


def build_app(tmp_path: Path, runtime, reviewer=None):
    return create_app(
        settings(tmp_path),
        runtime,
        reviewer or AllowingReviewer(),
        ParentPolicySource(),
    )


def wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(200):
        body = client.get(f"/v1/jobs/{job_id}", headers=headers()).json()
        if body["state"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not become terminal")


def test_cleanup_warning_is_visible_in_status_and_result(tmp_path: Path) -> None:
    class CleanupFailedRuntime(SuccessfulRuntime):
        def run(self, job: Job) -> ExecutionResult:
            return ExecutionResult("DONE", "", 0, cleanup_error="gateway unavailable")

    with TestClient(build_app(tmp_path, CleanupFailedRuntime())) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        status = wait_for_terminal(client, job_id)
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        for body in (status, result):
            assert body["state"] == "completed"
            assert body["cleanupError"] == "gateway unavailable"
            assert body["sandboxName"].startswith("pi-child-")
        assert result["output"] == "DONE"


def test_health_and_authentication(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/v1/jobs", json=payload()).status_code == 401


def test_job_completes_and_submission_is_idempotent(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(build_app(tmp_path, runtime)) as client:
        first = client.post("/v1/jobs", json=payload(), headers=headers())
        second = client.post("/v1/jobs", json=payload(), headers=headers())
        assert first.status_code == second.status_code == 202
        assert first.json()["providerJobId"] == second.json()["providerJobId"]

        job_id = first.json()["providerJobId"]
        assert wait_for_terminal(client, job_id)["state"] == "completed"
        assert client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json() == {
            "providerJobId": job_id,
            "state": "completed",
            "output": "OPEN_SHELL_CHILD_OK",
        }
    assert len(runtime.jobs) == 1


@pytest.mark.parametrize("suffix", ["", "/result", "/logs"])
def test_job_routes_share_lookup_and_authentication(tmp_path: Path, suffix: str) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        route = f"/v1/jobs/missing{suffix}"
        assert client.get(route).status_code == 401
        response = client.get(route, headers=headers())
        assert response.status_code == 404
        assert response.json() == {"detail": "job not found"}


def test_service_does_not_repeat_runtime_cleanup(tmp_path: Path) -> None:
    class BrokenRuntime(SuccessfulRuntime):
        def run(self, job: Job) -> ExecutionResult:
            raise ValueError("unexpected failure")

        def cleanup(self, job: Job) -> str | None:
            raise AssertionError("execution cleanup belongs to the runtime")

    with TestClient(build_app(tmp_path, BrokenRuntime())) as client:
        created = client.post("/v1/jobs", json=payload(), headers=headers()).json()
        terminal = wait_for_terminal(client, created["providerJobId"])
        assert terminal["failureCode"] == "tool-service"
        assert terminal["failureMessage"] == "unexpected failure"


def test_policy_denial_fails_before_child_creation(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(build_app(tmp_path, runtime, DenyingReviewer())) as client:
        created = client.post("/v1/jobs", json=payload(), headers=headers()).json()
        job_id = created["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        assert terminal["failureCode"] == "policy-review-denied"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert "POLICY_ADVISOR_ACTION_REQUIRED" in result["output"]
    assert runtime.jobs == []


def test_reusing_idempotency_key_for_different_request_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.post("/v1/jobs", json=payload(), headers=headers()).status_code == 202
        changed = payload()
        changed["worker"]["prompt"] = "different"  # type: ignore[index]
        assert client.post("/v1/jobs", json=changed, headers=headers()).status_code == 409


@pytest.mark.parametrize("second_policy", ["parent-policy", "updated-parent-policy"])
def test_each_new_job_fetches_parent_policy_and_runs_review(
    tmp_path: Path, second_policy: str
) -> None:
    class ChangingPolicySource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, sandbox_name: str) -> str:
            self.calls.append(sandbox_name)
            return "parent-policy" if len(self.calls) == 1 else second_policy

    class ReviewingEachJob:
        def __init__(self) -> None:
            self.requests: list[PolicyReviewRequest] = []

        def review(self, request: PolicyReviewRequest) -> PolicyReviewResult:
            self.requests.append(request)
            if len(self.requests) == 1:
                return PolicyReviewResult(decision="allow", reason="contained", violations=[])
            return PolicyReviewResult(
                decision="deny", reason="permission increase", violations=["network"]
            )

    runtime = SuccessfulRuntime()
    source = ChangingPolicySource()
    reviewer = ReviewingEachJob()
    app = create_app(settings(tmp_path), runtime, reviewer, source)
    with TestClient(app) as client:
        first = client.post("/v1/jobs", json=payload(), headers=headers()).json()
        assert wait_for_terminal(client, first["providerJobId"])["state"] == "completed"

        second_payload = payload()
        second_payload["idempotencyKey"] = "request-2"
        second = client.post("/v1/jobs", json=second_payload, headers=headers()).json()
        assert wait_for_terminal(client, second["providerJobId"])["state"] == "failed"

    assert source.calls == ["pi-parent", "pi-parent"]
    assert [request.parent_policy for request in reviewer.requests] == [
        "parent-policy",
        second_policy,
    ]
    assert len(runtime.jobs) == 1
