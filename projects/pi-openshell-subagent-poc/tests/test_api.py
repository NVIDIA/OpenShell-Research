from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openshell_tool_service.app import create_app
from openshell_tool_service.collaboration import ChildCollaboration
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
from openshell_tool_service.store import Job, JobStore


class SuccessfulRuntime:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.executed: list[Job] = []

    def prepare(self, job: Job, _collaboration: ChildCollaboration, on_ready=None) -> None:
        self.jobs.append(job)
        if on_ready:
            on_ready()

    def execute(self, job: Job) -> ExecutionResult:
        self.executed.append(job)
        return ExecutionResult(
            output="OPEN_SHELL_CHILD_OK",
            stderr="",
            exit_code=0,
            sandbox_logs="captured child log\n",
        )

    def cleanup(self, _job: Job) -> str | None:
        return None


class FailedRuntime:
    def prepare(self, _job: Job, _collaboration: ChildCollaboration, on_ready=None) -> None:
        if on_ready:
            on_ready()

    def execute(self, _job: Job) -> ExecutionResult:
        raise RuntimeExecutionError("child failed", code="child-exit", exit_code=7)

    def cleanup(self, _job: Job) -> str | None:
        return None


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def prepare(self, _job: Job, _collaboration: ChildCollaboration, on_ready=None) -> None:
        if on_ready:
            on_ready()

    def execute(self, _job: Job) -> ExecutionResult:
        self.started.set()
        assert self.release.wait(timeout=5)
        return ExecutionResult(output="done", stderr="", exit_code=0)

    def cleanup(self, _job: Job) -> str | None:
        return None


class RecoveryRuntime(SuccessfulRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cleaned: list[str] = []

    def cleanup(self, job: Job) -> str | None:
        self.cleaned.append(job.sandbox_name)
        return None


class ConcurrencyTrackingRuntime:
    def __init__(self) -> None:
        self._lock = Lock()
        self.active = 0
        self.max_active = 0
        self.live = 0
        self.max_live = 0
        self.all_live = Event()
        self.jobs: list[str] = []

    def prepare(self, job: Job, _collaboration: ChildCollaboration, on_ready=None) -> None:
        if on_ready:
            on_ready()
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.jobs.append(job.id)
        try:
            time.sleep(0.02)
        finally:
            with self._lock:
                self.active -= 1

    def execute(self, _job: Job) -> ExecutionResult:
        with self._lock:
            self.live += 1
            self.max_live = max(self.max_live, self.live)
            if self.live == 64:
                self.all_live.set()
        try:
            assert self.all_live.wait(timeout=5)
            return ExecutionResult(output="done", stderr="", exit_code=0)
        finally:
            with self._lock:
                self.live -= 1

    def cleanup(self, _job: Job) -> str | None:
        return None


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


def parent_collaboration_headers(name: str = "pi-parent") -> dict[str, str]:
    return {
        **headers(),
        "X-POC-Caller-Sandbox-Name": name,
    }


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
        "workflow": {"id": "run-1", "startMode": "immediate"},
        "worker": {
            "stepIndex": 0,
            "role": "worker-1",
            "prompt": "Return OPEN_SHELL_CHILD_OK",
            "resources": {
                "childPolicy": "version: 1\nnetwork_policies: {}",
            },
        },
    }


def wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(300):
        response = client.get(f"/v1/jobs/{job_id}", headers=headers())
        body = response.json()
        if body["state"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not become terminal")


def test_health_and_authentication(tmp_path: Path) -> None:
    app = build_app(tmp_path, SuccessfulRuntime())
    assert app.state.tool_service.prepare_executor._max_workers == 8
    assert app.state.tool_service.execution_executor._max_workers == 64
    with TestClient(app) as client:
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
        logs = client.get(f"/v1/jobs/{job_id}/logs", headers=headers())
        assert logs.json() == {
            "providerJobId": job_id,
            "sandboxName": runtime.jobs[0].sandbox_name,
            "logs": "captured child log\n",
            "captureError": None,
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


def test_batch_status_returns_many_jobs_with_one_read(tmp_path: Path) -> None:
    runtime = BlockingRuntime()
    app = create_app(
        replace(settings(tmp_path), create_concurrency=1, max_active_workers=4),
        runtime,
        AllowingReviewer(),
        ParentPolicySource(),
    )
    with TestClient(app) as client:
        first = client.post("/v1/jobs", json=payload(), headers=headers()).json()
        assert runtime.started.wait(timeout=2)
        second_payload = payload()
        second_payload["idempotencyKey"] = "request-2"
        second_payload["worker"] = {**second_payload["worker"], "role": "worker-2"}
        second = client.post("/v1/jobs", json=second_payload, headers=headers()).json()

        response = client.post(
            "/v1/jobs/status",
            json={"jobIds": [first["providerJobId"], second["providerJobId"]]},
            headers=headers(),
        )
        assert response.status_code == 200
        assert [job["providerJobId"] for job in response.json()["jobs"]] == [
            first["providerJobId"],
            second["providerJobId"],
        ]
        assert [job["state"] for job in response.json()["jobs"]] == ["running", "queued"]
        runtime.release.set()


def test_worker_capacity_returns_429_with_retry_after(tmp_path: Path) -> None:
    runtime = BlockingRuntime()
    app = create_app(
        replace(settings(tmp_path), create_concurrency=1, max_active_workers=1),
        runtime,
        AllowingReviewer(),
        ParentPolicySource(),
    )
    with TestClient(app) as client:
        first = client.post("/v1/jobs", json=payload(), headers=headers())
        assert first.status_code == 202
        assert runtime.started.wait(timeout=2)
        second_payload = payload()
        second_payload["idempotencyKey"] = "request-over-capacity"
        second_payload["worker"] = {
            **second_payload["worker"],
            "role": "worker-over-capacity",
        }
        rejected = client.post("/v1/jobs", json=second_payload, headers=headers())
        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "5"
        assert rejected.json()["detail"]["code"] == "worker-capacity"
        runtime.release.set()


def test_all_ready_workers_wait_for_the_declared_group(tmp_path: Path) -> None:
    runtime = SuccessfulRuntime()
    app = build_app(tmp_path, runtime)
    with TestClient(app) as client:
        first_request = payload()
        first_request["workflow"] = {
            "id": "coordinated-two",
            "startMode": "all-ready",
            "expectedWorkers": 2,
        }
        first = client.post("/v1/jobs", json=first_request, headers=headers())
        assert first.status_code == 202
        first_id = first.json()["providerJobId"]

        for _ in range(200):
            current = app.state.tool_service.store.get(first_id)
            if current is not None and current.state == "prepared":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("first coordinated worker was not prepared")
        assert runtime.executed == []

        second_request = payload()
        second_request["idempotencyKey"] = "coordinated-request-2"
        second_request["workflow"] = first_request["workflow"]
        second_request["worker"] = {
            **second_request["worker"],
            "stepIndex": 1,
            "role": "worker-2",
        }
        second = client.post("/v1/jobs", json=second_request, headers=headers())
        assert second.status_code == 202
        second_id = second.json()["providerJobId"]

        assert wait_for_terminal(client, first_id)["state"] == "completed"
        assert wait_for_terminal(client, second_id)["state"] == "completed"
        assert {job.participant_alias for job in runtime.executed} == {
            "worker-1",
            "worker-2",
        }


def test_workflow_contract_mismatch_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        coordinated = payload()
        coordinated["workflow"] = {
            "id": "contract-conflict",
            "startMode": "all-ready",
            "expectedWorkers": 2,
        }
        assert client.post("/v1/jobs", json=coordinated, headers=headers()).status_code == 202

        conflicting = payload()
        conflicting["idempotencyKey"] = "contract-conflict-2"
        conflicting["workflow"] = {
            "id": "contract-conflict",
            "startMode": "immediate",
        }
        conflicting["worker"] = {
            **conflicting["worker"],
            "stepIndex": 1,
            "role": "worker-2",
        }
        response = client.post("/v1/jobs", json=conflicting, headers=headers())

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "workflow-conflict"


def test_startup_fails_interrupted_job_and_cleans_its_sandbox(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    store = JobStore(configured.database_path)
    seeded, _created = store.create_or_get(
        caller_id="pi-parent",
        run_id="run-recovery",
        start_mode="immediate",
        expected_workers=None,
        step_index=0,
        idempotency_key="recovery-request",
        prompt="recover me",
        child_policy="version: 1\nnetwork_policies: {}",
        participant_alias="recovery-worker",
        max_active_workers=4,
    )
    store.mark_queued(seeded.id)
    assert store.claim_next_queued() is not None
    runtime = RecoveryRuntime()
    app = create_app(configured, runtime, AllowingReviewer(), ParentPolicySource())

    with TestClient(app) as client:
        recovered = client.get(f"/v1/jobs/{seeded.id}", headers=headers()).json()

    assert recovered["state"] == "failed"
    assert recovered["failureCode"] == "service-restarted"
    assert runtime.cleaned == [seeded.sandbox_name]


def test_startup_dispatches_durable_queued_job(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    store = JobStore(configured.database_path)
    seeded, _created = store.create_or_get(
        caller_id="pi-parent",
        run_id="run-queued",
        start_mode="immediate",
        expected_workers=None,
        step_index=0,
        idempotency_key="queued-request",
        prompt="run after restart",
        child_policy="version: 1\nnetwork_policies: {}",
        participant_alias="queued-worker",
        max_active_workers=4,
    )
    runtime = SuccessfulRuntime()
    app = create_app(configured, runtime, AllowingReviewer(), ParentPolicySource())

    with TestClient(app) as client:
        terminal = wait_for_terminal(client, seeded.id)

    assert terminal["state"] == "completed"
    assert [job.id for job in runtime.jobs] == [seeded.id]


def test_startup_reconciles_orphan_from_tool_service_failure(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    store = JobStore(configured.database_path)
    seeded, _created = store.create_or_get(
        caller_id="pi-parent",
        run_id="run-orphan",
        start_mode="immediate",
        expected_workers=None,
        step_index=0,
        idempotency_key="orphan-request",
        prompt="orphaned",
        child_policy="version: 1\nnetwork_policies: {}",
        participant_alias="orphan-worker",
        max_active_workers=4,
    )
    store.mark_queued(seeded.id)
    assert store.claim_next_queued() is not None
    store.mark_failed(seeded.id, code="tool-service", message="Too many open files")
    runtime = RecoveryRuntime()

    with TestClient(
        create_app(configured, runtime, AllowingReviewer(), ParentPolicySource())
    ):
        pass

    recovered = store.get(seeded.id)
    assert recovered is not None
    assert recovered.cleanup_error == ""
    assert runtime.cleaned == [seeded.sandbox_name]


def test_sixty_four_jobs_prepare_in_batches_then_run_together(tmp_path: Path) -> None:
    runtime = ConcurrencyTrackingRuntime()
    app = create_app(
        replace(settings(tmp_path), create_concurrency=8, max_active_workers=64),
        runtime,
        AllowingReviewer(),
        ParentPolicySource(),
    )
    with TestClient(app) as client:
        def submit(index: int):
            request = payload()
            request["idempotencyKey"] = f"fanout-{index}"
            request["workflow"] = {
                "id": "coordinated-64",
                "startMode": "all-ready",
                "expectedWorkers": 64,
            }
            request["worker"] = {
                **request["worker"],
                "stepIndex": index,
                "role": f"worker-{index + 1}",
            }
            return client.post("/v1/jobs", json=request, headers=headers())

        with ThreadPoolExecutor(max_workers=64) as submitters:
            responses = list(submitters.map(submit, range(64)))
        assert all(response.status_code == 202 for response in responses)
        job_ids = [response.json()["providerJobId"] for response in responses]

        for job_id in job_ids:
            assert wait_for_terminal(client, job_id)["state"] == "completed"

    assert len(runtime.jobs) == 64
    assert 2 <= runtime.max_active <= 8
    assert runtime.max_live == 64


def test_reusing_an_idempotency_key_for_different_content_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        assert client.post("/v1/jobs", json=payload(), headers=headers()).status_code == 202
        changed = payload()
        changed["worker"] = {**changed["worker"], "prompt": "Do something different"}
        response = client.post("/v1/jobs", json=changed, headers=headers())
        assert response.status_code == 409
        assert "already used" in response.json()["detail"]


def test_duplicate_active_role_is_classified_as_a_conflict(tmp_path: Path) -> None:
    app = build_app(tmp_path, SuccessfulRuntime())
    app.state.tool_service.collaboration.reserve_child(
        parent_sandbox_name="pi-parent",
        job_id="existing-job",
        sandbox_name="pi-child-existing",
        participant_alias="worker-1",
        run_id="run-1",
    )

    with TestClient(app) as client:
        response = client.post("/v1/jobs", json=payload(), headers=headers())

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "collaboration-role-conflict",
            "message": "the collaboration role 'worker-1' is already active in this workflow",
        }


def test_parent_authored_child_policy_is_required(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        missing = payload()
        missing["worker"] = {**missing["worker"], "resources": {}}
        assert client.post("/v1/jobs", json=missing, headers=headers()).status_code == 422

        empty = payload()
        empty["worker"] = {
            **empty["worker"],
            "resources": {"childPolicy": "   "},
        }
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


def test_collaboration_api_routes_parent_and_child_messages(tmp_path: Path) -> None:
    app = build_app(tmp_path, SuccessfulRuntime())
    collaboration = app.state.tool_service.collaboration
    child = collaboration.register_child(
        parent_sandbox_name="pi-parent",
        job_id="manual-job",
        sandbox_name="pi-child-manual",
        participant_alias="worker-a",
        service_url="http://host.openshell.internal:8765",
    )
    child_headers = {"Authorization": f"Bearer {child.token}"}

    with TestClient(app) as client:
        participants = client.get(
            "/v1/collaboration/participants",
            headers=parent_collaboration_headers(),
        )
        assert participants.status_code == 200
        assert [item["sandboxName"] for item in participants.json()["participants"]] == [
            "pi-parent",
            "pi-child-manual",
        ]
        assert [item["roleName"] for item in participants.json()["participants"]] == [
            "parent",
            "worker-a",
        ]

        sent = client.post(
            "/v1/collaboration/messages",
            headers=parent_collaboration_headers(),
            json={
                "recipient": "worker-a",
                "body": "Please report progress.",
                "kind": "question",
                "idempotencyKey": "parent-send-1",
            },
        )
        assert sent.status_code == 201

        child_inbox = client.get(
            "/v1/collaboration/messages?after=0&wait=0",
            headers=child_headers,
        ).json()
        assert child_inbox["messages"][0]["body"] == "Please report progress."
        assert child_inbox["messages"][0]["recipient"]["roleName"] == "worker-a"
        assert child_inbox["lastSequence"] == sent.json()["sequence"]

        child_reply = client.post(
            "/v1/collaboration/messages",
            headers=child_headers,
            json={
                "recipient": "parent",
                "body": "Halfway done.",
                "kind": "progress",
                "replyTo": sent.json()["messageId"],
                "idempotencyKey": "child-send-1",
            },
        )
        assert child_reply.status_code == 201
        parent_inbox = client.get(
            "/v1/collaboration/messages?after=0&wait=0",
            headers=parent_collaboration_headers(),
        ).json()
        assert parent_inbox["messages"][0]["body"] == "Halfway done."
        assert parent_inbox["messages"][0]["sender"]["roleName"] == "worker-a"

        mailbox = client.get(
            "/v1/collaboration/mailbox?wait=0",
            headers=parent_collaboration_headers(),
        ).json()
        assert mailbox["deliveries"][0]["message"]["body"] == "Halfway done."
        delivery_id = mailbox["deliveries"][0]["deliveryId"]
        acknowledged = client.post(
            "/v1/collaboration/mailbox/ack",
            headers=parent_collaboration_headers(),
            json={"deliveryIds": [delivery_id]},
        )
        assert acknowledged.json()["acknowledgedDeliveryIds"] == [delivery_id]
        assert (
            client.get(
                "/v1/collaboration/mailbox?wait=0",
                headers=parent_collaboration_headers(),
            ).json()["deliveries"]
            == []
        )

        collaboration.finish_child(child.participant_id)
        active = client.get(
            "/v1/collaboration/participants",
            headers=parent_collaboration_headers(),
        ).json()["participants"]
        assert active == []
        with_history = client.get(
            "/v1/collaboration/participants?include_finished=true",
            headers=parent_collaboration_headers(),
        ).json()["participants"]
        assert with_history == []


def test_parent_collaboration_api_requires_sandbox_name(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, SuccessfulRuntime())) as client:
        response = client.get("/v1/collaboration/participants", headers=headers())
        assert response.status_code == 400


def test_child_mailbox_reports_terminal_expected_sender(tmp_path: Path) -> None:
    app = build_app(tmp_path, SuccessfulRuntime())
    collaboration = app.state.tool_service.collaboration
    waiting_child = collaboration.register_child(
        parent_sandbox_name="pi-parent",
        run_id="dependency-run",
        job_id="waiting-job",
        sandbox_name="waiting-child",
        participant_alias="waiting-worker",
        service_url="http://example.test",
    )
    collaboration.register_child(
        parent_sandbox_name="pi-parent",
        run_id="dependency-run",
        job_id="dependency-job",
        sandbox_name="dependency-child",
        participant_alias="dependency-worker",
        service_url="http://example.test",
    )
    collaboration.fail_job("dependency-job", "SSH transport failed")

    with TestClient(app) as client:
        response = client.get(
            "/v1/collaboration/mailbox?wait=30&sender=dependency-worker",
            headers={"Authorization": f"Bearer {waiting_child.token}"},
        )

    assert response.status_code == 200
    assert response.json()["deliveries"] == []
    assert response.json()["terminalError"] == {
        "code": "expected-sender-failed",
        "sender": {
            "roleName": "dependency-worker",
            "sandboxName": "dependency-child",
        },
        "state": "failed",
        "reason": "SSH transport failed",
        "message": "Expected worker dependency-worker failed: SSH transport failed",
    }
