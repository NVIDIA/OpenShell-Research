"""Job orchestration owned by the OpenShell Tool Service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Lock
from typing import Protocol

from openshell_tool_service.collaboration import (
    ChildCollaboration,
    CollaborationError,
    CollaborationStore,
)
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
    def prepare(
        self,
        job: Job,
        collaboration: ChildCollaboration,
        on_ready: Callable[[], None] | None = None,
    ) -> None: ...

    def execute(self, job: Job) -> ExecutionResult: ...

    def cleanup(self, job: Job) -> str | None: ...


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
        collaboration: CollaborationStore,
        collaboration_url: str,
        create_concurrency: int = 8,
        max_active_workers: int = 64,
        workflow_ready_timeout_seconds: int = 300,
        prepare_executor: ThreadPoolExecutor | None = None,
        execution_executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.policy_reviewer = policy_reviewer
        self.parent_policy_source = parent_policy_source
        self.collaboration = collaboration
        self.collaboration_url = collaboration_url
        self.create_concurrency = create_concurrency
        self.max_active_workers = max_active_workers
        self.workflow_ready_timeout_seconds = workflow_ready_timeout_seconds
        self.prepare_executor = prepare_executor or ThreadPoolExecutor(
            max_workers=create_concurrency, thread_name_prefix="openshell-prepare"
        )
        self.execution_executor = execution_executor or ThreadPoolExecutor(
            max_workers=max_active_workers, thread_name_prefix="openshell-exec"
        )
        self._admission_lock = Lock()
        self._started = False
        self._stopping = False
        self._wake: asyncio.Event | None = None
        self._prepare_slots: asyncio.Semaphore | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._prepare_tasks: set[asyncio.Task[None]] = set()
        self._execution_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._wake = asyncio.Event()
        self._prepare_slots = asyncio.Semaphore(self.create_concurrency)
        await asyncio.to_thread(self._recover)
        self._dispatcher = asyncio.create_task(
            self._dispatch_loop(), name="openshell-job-dispatcher"
        )

    def _recover(self) -> None:
        for job in self.store.recover_interrupted():
            cleanup_error: str | None = None
            cleanup = getattr(self.runtime, "cleanup", None)
            if callable(cleanup):
                cleanup_error = cleanup(job)
            self.store.mark_failed(
                job.id,
                code="service-restarted",
                message="Tool Service restarted while the job was running",
                cleanup_error=cleanup_error,
            )
            self.collaboration.fail_job(job.id, "Tool Service restarted")
            logger.warning(
                "job %s recovered as failed after service restart (cleanup=%s)",
                job.id[:8],
                "error" if cleanup_error else "ok",
            )

        cleanup = getattr(self.runtime, "cleanup", None)
        if callable(cleanup):
            for job in self.store.list_cleanup_candidates():
                cleanup_error = cleanup(job)
                self.store.record_cleanup_attempt(job.id, cleanup_error)
                if cleanup_error:
                    logger.warning(
                        "job %s orphan cleanup failed: %s", job.id[:8], cleanup_error
                    )
                else:
                    logger.info(
                        "job %s reconciled orphan sandbox %s",
                        job.id[:8],
                        job.sandbox_name,
                    )

        expired = self.store.expire_waiting(
            older_than=time.time() - self.workflow_ready_timeout_seconds
        )
        for job in expired:
            self.collaboration.fail_job(job.id, "job expired before execution")
        if expired:
            logger.warning("expired %d stale queued jobs during startup", len(expired))

        for job in [
            *self.store.list_by_state("admitting"),
            *self.store.list_by_state("queued"),
        ]:
            try:
                self.collaboration.reserve_child(
                    parent_sandbox_name=job.caller_id,
                    run_id=job.run_id,
                    job_id=job.id,
                    sandbox_name=job.sandbox_name,
                    participant_alias=job.participant_alias,
                )
                if job.state == "admitting":
                    self.store.mark_queued(job.id)
            except CollaborationError as error:
                self.store.mark_failed(job.id, code=error.code, message=str(error))
                logger.error("job %s recovery failed: %s", job.id[:8], error)

    async def _dispatch_loop(self) -> None:
        assert self._wake is not None
        assert self._prepare_slots is not None
        while not self._stopping:
            await asyncio.to_thread(self._expire_waiting_jobs)
            await self._release_ready_workflows()
            await self._prepare_slots.acquire()
            if self._stopping:
                self._prepare_slots.release()
                return

            # Clear before checking SQLite so a submission racing this lookup
            # leaves the event armed and cannot strand a queued job.
            self._wake.clear()
            job = await asyncio.to_thread(self.store.claim_next_queued)
            if job is None:
                self._prepare_slots.release()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                continue

            task = asyncio.create_task(
                self._prepare_async(job.id), name=f"openshell-prepare-{job.id[:8]}"
            )
            self._prepare_tasks.add(task)
            task.add_done_callback(self._prepare_finished)

    def _expire_waiting_jobs(self) -> None:
        expired = self.store.expire_waiting(
            older_than=time.time() - self.workflow_ready_timeout_seconds
        )
        for job in expired:
            cleanup_error = self.runtime.cleanup(job) if job.state == "prepared" else None
            if cleanup_error:
                self.store.record_cleanup_attempt(job.id, cleanup_error)
            self.collaboration.fail_job(job.id, "workflow readiness timed out")
        if expired:
            logger.warning("expired %d workers waiting for workflow readiness", len(expired))

    async def _prepare_async(self, job_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.prepare_executor, self._prepare, job_id)

    def _prepare_finished(self, task: asyncio.Task[None]) -> None:
        self._prepare_tasks.discard(task)
        if self._prepare_slots is not None:
            self._prepare_slots.release()
        if self._wake is not None:
            self._wake.set()
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("job preparation task failed unexpectedly: %s", error)

    async def _release_ready_workflows(self) -> None:
        """Start workers using the workflow mode declared by the parent."""

        for caller_id, run_id in await asyncio.to_thread(self.store.list_workflows):
            jobs = await asyncio.to_thread(self.store.workflow_jobs, caller_id, run_id)
            if not jobs:
                continue
            if jobs[0].start_mode == "all-ready" and any(
                job.state == "failed" for job in jobs
            ):
                await asyncio.to_thread(self._abort_prepared_workflow, caller_id, run_id)
                continue
            if jobs[0].start_mode == "all-ready" and any(
                job.state in {"admitting", "queued", "preparing"} for job in jobs
            ):
                continue
            released = await asyncio.to_thread(
                self.store.release_ready_workflow,
                caller_id,
                run_id,
                max_active_workers=self.max_active_workers,
            )
            if not released:
                continue
            coordinated = released[0].start_mode == "all-ready"
            logger.info(
                "workflow %s starting %d %s Pi worker%s",
                run_id[:12],
                len(released),
                "coordinated" if coordinated else "independent",
                "s" if len(released) != 1 else "",
            )
            for job in released:
                self.collaboration.record_job_event(
                    job.id, "workflow.released" if coordinated else "worker.released"
                )
                task = asyncio.create_task(
                    self._execute_async(job.id),
                    name=f"openshell-exec-{job.id[:8]}",
                )
                self._execution_tasks.add(task)
                task.add_done_callback(self._execution_finished)

    def _abort_prepared_workflow(self, caller_id: str, run_id: str) -> None:
        for job in self.store.workflow_jobs(caller_id, run_id):
            if job.state not in {"admitting", "queued", "preparing", "prepared"}:
                continue
            cleanup_error = self.runtime.cleanup(job) if job.state == "prepared" else None
            self.store.mark_failed(
                job.id,
                code="workflow-prepare-failed",
                message="Another coordinated worker failed before the workflow was ready",
                cleanup_error=cleanup_error,
            )
            self.collaboration.fail_job(job.id, "coordinated workflow preparation failed")

    async def _execute_async(self, job_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.execution_executor, self._execute_child, job_id)

    def _execution_finished(self, task: asyncio.Task[None]) -> None:
        self._execution_tasks.discard(task)
        if self._wake is not None:
            self._wake.set()
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("job execution task failed unexpectedly: %s", error)

    async def submit(
        self,
        *,
        caller_id: str,
        run_id: str,
        start_mode: str,
        expected_workers: int | None,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
        participant_alias: str,
    ) -> Job:
        job, should_wake = await asyncio.to_thread(
            self._submit_sync,
            caller_id=caller_id,
            run_id=run_id,
            start_mode=start_mode,
            expected_workers=expected_workers,
            step_index=step_index,
            idempotency_key=idempotency_key,
            prompt=prompt,
            child_policy=child_policy,
            participant_alias=participant_alias,
        )
        if should_wake and self._wake is not None:
            self._wake.set()
        return job

    def _submit_sync(
        self,
        *,
        caller_id: str,
        run_id: str,
        start_mode: str,
        expected_workers: int | None,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
        participant_alias: str,
    ) -> tuple[Job, bool]:
        # Admission spans the job and collaboration tables. Serializing this
        # short sequence prevents concurrent fan-out from exposing a job before
        # its role reservation is durable. Child execution remains concurrent.
        with self._admission_lock:
            return self._submit_locked(
                caller_id=caller_id,
                run_id=run_id,
                start_mode=start_mode,
                expected_workers=expected_workers,
                step_index=step_index,
                idempotency_key=idempotency_key,
                prompt=prompt,
                child_policy=child_policy,
                participant_alias=participant_alias,
            )

    def _submit_locked(
        self,
        *,
        caller_id: str,
        run_id: str,
        start_mode: str,
        expected_workers: int | None,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
        participant_alias: str,
    ) -> tuple[Job, bool]:
        policy = child_policy.strip()
        if not policy:
            raise ValueError("the parent must provide a non-empty child policy")

        job, created = self.store.create_or_get(
            caller_id=caller_id,
            run_id=run_id,
            start_mode=start_mode,
            expected_workers=expected_workers,
            step_index=step_index,
            idempotency_key=idempotency_key,
            prompt=prompt,
            child_policy=policy,
            participant_alias=participant_alias,
            max_active_workers=self.max_active_workers,
        )
        request_body = {
            "idempotencyKey": idempotency_key,
            "caller": {"sandboxName": caller_id},
            "workflow": {
                "id": run_id,
                "startMode": start_mode,
                "expectedWorkers": expected_workers,
            },
            "worker": {
                "stepIndex": step_index,
                "role": participant_alias,
                "prompt": prompt,
                "resources": {"childPolicy": policy},
            },
        }
        policy_digest = hashlib.sha256(policy.encode()).hexdigest()[:12]
        if created or job.state == "admitting":
            try:
                self.collaboration.reserve_child(
                    parent_sandbox_name=caller_id,
                    run_id=run_id,
                    job_id=job.id,
                    sandbox_name=job.sandbox_name,
                    participant_alias=job.participant_alias,
                )
                job = self.store.mark_queued(job.id)
            except Exception as error:
                self.store.mark_failed(
                    job.id,
                    code=getattr(error, "code", "participant-reservation"),
                    message=str(error),
                )
                raise
            if created:
                logger.info(
                    "job %s accepted (run=%s, sandbox=%s, role=%s, policy_sha256=%s)",
                    job.id[:8],
                    run_id[:12],
                    job.sandbox_name,
                    job.participant_alias,
                    policy_digest,
                )
                self.collaboration.record_job_event(job.id, "job.accepted")
            logger.debug(
                "job %s request body:\n%s",
                job.id[:8],
                json.dumps(request_body, indent=2),
            )
        else:
            logger.info(
                "job %s reattached (state=%s)",
                job.id[:8],
                job.state,
            )
            logger.debug(
                "job %s request body:\n%s",
                job.id[:8],
                json.dumps(request_body, indent=2),
            )
        return job, created or job.state == "queued"

    def _prepare(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        logger.debug(
            "job %s details: policy review starting after queue_ms=%d",
            job.id[:8],
            round((time.time() - job.created_at) * 1000),
        )
        review_started_at = time.monotonic()
        reviewer_started_at = review_started_at
        self.collaboration.transition_job(
            job.id, "policy-reviewing", event_type="parent-policy.lookup.started"
        )
        try:
            policy_lookup_started_at = time.monotonic()
            parent_policy = self.parent_policy_source.get(job.caller_id)
            self.collaboration.record_job_event(
                job.id,
                "parent-policy.lookup.completed",
                {"durationMs": round((time.monotonic() - policy_lookup_started_at) * 1000)},
            )
            logger.info("job %s policy review started", job.id[:8])
            self.collaboration.record_job_event(job.id, "policy.review.started")
            reviewer_started_at = time.monotonic()
            review = self.policy_reviewer.review(
                PolicyReviewRequest(
                    parent_policy=parent_policy,
                    child_policy=job.child_policy,
                    task=job.prompt,
                )
            )
        except ParentPolicyUnavailableError as error:
            self.collaboration.record_job_event(
                job.id,
                "parent-policy.lookup.failed",
                {
                    "durationMs": round((time.monotonic() - policy_lookup_started_at) * 1000),
                    "reason": str(error),
                },
            )
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
            self.collaboration.fail_job(job_id, str(error))
            return
        except PolicyReviewError as error:
            self.collaboration.record_job_event(
                job.id,
                "policy.review.failed",
                {
                    "durationMs": round((time.monotonic() - reviewer_started_at) * 1000),
                    "reason": str(error),
                },
            )
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
            self.collaboration.fail_job(job_id, str(error))
            return
        except Exception:
            self.collaboration.record_job_event(
                job.id,
                "policy.review.failed",
                {
                    "durationMs": round((time.monotonic() - reviewer_started_at) * 1000),
                    "reason": "unexpected reviewer failure",
                },
            )
            self.store.mark_failed(
                job_id,
                code="policy-review-unavailable",
                message="mock LLM policy review failed unexpectedly",
            )
            logger.exception("job %s mock LLM policy review failed closed", job.id[:8])
            self.collaboration.fail_job(job_id, "policy review failed unexpectedly")
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
            self.collaboration.record_job_event(
                job_id,
                "policy.review.denied",
                {
                    "reason": review.reason,
                    "durationMs": round((time.monotonic() - reviewer_started_at) * 1000),
                },
            )
            self.collaboration.fail_job(job_id, review.reason)
            return
        logger.info(
            "job %s policy review allowed in %.1fs",
            job.id[:8],
            time.monotonic() - review_started_at,
        )
        logger.debug("job %s policy review reason: %s", job.id[:8], reason)
        self.collaboration.record_job_event(
            job_id,
            "policy.review.allowed",
            {
                "reason": review.reason,
                "durationMs": round((time.monotonic() - reviewer_started_at) * 1000),
            },
        )
        if review.task_alignment == "warning":
            logger.warning(
                "job %s policy task alignment=%s: %s",
                job.id[:8],
                review.task_alignment,
                review.task_alignment_reason.replace("\n", " "),
            )
        child_collaboration: ChildCollaboration | None = None
        try:
            sandbox_create_started_at = time.monotonic()
            self.collaboration.transition_job(
                job.id, "starting", event_type="sandbox.create.requested"
            )
            child_collaboration = self.collaboration.activate_child(
                job_id=job.id,
                service_url=self.collaboration_url,
            )
            logger.info(
                "job %s collaboration participant started (group=%s, participant=%s, role=%s)",
                job.id[:8],
                child_collaboration.group_id[:8],
                child_collaboration.participant_id[:8],
                child_collaboration.participant_alias,
            )
            self.runtime.prepare(
                job,
                child_collaboration,
                on_ready=lambda: self.collaboration.mark_ready(
                    job.id,
                    {
                        "durationMs": round(
                            (time.monotonic() - sandbox_create_started_at) * 1000
                        )
                    },
                ),
            )
        except RuntimeExecutionError as error:
            self.store.mark_failed(
                job_id,
                code=error.code,
                message=str(error),
                stderr=error.stderr,
                exit_code=error.exit_code,
                cleanup_error=error.cleanup_error,
                sandbox_logs=error.sandbox_logs,
                sandbox_log_error=error.sandbox_log_error,
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
            self.collaboration.record_job_event(
                job_id,
                "sandbox.cleanup.failed" if error.cleanup_error else "sandbox.cleanup.completed",
                {"error": error.cleanup_error} if error.cleanup_error else {},
            )
            return
        except Exception as error:
            self.store.mark_failed(job_id, code="tool-service", message=str(error))
            logger.exception("job %s failed inside the Tool Service", job.id)
            self.collaboration.record_job_event(job_id, "job.failed", {"reason": str(error)})
            return
        finally:
            current = self.store.get(job.id)
            if current is not None and current.state == "failed":
                self.collaboration.fail_job(
                    job.id, current.failure_message or "child execution failed"
                )
            if child_collaboration is not None:
                logger.info(
                    "job %s collaboration participant prepared (participant=%s)",
                    job.id[:8],
                    child_collaboration.participant_id[:8],
                )
        if not self.store.mark_prepared(job_id):
            cleanup_error = self.runtime.cleanup(job)
            self.store.record_cleanup_attempt(job.id, cleanup_error)
            logger.warning(
                "job %s finished preparation after its workflow had already failed",
                job.id[:8],
            )
            return
        self.collaboration.record_job_event(job_id, "sandbox.prepared")
        logger.info(
            "job %s sandbox prepared (workflow=%s, start_mode=%s)",
            job.id[:8],
            job.run_id[:12],
            job.start_mode,
        )

    def _execute_child(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        execution_started_at = time.monotonic()
        self.collaboration.record_job_event(job_id, "pi.execution.started")
        try:
            result = self.runtime.execute(job)
        except RuntimeExecutionError as error:
            self.collaboration.record_job_event(
                job_id,
                "pi.execution.failed",
                {
                    "durationMs": round((time.monotonic() - execution_started_at) * 1000),
                    "reason": str(error),
                },
            )
            self.store.mark_failed(
                job_id,
                code=error.code,
                message=str(error),
                stderr=error.stderr,
                exit_code=error.exit_code,
                cleanup_error=error.cleanup_error,
                sandbox_logs=error.sandbox_logs,
                sandbox_log_error=error.sandbox_log_error,
            )
            self.collaboration.record_job_event(
                job_id,
                "sandbox.cleanup.failed" if error.cleanup_error else "sandbox.cleanup.completed",
                {"error": error.cleanup_error} if error.cleanup_error else {},
            )
            self.collaboration.fail_job(job.id, str(error))
            logger.error(
                "job %s execution failed after %.1fs (code=%s)",
                job.id[:8],
                time.time() - job.created_at,
                error.code,
            )
            return
        except Exception as error:
            self.collaboration.record_job_event(
                job_id,
                "pi.execution.failed",
                {
                    "durationMs": round((time.monotonic() - execution_started_at) * 1000),
                    "reason": str(error),
                },
            )
            cleanup_error = self.runtime.cleanup(job)
            self.store.mark_failed(
                job_id,
                code="tool-service",
                message=str(error),
                cleanup_error=cleanup_error,
            )
            self.collaboration.fail_job(job.id, str(error))
            logger.exception("job %s failed inside the Tool Service", job.id)
            return
        self.collaboration.record_job_event(
            job_id,
            "pi.execution.completed",
            {"durationMs": round((time.monotonic() - execution_started_at) * 1000)},
        )
        self.store.mark_completed(
            job_id,
            output=result.output,
            stderr=result.stderr,
            exit_code=result.exit_code,
            cleanup_error=result.cleanup_error,
            sandbox_logs=result.sandbox_logs,
            sandbox_log_error=result.sandbox_log_error,
        )
        self.collaboration.record_job_event(
            job_id,
            "sandbox.cleanup.failed" if result.cleanup_error else "sandbox.cleanup.completed",
            {"error": result.cleanup_error} if result.cleanup_error else {},
        )
        self.collaboration.record_job_event(job_id, "job.completed")
        self.collaboration.finish_job(job.id)
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

    async def close(self) -> None:
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        if self._dispatcher is not None:
            await self._dispatcher
        if self._prepare_tasks:
            await asyncio.gather(*tuple(self._prepare_tasks), return_exceptions=True)
        if self._execution_tasks:
            await asyncio.gather(*tuple(self._execution_tasks), return_exceptions=True)
        await asyncio.to_thread(
            self.prepare_executor.shutdown, wait=True, cancel_futures=False
        )
        await asyncio.to_thread(
            self.execution_executor.shutdown, wait=True, cancel_futures=False
        )
        close_reviewer = getattr(self.policy_reviewer, "close", None)
        if callable(close_reviewer):
            await asyncio.to_thread(close_reviewer)
