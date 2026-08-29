"""Job orchestration owned by the OpenShell Tool Service."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from openshell_tool_service.runtime import ExecutionResult, RuntimeExecutionError
from openshell_tool_service.store import Job, JobStore

logger = logging.getLogger(__name__)

GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)


def normalize_github_repositories(repositories: list[str]) -> tuple[str, ...]:
    if len(repositories) > 1:
        raise ValueError("each openshell-worker job may access at most one GitHub repository")
    normalized: list[str] = []
    for repository in repositories:
        value = repository.strip()
        if value.endswith(".git"):
            value = value[:-4]
        if not GITHUB_REPOSITORY.fullmatch(value):
            raise ValueError("GitHub repositories must use the OWNER/REPO form")
        normalized.append(value)
    return tuple(normalized)


class Runtime(Protocol):
    def run(self, job: Job) -> ExecutionResult: ...


class ToolService:
    """Validate one generic worker envelope and execute jobs asynchronously."""

    def __init__(
        self,
        store: JobStore,
        runtime: Runtime,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="openshell-child"
        )

    def submit(
        self,
        *,
        caller_id: str,
        run_id: str,
        step_index: int,
        agent: str,
        prompt: str,
        prompt_digest: str,
        options: dict[str, Any],
        github_repositories: list[str],
        child_policy: str,
    ) -> Job:
        if agent != "openshell-worker":
            raise ValueError("only the openshell-worker agent is allowed")
        if options != {"profile": "worker"}:
            raise ValueError("options must select only the fixed worker profile")
        repositories = normalize_github_repositories(github_repositories)
        policy = child_policy.strip()
        if not policy:
            raise ValueError("the parent must provide a non-empty child policy")

        job, created = self.store.create_or_get(
            caller_id=caller_id,
            run_id=run_id,
            step_index=step_index,
            agent=agent,
            prompt=prompt,
            prompt_digest=prompt_digest,
            options=options,
            profile="worker",
            github_repositories=repositories,
            child_policy=policy,
        )
        if created:
            logger.info(
                "accepted job %s for child sandbox %s",
                job.id,
                job.sandbox_name,
            )
            self.executor.submit(self._execute, job.id)
        else:
            logger.info("reattached to existing job %s in state %s", job.id, job.state)
        return job

    def _execute(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        self.store.mark_running(job_id)
        logger.info("starting child sandbox %s for job %s", job.sandbox_name, job.id)
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
                "job %s failed with %s; child sandbox %s cleanup: %s",
                job.id,
                error.code,
                job.sandbox_name,
                error.cleanup_error or "ok",
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
            "job %s completed; child sandbox %s cleanup: %s",
            job.id,
            job.sandbox_name,
            result.cleanup_error or "ok",
        )

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
