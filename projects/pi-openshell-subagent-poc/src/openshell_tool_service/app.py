"""HTTP API consumed by the Pi external-job provider."""

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from openshell_tool_service.config import Settings
from openshell_tool_service.policy_reviewer import LlmPolicyReviewer, PolicyReviewer
from openshell_tool_service.runtime import OpenShellCliParentPolicySource, OpenShellCliRuntime
from openshell_tool_service.service import ParentPolicySource, Runtime, ToolService
from openshell_tool_service.store import IdempotencyConflictError, Job, JobStore


class JobResources(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    child_policy: str = Field(alias="childPolicy", min_length=1, max_length=65_536)


class JobCaller(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sandbox_name: str = Field(
        alias="sandboxName",
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)
    caller: JobCaller
    prompt: str = Field(min_length=1, max_length=65_536)
    resources: JobResources


def _handle(job: Job, *, include_output: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "providerJobId": job.id,
        "state": job.state,
    }
    if job.failure_code:
        result["failureCode"] = job.failure_code
    if job.failure_message:
        result["failureMessage"] = job.failure_message
    if include_output and job.output is not None:
        result["output"] = job.output
    elif include_output and job.failure_message:
        diagnostic = f"{job.failure_code or 'job-failed'}: {job.failure_message}"
        if job.stderr:
            diagnostic = f"{diagnostic}\n\n{job.stderr.strip()[:4096]}"
        result["output"] = diagnostic
    return result


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    policy_reviewer: PolicyReviewer | None = None,
    parent_policy_source: ParentPolicySource | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if policy_reviewer is None:
        if not settings.policy_review_api_key:
            raise ValueError("policy reviewer API key is required")
        policy_reviewer = LlmPolicyReviewer(
            base_url=settings.policy_review_base_url,
            api_key=settings.policy_review_api_key,
            model=settings.policy_review_model,
            timeout_seconds=settings.policy_review_timeout_seconds,
        )
    store = JobStore(settings.database_path)
    service = ToolService(
        store,
        runtime or OpenShellCliRuntime(settings),
        policy_reviewer,
        parent_policy_source or OpenShellCliParentPolicySource(settings),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        service.close()

    app = FastAPI(title="OpenShell Tool Service", version="0.1.0", lifespan=lifespan)
    app.state.tool_service = service

    def caller_id(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, settings.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid Tool Service token",
            )
        return "sandbox-1"

    def current_service(request: Request) -> ToolService:
        return request.app.state.tool_service

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(
        request: JobCreate,
        _authenticated: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        try:
            job = tool_service.submit(
                caller_id=request.caller.sandbox_name,
                idempotency_key=request.idempotency_key,
                prompt=request.prompt,
                child_policy=request.resources.child_policy,
            )
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _handle(job)

    @app.get("/v1/jobs/{job_id}")
    def get_job(
        job_id: str,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = tool_service.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _handle(job)

    @app.get("/v1/jobs/{job_id}/result")
    def get_result(
        job_id: str,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = tool_service.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.state not in {"completed", "failed", "stopped", "blocked"}:
            raise HTTPException(status_code=409, detail="job is not complete")
        return _handle(job, include_output=True)

    return app
