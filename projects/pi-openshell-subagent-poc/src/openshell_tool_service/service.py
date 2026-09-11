"""Asynchronous job orchestration for OpenShell-backed Pi workers."""

from __future__ import annotations

import asyncio
import hashlib
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
        "http://policy.local/v1/proposals, wait for a human decision, and launch "
        "a new openshell-worker after policy_reloaded is true.",
        "Do not approve the proposal yourself.",
    )
)


class Runtime(Protocol):
    def run(self, job: Job) -> ExecutionResult: ...

    def cleanup(self, job: Job) -> str | None: ...


class ParentPolicySource(Protocol):
    def get(self, sandbox_name: str) -> str: ...


class ToolService:
    """Review a parent-authored policy and run each worker in its own sandbox."""

    def __init__(
        self,
        store: JobStore,
        runtime: Runtime,
        policy_reviewer: PolicyReviewer,
        parent_policy_source: ParentPolicySource,
        *,
        max_workers: int = 8,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.policy_reviewer = policy_reviewer
        self.parent_policy_source = parent_policy_source
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="openshell-worker"
        )
        self._tasks: set[asyncio.Future[None]] = set()

    async def start(self) -> None:
        """Fail interrupted jobs and clean up their deterministically named sandboxes."""

        interrupted = await asyncio.to_thread(self.store.recover_interrupted)
        for job in interrupted:
            cleanup_error = await asyncio.to_thread(self.runtime.cleanup, job)
            await asyncio.to_thread(
                self.store.mark_failed,
                job.id,
                code="service-restarted",
                message="Tool Service restarted while the job was running",
                cleanup_error=cleanup_error,
            )

    async def submit(
        self,
        *,
        caller_id: str,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
    ) -> Job:
        policy = child_policy.strip()
        if not policy:
            raise ValueError("the parent must provide a non-empty child policy")
        job, created = await asyncio.to_thread(
            self.store.create_or_get,
            caller_id=caller_id,
            step_index=step_index,
            idempotency_key=idempotency_key,
            prompt=prompt,
            child_policy=policy,
        )
        if created:
            logger.info(
                "job %s accepted (sandbox=%s, policy_sha256=%s)",
                job.id[:8],
                job.sandbox_name,
                hashlib.sha256(policy.encode()).hexdigest()[:12],
            )
            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(self.executor, self._run_job, job.id)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            logger.info("job %s reattached (state=%s)", job.id[:8], job.state)
        return job

    def _run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        self.store.mark_running(job.id)
        started = time.monotonic()
        try:
            parent_policy = self.parent_policy_source.get(job.caller_id)
            logger.info("job %s policy review started", job.id[:8])
            review = self.policy_reviewer.review(
                PolicyReviewRequest(
                    parent_policy=parent_policy,
                    child_policy=job.child_policy,
                    task=job.prompt,
                )
            )
            if review.decision != "allow":
                violations = "; ".join(review.violations) or "unspecified policy expansion"
                self.store.mark_failed(
                    job.id,
                    code="policy-review-denied",
                    message="Proposed child policy exceeds the live parent policy",
                    stderr=(
                        f"Reviewer reason: {review.reason}\n"
                        f"Missing authority: {violations}\n\n{POLICY_ADVISOR_GUIDANCE}"
                    ),
                )
                logger.warning("job %s policy review denied: %s", job.id[:8], review.reason)
                return
            logger.info("job %s policy review allowed: %s", job.id[:8], review.reason)
            result = self.runtime.run(job)
            self.store.mark_completed(
                job.id,
                output=result.output,
                stderr=result.stderr,
                exit_code=result.exit_code,
                cleanup_error=result.cleanup_error,
                sandbox_logs=result.sandbox_logs,
                sandbox_log_error=result.sandbox_log_error,
            )
            logger.info("job %s completed in %.1fs", job.id[:8], time.monotonic() - started)
        except ParentPolicyUnavailableError as error:
            self.store.mark_failed(job.id, code="parent-policy-unavailable", message=str(error))
            logger.error("job %s parent policy lookup failed: %s", job.id[:8], error)
        except PolicyReviewError as error:
            self.store.mark_failed(job.id, code="policy-review-unavailable", message=str(error))
            logger.error("job %s policy review failed closed: %s", job.id[:8], error)
        except RuntimeExecutionError as error:
            self.store.mark_failed(
                job.id,
                code=error.code,
                message=str(error),
                stderr=error.stderr,
                exit_code=error.exit_code,
                cleanup_error=error.cleanup_error,
                sandbox_logs=error.sandbox_logs,
                sandbox_log_error=error.sandbox_log_error,
            )
            logger.error("job %s child execution failed: %s", job.id[:8], error)
        except Exception as error:
            self.store.mark_failed(
                job.id,
                code="tool-service",
                message=str(error),
            )
            logger.exception("job %s failed inside the Tool Service", job.id[:8])

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
        close_reviewer = getattr(self.policy_reviewer, "close", None)
        if callable(close_reviewer):
            await asyncio.to_thread(close_reviewer)
