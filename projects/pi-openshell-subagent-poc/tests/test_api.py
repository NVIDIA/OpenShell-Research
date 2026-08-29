from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.config import Settings
from openshell_tool_service.runtime import ExecutionResult, RuntimeExecutionError
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


def settings(tmp_path: Path) -> Settings:
    return Settings(token="test-token", database_path=tmp_path / "jobs.sqlite3")


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def payload() -> dict[str, object]:
    return {
        "runId": "run-1",
        "stepIndex": 0,
        "agent": "openshell-worker",
        "prompt": "Return OPEN_SHELL_CHILD_OK",
        "promptDigest": "digest-1",
        "options": {"profile": "worker"},
        "resources": {
            "githubRepositories": [],
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
    with TestClient(create_app(settings(tmp_path), SuccessfulRuntime())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/v1/jobs", json=payload()).status_code == 401


def test_job_completes_and_submission_is_idempotent(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(create_app(settings(tmp_path), runtime)) as client:
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
        assert runtime.jobs[0].github_repositories == ()
        assert runtime.jobs[0].child_policy == "version: 1\nnetwork_policies: {}"


def test_job_failure_is_returned_as_an_external_job_result(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), FailedRuntime())) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())
        job_id = response.json()["providerJobId"]
        terminal = wait_for_terminal(client, job_id)
        assert terminal["state"] == "failed"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers()).json()
        assert result["failureCode"] == "child-exit"
        assert result["failureMessage"] == "child failed"
        assert result["output"] == "child-exit: child failed"


def test_unknown_agent_and_job_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), SuccessfulRuntime())) as client:
        bad = payload()
        bad["agent"] = "arbitrary-agent"
        assert client.post("/v1/jobs", json=bad, headers=headers()).status_code == 400
        assert client.get("/v1/jobs/missing", headers=headers()).status_code == 404


def test_one_public_github_repository_is_attached_to_the_job(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    with TestClient(create_app(settings(tmp_path), runtime)) as client:
        request = payload()
        request["resources"] = {
            "githubRepositories": ["NVIDIA/OpenShell.git"],
            "childPolicy": "version: 1\nnetwork_policies: {}",
        }
        response = client.post("/v1/jobs", json=request, headers=headers())
        assert response.status_code == 202
        wait_for_terminal(client, response.json()["providerJobId"])
        assert runtime.jobs[0].github_repositories == ("NVIDIA/OpenShell",)


def test_invalid_or_multiple_github_repositories_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), SuccessfulRuntime())) as client:
        invalid = payload()
        invalid["resources"] = {
            "githubRepositories": ["https://github.com/NVIDIA/OpenShell"],
            "childPolicy": "version: 1\nnetwork_policies: {}",
        }
        assert client.post("/v1/jobs", json=invalid, headers=headers()).status_code == 400

        multiple = payload()
        multiple["resources"] = {
            "githubRepositories": ["NVIDIA/OpenShell", "nicobailon/pi-subagents"],
            "childPolicy": "version: 1\nnetwork_policies: {}",
        }
        assert client.post("/v1/jobs", json=multiple, headers=headers()).status_code == 422


def test_parent_authored_child_policy_is_required(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), SuccessfulRuntime())) as client:
        missing = payload()
        missing["resources"] = {"githubRepositories": []}
        assert client.post("/v1/jobs", json=missing, headers=headers()).status_code == 422

        empty = payload()
        empty["resources"] = {"githubRepositories": [], "childPolicy": "   "}
        assert client.post("/v1/jobs", json=empty, headers=headers()).status_code == 400
