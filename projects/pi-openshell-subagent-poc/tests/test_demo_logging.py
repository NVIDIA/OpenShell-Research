import json
import logging

import pytest

from openshell_tool_service.policy_reviewer import PolicyReviewError, PolicyReviewResult
from openshell_tool_service.runtime import ExecutionResult
from openshell_tool_service.service import ToolService, _violation_summary
from openshell_tool_service.store import JobStore


@pytest.mark.parametrize("service,label", [
    (["git-upload-pack"], "clone/fetch"),
    (["git-receive-pack"], "push"),
    (["PRIVATE"], None),
    (["git-receive-pack", "PRIVATE"], None),
    ("git-receive-pack", None),
])
def test_git_summary_only_exposes_known_operation_values(service, label):
    witness = {"domain": "network", "host": "github.com", "method": "GET",
               "path": "/NVIDIA/OpenShell-Research.git/info/refs",
               "query_params": {"service": service, "token": ["PRIVATE"]}}
    summary = _violation_summary(json.dumps(witness))
    assert "PRIVATE" not in summary
    if label:
        assert f"service={service[0]} ({label})" in summary
    else:
        assert "service=" not in summary
    witness["host"] = "other.example"
    assert "service=" not in _violation_summary(json.dumps(witness))


@pytest.mark.parametrize("outcome", ["allow", "deny", "unsupported", "inconclusive"])
@pytest.mark.parametrize("cleanup_error", [None, "delete failed"])
def test_demo_logs_distinguish_decisions_from_missing_proof(
    tmp_path, caplog, outcome, cleanup_error,
):
    caplog.set_level(logging.INFO)
    store = JobStore(tmp_path / "jobs.sqlite3")
    job, _ = store.create_or_get(
        caller_id="pi-parent", step_index=0, idempotency_key="demo",
        prompt="PRIVATE TASK", child_policy="PRIVATE POLICY",
    )
    executed = []

    class Source:
        def get(self, _name):
            return "PRIVATE PARENT POLICY"

    class Reviewer:
        def review(self, _request):
            if outcome in {"unsupported", "inconclusive"}:
                raise PolicyReviewError("private diagnostic", code=f"policy-review-{outcome}")
            return PolicyReviewResult(
                decision=outcome, reason="openshell-prover verified within_boundary",
                violations=[json.dumps({"domain": "network", "host": "example.com",
                                       "port": 443, "destination_ip": "192.168.0.1"})]
                if outcome == "deny" else [],
            )

    class Runtime:
        def run(self, job):
            executed.append(job.id)
            return ExecutionResult("done", "", 0, cleanup_error=cleanup_error)

    service = ToolService(store, Runtime(), Reviewer(), Source())
    try:
        service._run_job(job.id)
    finally:
        service.executor.shutdown()
    text = caplog.text
    assert "checking parent-authored" not in text
    assert "PRIVATE" not in text
    assert "within_boundary" not in text
    if outcome == "allow":
        assert "VERIFIED No additional permissions in the supported policy model" in text
        assert "DONE     total=" in text
        if cleanup_error:
            assert "cleanup needs attention" in text
            assert "sandbox deleted" not in text
            assert caplog.records[-1].levelno == logging.WARNING
        else:
            assert "sandbox deleted" in text
        assert len(caplog.records) == 2  # Fake runtime does not emit RUNNING.
        assert executed == [job.id]
    else:
        assert not executed
        assert "child will not be created" in text
        if outcome == "deny":
            assert "DENIED" in text
            assert "example.com:443 at destination IP 192.168.0.1" in text
        else:
            assert "NOT VERIFIED" in text
            assert "DENIED" not in text
