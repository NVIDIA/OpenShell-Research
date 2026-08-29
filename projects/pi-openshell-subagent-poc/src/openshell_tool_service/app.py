"""HTTP API consumed by the Pi external-job provider."""

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from openshell_tool_service.config import Settings
from openshell_tool_service.runtime import OpenShellCliRuntime
from openshell_tool_service.service import Runtime, ToolService
from openshell_tool_service.store import Job, JobStore


class JobResources(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    github_repositories: list[str] = Field(
        default_factory=list,
        alias="githubRepositories",
        max_length=1,
    )
    child_policy: str = Field(alias="childPolicy", min_length=1, max_length=65_536)


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    run_id: str = Field(alias="runId", min_length=1, max_length=256)
    step_index: int = Field(alias="stepIndex", ge=0)
    agent: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=65_536)
    prompt_digest: str = Field(alias="promptDigest", min_length=1, max_length=256)
    options: dict[str, Any]
    resources: JobResources = Field(default_factory=JobResources)


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


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = JobStore(settings.database_path)
    service = ToolService(store, runtime or OpenShellCliRuntime(settings))

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
        caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        try:
            job = tool_service.submit(
                caller_id=caller,
                run_id=request.run_id,
                step_index=request.step_index,
                agent=request.agent,
                prompt=request.prompt,
                prompt_digest=request.prompt_digest,
                options=request.options,
                github_repositories=request.resources.github_repositories,
                child_policy=request.resources.child_policy,
            )
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
