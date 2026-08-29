"""Job orchestration owned by the OpenShell Tool Service."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from openshell_tool_service.policy_reviewer import (
    PolicyReviewer,
    PolicyReviewError,
    PolicyReviewRequest,
)
from openshell_tool_service.runtime import (
    ExecutionResult,
    ParentPolicyUnavailableError,
    RuntimeExecutionError,
)
from openshell_tool_service.store import Job, JobStore

logger = logging.getLogger(__name__)

POLICY_ADVISOR_GUIDANCE = "\n".join(
    (
        "POLICY_ADVISOR_ACTION_REQUIRED",
        "No child sandbox was created because the proposed child policy exceeds "
        "the live parent policy.",
        "For missing network authority only, read "
        "/etc/openshell/skills/policy-advisor/SKILL.md in the parent sandbox. "
        "Submit the narrowest required addRule proposal to "
        "http://policy.local/v1/proposals, wait for a human decision, and retry "
        "by launching a new openshell-worker only after policy_reloaded is true.",
        "Do not approve the proposal yourself. If the missing authority is not "
        "supported by Policy Advisor, report that a manual parent-policy update "
        "is required.",
    )
)


class Runtime(Protocol):
    def run(self, job: Job) -> ExecutionResult: ...


class ParentPolicySource(Protocol):
    def get(self, sandbox_name: str) -> str: ...


class ToolService:
    """Validate one generic worker envelope and execute jobs asynchronously."""

    def __init__(
        self,
        store: JobStore,
        runtime: Runtime,
        policy_reviewer: PolicyReviewer,
        parent_policy_source: ParentPolicySource,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.policy_reviewer = policy_reviewer
        self.parent_policy_source = parent_policy_source
        self.executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="openshell-child"
        )

    def submit(
        self,
        *,
        caller_id: str,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
    ) -> Job:
        policy = child_policy.strip()
        if not policy:
            raise ValueError("the parent must provide a non-empty child policy")

        job, created = self.store.create_or_get(
            caller_id=caller_id,
            idempotency_key=idempotency_key,
            prompt=prompt,
            child_policy=policy,
        )
        request_body = {
            "idempotencyKey": idempotency_key,
            "caller": {
                "sandboxName": caller_id,
            },
            "prompt": prompt,
            "resources": {
                "childPolicy": policy,
            },
        }
        if created:
            logger.info(
                "job %s accepted (sandbox=%s)",
                job.id[:8],
                job.sandbox_name,
            )
            logger.info(
                "job %s request body:\n%s",
                job.id[:8],
                json.dumps(request_body, indent=2),
            )
            self.executor.submit(self._execute, job.id)
        else:
            logger.info(
                "job %s reattached (state=%s)",
                job.id[:8],
                job.state,
            )
            logger.info(
                "job %s request body:\n%s",
                job.id[:8],
                json.dumps(request_body, indent=2),
            )
        return job

    def _execute(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        logger.debug(
            "job %s details: policy review starting after queue_ms=%d",
            job.id[:8],
            round((time.time() - job.created_at) * 1000),
        )
        logger.info("job %s running mock LLM policy review", job.id[:8])
        try:
            parent_policy = self.parent_policy_source.get(job.caller_id)
            review = self.policy_reviewer.review(
                PolicyReviewRequest(
                    parent_policy=parent_policy,
                    child_policy=job.child_policy,
                    task=job.prompt,
                )
            )
        except ParentPolicyUnavailableError as error:
            self.store.mark_failed(
                job_id,
                code="parent-policy-unavailable",
                message=str(error),
            )
            logger.error(
                "job %s parent policy lookup failed closed: %s",
                job.id[:8],
                str(error).replace("\n", " "),
            )
            return
        except PolicyReviewError as error:
            self.store.mark_failed(
                job_id,
                code="policy-review-unavailable",
                message=str(error),
            )
            logger.error(
                "job %s mock LLM policy review failed closed: %s",
                job.id[:8],
                str(error).replace("\n", " "),
            )
            return
        except Exception:
            self.store.mark_failed(
                job_id,
                code="policy-review-unavailable",
                message="mock LLM policy review failed unexpectedly",
            )
            logger.exception("job %s mock LLM policy review failed closed", job.id[:8])
            return
        reason = review.reason.replace("\n", " ")
        if review.decision != "allow":
            violation_text = "; ".join(review.violations) or "unspecified policy expansion"
            diagnostic = (
                f"Reviewer reason: {review.reason}\n"
                f"Missing authority: {violation_text}\n\n"
                f"{POLICY_ADVISOR_GUIDANCE}"
            )
            self.store.mark_failed(
                job_id,
                code="policy-review-denied",
                message="Proposed child policy exceeds the live parent policy",
                stderr=diagnostic,
            )
            logger.warning(
                "job %s mock LLM policy review denied: %s",
                job.id[:8],
                reason,
            )
            return
        logger.info(
            "job %s mock LLM policy review allowed: %s",
            job.id[:8],
            reason,
        )
        if review.task_alignment != "aligned":
            logger.warning(
                "job %s policy task alignment=%s: %s",
                job.id[:8],
                review.task_alignment,
                review.task_alignment_reason.replace("\n", " "),
            )
        self.store.mark_running(job_id)
        try:
            result = self.runtime.run(job)
        except RuntimeExecutionError as error:
            self.store.mark_failed(
                job_id,
                code=error.code,
                message=str(error),
                stderr=error.stderr,
                exit_code=error.exit_code,
                cleanup_error=error.cleanup_error,
            )
            logger.error(
                "job %s failed after %.1fs (code=%s, exit_code=%s, cleanup=%s)",
                job.id[:8],
                (time.time() - job.created_at),
                error.code,
                error.exit_code if error.exit_code is not None else "none",
                "error" if error.cleanup_error else "ok",
            )
            logger.debug(
                "job %s details: failure stderr_bytes=%d",
                job.id[:8],
                len(error.stderr.encode("utf-8")),
            )
            return
        except Exception as error:
            self.store.mark_failed(job_id, code="tool-service", message=str(error))
            logger.exception("job %s failed inside the Tool Service", job.id)
            return
        self.store.mark_completed(
            job_id,
            output=result.output,
            stderr=result.stderr,
            exit_code=result.exit_code,
            cleanup_error=result.cleanup_error,
        )
        logger.info(
            "job %s completed successfully in %.1fs",
            job.id[:8],
            (time.time() - job.created_at),
        )
        logger.debug(
            "job %s details: exit_code=%d output_bytes=%d stderr_bytes=%d cleanup=%s",
            job.id[:8],
            result.exit_code,
            len(result.output.encode("utf-8")),
            len(result.stderr.encode("utf-8")),
            "error" if result.cleanup_error else "ok",
        )

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
