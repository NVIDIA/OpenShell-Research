"""Asynchronous job orchestration for OpenShell-backed Pi workers."""

from __future__ import annotations

import asyncio
import hashlib
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


def _violation_summary(violation: str) -> str:
    """Render a concrete witness without claiming that it proves task safety."""
    try:
        witness = json.loads(violation)
    except ValueError:
        return " ".join(violation.split())[:512]
    if not isinstance(witness, dict):
        return "child permissions exceed the parent policy"
    domain = witness.get("domain")
    if domain == "process":
        text = (f"child changes {witness.get('field')} from {witness.get('maximum')} "
                f"to {witness.get('candidate')}")
    elif domain == "landlock":
        text = (f"child weakens Landlock from {witness.get('maximum')} "
                f"to {witness.get('candidate')}")
    elif domain == "filesystem":
        text = f"child permits {witness.get('access')} access to {witness.get('path')}"
    elif domain == "network":
        text = f"child permits {witness.get('host')}:{witness.get('port')}"
        if witness.get("destination_ip"):
            text += f" at destination IP {witness['destination_ip']}"
        if witness.get("method"):
            text += f" ({witness['method']} {witness.get('path', '')})"
        # Only expose known Git protocol operation values, never arbitrary queries.
        query = witness.get("query_params")
        if (witness.get("host") == "github.com"
                and witness.get("method") == "GET"
                and str(witness.get("path", "")).endswith("/info/refs")
                and isinstance(query, dict)):
            services = query.get("service")
            if isinstance(services, list) and services and all(
                isinstance(value, str) and value in {"git-upload-pack", "git-receive-pack"}
                for value in services
            ):
                operations = {"git-upload-pack": "clone/fetch", "git-receive-pack": "push"}
                text += "; " + ", ".join(
                    f"service={value} ({operations[value]})" for value in sorted(set(services))
                )
    else:
        return "child permissions exceed the parent policy; counterexample available at DEBUG"
    return " ".join(f"{text}, outside the parent policy".split())[:512]

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
        "Override longer skill examples: wait once at "
        "http://policy.local/v1/proposals/<id>/wait?timeout=30 with curl --max-time 35 "
        "and a 40-second shell-tool timeout. If approval and reload are not confirmed, "
        "report the proposal ID and ask the user to review it in openshell term. "
        "Do not automatically repeat the wait or submit a duplicate proposal. "
        "A wait timeout does not authorize a launch.",
        "Do not approve the proposal yourself.",
    )
)


class Runtime(Protocol):
    def run(
        self, job: Job, *, expected_policy: str | None = None,
        expected_parent_policy: str | None = None,
    ) -> ExecutionResult: ...

    def cleanup(self, job: Job) -> str | None: ...


class ParentPolicySource(Protocol):
    def get(self, sandbox_name: str) -> str: ...


class PolicyComposer(Protocol):
    def prepare(self, job: Job) -> PolicyReviewRequest: ...


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
        policy_composer: PolicyComposer | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.policy_reviewer = policy_reviewer
        self.parent_policy_source = parent_policy_source
        self.policy_composer = policy_composer
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
            logger.debug(
                "job %s accepted (parent=%s, sandbox=%s)",
                job.id[:8],
                job.caller_id,
                job.sandbox_name,
            )
            logger.debug(
                "job %s source policy sha256=%s", job.id[:8],
                hashlib.sha256(policy.encode()).hexdigest()[:12],
            )
            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(self.executor, self._run_job, job.id)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            logger.debug("job %s reattached (state=%s)", job.id[:8], job.state)
        return job

    def _run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        self.store.mark_running(job.id)
        started = time.monotonic()
        stage_started = started
        try:
            parent_policy = self.parent_policy_source.get(job.caller_id)
            logger.debug("job %s checking parent-authored child policy against parent %s",
                        job.id[:8], job.caller_id)
            request = PolicyReviewRequest(
                    parent_policy=parent_policy,
                    child_policy=job.child_policy,
                    task=job.prompt,
            )
            # Check the original document too: composition must not hide unsupported fields.
            stage_started = time.monotonic()
            review = self.policy_reviewer.review(request)
            review_ms = round((time.monotonic() - stage_started) * 1000)
            if review.decision == "allow" and self.policy_composer is not None:
                logger.debug("job %s child policy check passed (%dms)", job.id[:8], review_ms)
                stage_started = time.monotonic()
                request = self.policy_composer.prepare(job)
                logger.debug("job %s checking effective child policy against parent %s",
                            job.id[:8], job.caller_id)
                stage_started = time.monotonic()
                review = self.policy_reviewer.review(request)
                review_ms = round((time.monotonic() - stage_started) * 1000)
            logger.debug("job %s policy review details: %s; counterexamples=%s",
                         job.id[:8], review.reason, review.violations)
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
                logger.warning(
                    "%s DENIED   %s; child will not be created (%dms)",
                    job.id[:8], _violation_summary(review.violations[0])
                    if review.violations else "child permissions exceed the parent policy",
                    review_ms,
                )
                return
            logger.info(
                "%s VERIFIED No additional permissions in the supported policy model; "
                "parent=%s; review=%dms", job.id[:8], job.caller_id,
                round((time.monotonic() - started) * 1000),
            )
            if self.policy_composer is not None:
                result = self.runtime.run(
                    job, expected_policy=request.child_policy,
                    expected_parent_policy=request.parent_policy,
                )
            else:
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
            needs_attention = result.cleanup_error or result.sandbox_log_error
            log = logger.warning if needs_attention else logger.info
            log("%s DONE     total=%.1fs; exit=%s; %s%s", job.id[:8],
                time.monotonic() - started, result.exit_code,
                "cleanup needs attention" if result.cleanup_error else "sandbox deleted",
                "; log capture failed" if result.sandbox_log_error else "")
        except ParentPolicyUnavailableError as error:
            self.store.mark_failed(job.id, code="parent-policy-unavailable", message=str(error))
            logger.error("%s NOT VERIFIED — parent policy could not be read; "
                         "child will not be created", job.id[:8])
            logger.debug("job %s parent policy lookup details: %s", job.id[:8], error)
        except PolicyReviewError as error:
            self.store.mark_failed(job.id, code=error.code, message=str(error))
            explanation = {
                "policy-review-unsupported": "policy comparison is unsupported",
                "policy-review-inconclusive": "prover could not establish containment",
                "policy-composition-unavailable": "effective policy could not be composed",
            }.get(error.code, "policy check could not complete")
            logger.error("%s NOT VERIFIED — %s; child will not be created (%dms)",
                         job.id[:8], explanation,
                         round((time.monotonic() - stage_started) * 1000))
            logger.debug("job %s policy review failure details (%s): %s",
                         job.id[:8], error.code, error)
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
            logger.error("%s FAILED   %s: %s%s", job.id[:8], error.code, error,
                         "; cleanup needs attention" if error.cleanup_error else "")
        except Exception as error:
            self.store.mark_failed(
                job.id,
                code="tool-service",
                message=str(error),
            )
            logger.exception("%s FAILED   Internal Tool Service error", job.id[:8])

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
        close_reviewer = getattr(self.policy_reviewer, "close", None)
        if callable(close_reviewer):
            await asyncio.to_thread(close_reviewer)
