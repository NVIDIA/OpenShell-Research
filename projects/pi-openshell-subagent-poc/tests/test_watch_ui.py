from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.collaboration import ChildCollaboration
from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import PolicyReviewRequest, PolicyReviewResult
from openshell_tool_service.runtime import ExecutionResult
from openshell_tool_service.store import Job


class SuccessfulRuntime:
    def prepare(
        self,
        _job: Job,
        _collaboration: ChildCollaboration,
        on_ready=None,
    ) -> None:
        if on_ready:
            on_ready()

    def execute(self, _job: Job) -> ExecutionResult:
        return ExecutionResult(
            output="watcher result",
            stderr="",
            exit_code=0,
            sandbox_logs=(
                "[100.000] [sandbox] [OCSF ] [ocsf] API:INFERENCE [INFO] "
                "Success test/model via https://inference.example/v1 250ms "
                "[POST /v1/responses]\n"
            ),
        )

    def cleanup(self, _job: Job) -> str | None:
        return None


class AllowingReviewer:
    def review(self, _request: PolicyReviewRequest) -> PolicyReviewResult:
        return PolicyReviewResult(
            decision="allow",
            reason="allowed for watcher test",
            violations=(),
            task_alignment="aligned",
            task_alignment_reason="aligned for watcher test",
            reviewer="test",
        )


class ParentPolicySource:
    def get(self, _sandbox_name: str) -> str:
        return "parent-policy"


def application(tmp_path: Path):
    return create_app(
        Settings(token="test-token", database_path=tmp_path / "jobs.sqlite3"),
        SuccessfulRuntime(),
        AllowingReviewer(),
        ParentPolicySource(),
    )


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def wait_for_terminal(client: TestClient, job_id: str) -> None:
    for _ in range(100):
        state = client.get(f"/v1/jobs/{job_id}", headers=headers()).json()["state"]
        if state in {"completed", "failed"}:
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_watch_page_is_served_without_embedding_the_service_token(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        response = client.get("/watch")

    assert response.status_code == 200
    assert "OpenShell Collaboration Watcher" in response.text
    assert '<option value="conversation">Conversation</option>' in response.text
    assert '<option value="network" selected>Network flow</option>' in response.text
    assert "Technical details" in response.text
    assert "test-token" not in response.text
    assert "sessionStorage" in response.text


def test_timeline_requires_authentication_and_filters_by_parent(tmp_path: Path) -> None:
    app = application(tmp_path)
    collaboration = app.state.tool_service.collaboration
    parent_a = collaboration.parent_actor("parent-a", "run-a")
    collaboration.parent_actor("parent-b", "run-b")
    child = collaboration.register_child(
        parent_sandbox_name="parent-a",
        run_id="run-a",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="worker-a",
        service_url="http://example.test",
    )
    actor = collaboration.actor_for_token(child.token)
    assert actor is not None
    collaboration.send(
        actor=actor,
        recipient="parent",
        kind="result",
        body="message visible in watcher",
        reply_to=None,
        idempotency_key="watch-message",
    )

    with TestClient(app) as client:
        unauthorized = client.get("/v1/watch/timeline", params={"parent": "parent-a"})
        response = client.get(
            "/v1/watch/timeline",
            params={"parent": "parent-a", "after": 0},
            headers=headers(),
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["lastSequence"] > 0
    assert {event["parentSandboxName"] for event in body["events"]} == {"parent-a"}
    message = next(event for event in body["events"] if event["eventType"] == "message.stored")
    assert message["message"]["body"] == "message visible in watcher"
    assert message["message"]["sender"]["roleName"] == "worker-a"
    assert message["message"]["recipient"]["roleName"] == "parent"
    assert parent_a.sandbox_name == "parent-a"


def test_job_detail_returns_debug_data_without_prompt_or_policy(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        response = client.post(
            "/v1/jobs",
            headers=headers(),
            json={
                "idempotencyKey": "watch-job",
                "caller": {"sandboxName": "pi-parent"},
                "workflow": {"id": "watch-run", "startMode": "immediate"},
                "worker": {
                    "stepIndex": 0,
                    "role": "reviewer",
                    "prompt": "private delegated prompt",
                    "resources": {
                        "childPolicy": "version: 1\nnetwork_policies: {}"
                    },
                },
            },
        )
        assert response.status_code == 202
        job_id = response.json()["providerJobId"]
        wait_for_terminal(client, job_id)
        detail = client.get(f"/v1/watch/jobs/{job_id}", headers=headers())

    assert detail.status_code == 200
    body = detail.json()
    assert body["state"] == "completed"
    assert body["roleName"] == "reviewer"
    assert body["output"] == "watcher result"
    assert "API:INFERENCE" in body["sandboxLogs"]
    assert "prompt" not in body
    assert "childPolicy" not in body


def test_network_flow_reports_service_and_inference_latencies(tmp_path: Path) -> None:
    with TestClient(application(tmp_path)) as client:
        response = client.post(
            "/v1/jobs",
            headers=headers(),
            json={
                "idempotencyKey": "flow-job",
                "caller": {"sandboxName": "pi-parent"},
                "workflow": {"id": "flow-run", "startMode": "immediate"},
                "worker": {
                    "stepIndex": 0,
                    "role": "reviewer",
                    "prompt": "review",
                    "resources": {"childPolicy": "version: 1\nnetwork_policies: {}"},
                },
            },
        )
        job_id = response.json()["providerJobId"]
        wait_for_terminal(client, job_id)
        flow = client.get(
            "/v1/watch/network-flow",
            params={"parent": "pi-parent", "run": "flow-run"},
            headers=headers(),
        )

    assert flow.status_code == 200
    spans = flow.json()["spans"]
    assert any(
        span["source"] == "Parent Pi"
        and span["target"] == "Tool Service"
        and span["durationMs"] is None
        for span in spans
    )
    assert any(
        span["source"] == "Tool Service"
        and span["target"] == "Policy Reviewer"
        and span["durationMs"] is not None
        for span in spans
    )
    inference = next(span for span in spans if span["target"] == "Inference")
    assert inference["source"] == "reviewer"
    assert inference["durationMs"] == 250
    assert inference["timingSource"] == "OpenShell API:INFERENCE latency"
