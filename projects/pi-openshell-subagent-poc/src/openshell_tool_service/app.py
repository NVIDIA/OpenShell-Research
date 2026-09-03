"""HTTP API consumed by the Pi external-job provider."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openshell_tool_service.collaboration import (
    CollaborationActor,
    CollaborationConflictError,
    CollaborationDelivery,
    CollaborationError,
    CollaborationMessage,
    CollaborationNotFoundError,
    CollaborationParticipant,
    CollaborationStore,
    CollaborationTerminalError,
    token_matches,
)
from openshell_tool_service.config import Settings
from openshell_tool_service.network_flow import build_network_flow
from openshell_tool_service.policy_reviewer import (
    CachingPolicyReviewer,
    LlmPolicyReviewer,
    PolicyReviewer,
)
from openshell_tool_service.runtime import (
    CachingParentPolicySource,
    OpenShellCliParentPolicySource,
    OpenShellCliRuntime,
)
from openshell_tool_service.service import ParentPolicySource, Runtime, ToolService
from openshell_tool_service.store import (
    IdempotencyConflictError,
    Job,
    JobQueueFullError,
    JobStore,
    WorkflowConflictError,
)

logger = logging.getLogger(__name__)
WATCH_UI_PATH = Path(__file__).with_name("static") / "watch.html"


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


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=128)
    start_mode: Literal["immediate", "all-ready"] = Field(alias="startMode")
    expected_workers: int | None = Field(
        default=None, alias="expectedWorkers", ge=2, le=64
    )

    @model_validator(mode="after")
    def validate_coordination(self) -> "WorkflowSpec":
        if self.start_mode == "all-ready" and self.expected_workers is None:
            raise ValueError("all-ready workflows require expectedWorkers")
        if self.start_mode == "immediate" and self.expected_workers is not None:
            raise ValueError("immediate workflows must not set expectedWorkers")
        return self


class WorkerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    step_index: int = Field(alias="stepIndex", ge=0, le=10_000)
    role: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    prompt: str = Field(min_length=1, max_length=65_536)
    resources: JobResources


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)
    caller: JobCaller
    workflow: WorkflowSpec
    worker: WorkerSpec


class JobStatusBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_ids: list[str] = Field(alias="jobIds", min_length=1, max_length=100)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    recipient: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=65_536)
    kind: str = Field(default="message", min_length=1, max_length=32)
    message_type: str | None = Field(default=None, alias="type", min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, alias="correlationId", max_length=128)
    payload: dict[str, object] = Field(default_factory=dict)
    reply_to: str | None = Field(default=None, alias="replyTo", max_length=64)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)


class MailboxAcknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    delivery_ids: list[str] = Field(alias="deliveryIds", min_length=1, max_length=100)


@dataclass(frozen=True)
class CollaborationPrincipal:
    parent_sandbox_name: str | None = None
    actor: CollaborationActor | None = None


def _handle(job: Job, *, include_output: bool = False) -> dict[str, object]:
    public_state = (
        "queued"
        if job.state in {"admitting", "queued", "preparing", "prepared"}
        else job.state
    )
    result: dict[str, object] = {
        "providerJobId": job.id,
        "state": public_state,
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


def _participant_handle(participant: CollaborationParticipant) -> dict[str, object]:
    return {
        "participantId": participant.id,
        "role": participant.role,
        "roleName": participant.alias,
        "sandboxName": participant.sandbox_name,
        "jobId": participant.job_id,
        "status": participant.status,
        "lifecycleState": participant.lifecycle_state,
        "runId": participant.run_id,
        "createdAt": participant.created_at,
    }


def _message_handle(message: CollaborationMessage) -> dict[str, object]:
    return {
        "messageId": message.id,
        "runId": message.run_id,
        "sequence": message.sequence,
        "sender": {
            "participantId": message.sender_id,
            "roleName": message.sender_alias,
            "sandboxName": message.sender_sandbox_name,
        },
        "recipient": {
            "participantId": message.recipient_id,
            "roleName": message.recipient_alias,
            "sandboxName": message.recipient_sandbox_name,
        },
        "kind": message.kind,
        "body": message.body,
        "replyTo": message.reply_to,
        "createdAt": message.created_at,
        "envelope": {
            "version": message.version,
            "type": message.message_type,
            "correlationId": message.correlation_id,
            "payload": message.payload,
            "text": message.body,
        },
    }


def _terminal_error_handle(error: CollaborationTerminalError) -> dict[str, object]:
    message = (
        f"Expected worker {error.sender_alias} failed"
        if error.state == "failed"
        else f"Expected worker {error.sender_alias} finished without sending a message"
    )
    if error.reason:
        message = f"{message}: {error.reason}"
    return {
        "code": error.code,
        "sender": {
            "roleName": error.sender_alias,
            "sandboxName": error.sender_sandbox_name,
        },
        "state": error.state,
        "reason": error.reason,
        "message": message,
    }


def _delivery_handle(delivery: CollaborationDelivery) -> dict[str, object]:
    return {
        "deliveryId": delivery.id,
        "state": delivery.state,
        "message": _message_handle(delivery.message),
        "createdAt": delivery.created_at,
    }


def _timeline_event_handle(event) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "groupId": event.group_id,
        "runId": event.run_id,
        "parentSandboxName": event.parent_sandbox_name,
        "eventType": event.event_type,
        "participant": (
            {
                "participantId": event.participant_id,
                "role": event.participant_role,
                "roleName": event.participant_alias,
                "sandboxName": event.participant_sandbox_name,
            }
            if event.participant_id
            else None
        ),
        "jobId": event.job_id,
        "deliveryId": event.delivery_id,
        "payload": event.payload,
        "message": _message_handle(event.message) if event.message else None,
        "createdAt": event.created_at,
    }


def _watch_job_handle(job: Job) -> dict[str, object]:
    return {
        "providerJobId": job.id,
        "runId": job.run_id,
        "startMode": job.start_mode,
        "expectedWorkers": job.expected_workers,
        "parentSandboxName": job.caller_id,
        "roleName": job.participant_alias,
        "state": job.state,
        "sandboxName": job.sandbox_name,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "exitCode": job.exit_code,
        "failureCode": job.failure_code,
        "failureMessage": job.failure_message,
        "cleanupError": job.cleanup_error,
        "output": job.output,
        "stderr": job.stderr,
        "sandboxLogs": job.sandbox_logs,
        "sandboxLogError": job.sandbox_log_error,
    }


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
    collaboration = CollaborationStore(
        settings.database_path, run_ttl_seconds=settings.collaboration_run_ttl_seconds
    )
    cached_reviewer = CachingPolicyReviewer(policy_reviewer)
    resolved_parent_source = parent_policy_source or OpenShellCliParentPolicySource(settings)
    cached_parent_source = CachingParentPolicySource(resolved_parent_source)
    service = ToolService(
        store,
        runtime or OpenShellCliRuntime(settings),
        cached_reviewer,
        cached_parent_source,
        collaboration,
        settings.collaboration_url,
        create_concurrency=settings.create_concurrency,
        max_active_workers=settings.max_active_workers,
        workflow_ready_timeout_seconds=settings.workflow_ready_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="OpenShell Tool Service", version="0.1.0", lifespan=lifespan)
    app.state.tool_service = service

    def caller_id(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token_matches(supplied, settings.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid Tool Service token",
            )
        return "sandbox-1"

    def collaboration_actor(
        authorization: Annotated[str | None, Header()] = None,
        caller_sandbox_name: Annotated[
            str | None, Header(alias="X-POC-Caller-Sandbox-Name")
        ] = None,
    ) -> CollaborationPrincipal:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing collaboration bearer token")
        if token_matches(supplied, settings.token):
            sandbox_name = (caller_sandbox_name or "").strip()
            if not sandbox_name:
                raise HTTPException(
                    status_code=400,
                    detail="X-POC-Caller-Sandbox-Name is required for the parent",
                )
            return CollaborationPrincipal(parent_sandbox_name=sandbox_name)
        actor = collaboration.actor_for_token(supplied)
        if actor is None:
            raise HTTPException(status_code=401, detail="invalid collaboration participant token")
        return CollaborationPrincipal(actor=actor)

    def current_service(request: Request) -> ToolService:
        return request.app.state.tool_service

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/watch", include_in_schema=False)
    def watch_ui() -> FileResponse:
        return FileResponse(WATCH_UI_PATH)

    @app.get("/v1/watch/timeline")
    async def watch_timeline(
        parent: str,
        _authenticated: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
        after: int = 0,
        limit: int = 500,
    ) -> dict[str, object]:
        if not parent.strip():
            raise HTTPException(status_code=400, detail="parent is required")
        if after < 0:
            raise HTTPException(status_code=400, detail="after must be zero or greater")
        if limit < 1 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
        events = await asyncio.to_thread(
            tool_service.collaboration.timeline_after,
            after,
            parent_sandbox_name=parent.strip(),
        )
        bounded = events[:limit]
        return {
            "events": [_timeline_event_handle(event) for event in bounded],
            "lastSequence": bounded[-1].sequence if bounded else after,
            "hasMore": len(events) > limit,
        }

    @app.get("/v1/watch/jobs/{job_id}")
    async def watch_job(
        job_id: str,
        _authenticated: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = await asyncio.to_thread(tool_service.store.get, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _watch_job_handle(job)

    @app.get("/v1/watch/network-flow")
    async def watch_network_flow(
        parent: str,
        run: str,
        _authenticated: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        parent = parent.strip()
        run = run.strip()
        if not parent or not run:
            raise HTTPException(status_code=400, detail="parent and run are required")
        events = await asyncio.to_thread(
            tool_service.collaboration.timeline_after,
            0,
            parent_sandbox_name=parent,
        )
        run_events = [event for event in events if event.run_id == run]
        job_ids = list(dict.fromkeys(event.job_id for event in run_events if event.job_id))
        jobs = await asyncio.to_thread(tool_service.store.get_many, job_ids)
        generated_at = time.time()
        spans = await asyncio.to_thread(
            build_network_flow, run_events, jobs, now=generated_at
        )
        return {
            "runId": run,
            "spans": [span.handle() for span in spans],
            "generatedAt": generated_at,
        }

    @app.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        request: JobCreate,
        _authenticated: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        try:
            job = await tool_service.submit(
                caller_id=request.caller.sandbox_name,
                run_id=request.workflow.id,
                start_mode=request.workflow.start_mode,
                expected_workers=request.workflow.expected_workers,
                step_index=request.worker.step_index,
                idempotency_key=request.idempotency_key,
                prompt=request.worker.prompt,
                child_policy=request.worker.resources.child_policy,
                participant_alias=request.worker.role,
            )
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except JobQueueFullError as error:
            raise HTTPException(
                status_code=429,
                detail={"code": "worker-capacity", "message": str(error)},
                headers={"Retry-After": "5"},
            ) from error
        except WorkflowConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "workflow-conflict", "message": str(error)},
            ) from error
        except CollaborationConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except CollaborationError as error:
            raise HTTPException(
                status_code=400,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _handle(job)

    @app.post("/v1/jobs/status")
    async def get_job_status_batch(
        request: JobStatusBatch,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        jobs = await asyncio.to_thread(tool_service.store.get_many, request.job_ids)
        return {"jobs": [_handle(job) for job in jobs]}

    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        job_id: str,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = await asyncio.to_thread(tool_service.store.get, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _handle(job)

    @app.get("/v1/jobs/{job_id}/result")
    async def get_result(
        job_id: str,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = await asyncio.to_thread(tool_service.store.get, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.state not in {"completed", "failed", "stopped", "blocked"}:
            raise HTTPException(status_code=409, detail="job is not complete")
        return _handle(job, include_output=True)

    @app.get("/v1/jobs/{job_id}/logs")
    async def get_job_logs(
        job_id: str,
        _caller: Annotated[str, Depends(caller_id)],
        tool_service: Annotated[ToolService, Depends(current_service)],
    ) -> dict[str, object]:
        job = await asyncio.to_thread(tool_service.store.get, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "providerJobId": job.id,
            "sandboxName": job.sandbox_name,
            "logs": job.sandbox_logs or "",
            "captureError": job.sandbox_log_error,
        }

    @app.get("/v1/collaboration/participants")
    def list_participants(
        principal: Annotated[CollaborationPrincipal, Depends(collaboration_actor)],
        include_finished: bool = False,
    ) -> dict[str, object]:
        if principal.actor:
            participants = collaboration.list_participants(
                principal.actor, include_finished=include_finished
            )
            group_id: str | None = principal.actor.group_id
            self_id: str | None = principal.actor.participant_id
        else:
            participants = collaboration.list_parent_participants(
                principal.parent_sandbox_name or "", include_finished=include_finished
            )
            group_id = None
            self_id = None
        return {
            "groupId": group_id,
            "selfParticipantId": self_id,
            "participants": [_participant_handle(item) for item in participants],
        }

    @app.post("/v1/collaboration/messages", status_code=status.HTTP_201_CREATED)
    def send_message(
        request: MessageCreate,
        principal: Annotated[CollaborationPrincipal, Depends(collaboration_actor)],
    ) -> dict[str, object]:
        try:
            arguments = dict(
                recipient=request.recipient,
                body=request.body,
                kind=request.kind,
                reply_to=request.reply_to,
                idempotency_key=request.idempotency_key,
                message_type=request.message_type,
                correlation_id=request.correlation_id,
                payload=request.payload,
            )
            message = (
                collaboration.send(actor=principal.actor, **arguments)
                if principal.actor
                else collaboration.parent_send(principal.parent_sandbox_name or "", **arguments)
            )
        except CollaborationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except CollaborationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CollaborationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        logger.info(
            "collaboration message %s stored "
            "(group=%s, sender=%s, recipient=%s, kind=%s, body_bytes=%d)",
            message.id[:8],
            message.group_id[:8],
            message.sender_sandbox_name,
            message.recipient_sandbox_name,
            message.kind,
            len(message.body.encode("utf-8")),
        )
        return _message_handle(message)

    @app.get("/v1/collaboration/messages")
    async def wait_for_messages(
        principal: Annotated[CollaborationPrincipal, Depends(collaboration_actor)],
        after: int = 0,
        wait: float = 0,
    ) -> dict[str, object]:
        if after < 0:
            raise HTTPException(status_code=400, detail="after must be zero or greater")
        if wait < 0 or wait > 30:
            raise HTTPException(status_code=400, detail="wait must be between 0 and 30 seconds")
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            if principal.actor:
                messages = await asyncio.to_thread(
                    collaboration.inbox, principal.actor, after
                )
            else:
                deliveries = await asyncio.to_thread(
                    collaboration.parent_mailbox,
                    principal.parent_sandbox_name or "",
                )
                messages = [item.message for item in deliveries if item.message.sequence > after]
            if messages or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.2)
        logger.debug(
            "collaboration inbox read (group=%s, participant=%s, after=%d, returned=%d)",
            principal.actor.group_id[:8] if principal.actor else "multiple",
            principal.actor.participant_id[:8] if principal.actor else "parent",
            after,
            len(messages),
        )
        return {
            "messages": [_message_handle(item) for item in messages],
            "lastSequence": messages[-1].sequence if messages else after,
        }

    @app.get("/v1/collaboration/mailbox")
    async def automatic_mailbox(
        principal: Annotated[CollaborationPrincipal, Depends(collaboration_actor)],
        wait: float = 0,
        sender: str | None = None,
    ) -> dict[str, object]:
        if wait < 0 or wait > 30:
            raise HTTPException(status_code=400, detail="wait must be between 0 and 30 seconds")
        deadline = asyncio.get_running_loop().time() + wait
        terminal_error = None
        deliveries = []
        try:
            while True:
                if principal.actor:
                    deliveries = await asyncio.to_thread(
                        collaboration.mailbox, principal.actor
                    )
                else:
                    deliveries = await asyncio.to_thread(
                        collaboration.parent_mailbox,
                        principal.parent_sandbox_name or "",
                    )
                if sender:
                    deliveries = [
                        item
                        for item in deliveries
                        if sender
                        in {
                            item.message.sender_id,
                            item.message.sender_alias,
                            item.message.sender_sandbox_name,
                        }
                    ]
                expected_delivery = bool(sender and deliveries)
                if principal.actor and sender and not expected_delivery:
                    terminal_error = await asyncio.to_thread(
                        collaboration.expected_sender_terminal,
                        principal.actor,
                        sender,
                    )
                if deliveries or terminal_error or asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            # Uvicorn cancels outstanding long polls after its bounded graceful
            # shutdown window. An empty response is the normal shutdown outcome.
            logger.debug("collaboration mailbox long poll cancelled during shutdown")
        return {
            "deliveries": [_delivery_handle(item) for item in deliveries],
            "terminalError": (
                _terminal_error_handle(terminal_error) if terminal_error else None
            ),
        }

    @app.post("/v1/collaboration/mailbox/ack")
    def acknowledge_automatic_mailbox(
        request: MailboxAcknowledge,
        principal: Annotated[CollaborationPrincipal, Depends(collaboration_actor)],
    ) -> dict[str, object]:
        try:
            acknowledged = (
                collaboration.acknowledge_deliveries(principal.actor, request.delivery_ids)
                if principal.actor
                else collaboration.acknowledge_parent_deliveries(
                    principal.parent_sandbox_name or "", request.delivery_ids
                )
            )
        except CollaborationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"acknowledgedDeliveryIds": acknowledged}

    return app
