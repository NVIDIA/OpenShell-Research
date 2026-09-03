"""Build a small, read-only network view from the POC event journal and logs."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from openshell_tool_service.collaboration import CollaborationTimelineEvent
from openshell_tool_service.store import Job

INFERENCE_SUCCESS = re.compile(
    r"^\[(?P<at>\d+(?:\.\d+)?)\].*API:INFERENCE.*"
    r"(?P<status>Success|Failure) (?P<model>\S+) via (?P<endpoint>\S+) "
    r"(?P<duration>\d+)ms"
)
INFERENCE_CONNECTION_FAILURE = re.compile(
    r"^\[(?P<at>\d+(?:\.\d+)?)\].*DENIED inference\.local:443 "
    r"\[reason:(?P<reason>[^]]+)\]"
)


@dataclass(frozen=True)
class NetworkSpan:
    id: str
    run_id: str
    job_id: str | None
    role_name: str | None
    source: str
    target: str
    via: str | None
    label: str
    status: str
    started_at: float
    ended_at: float
    duration_ms: int | None
    timing_source: str
    detail: str | None = None

    def handle(self) -> dict[str, Any]:
        value = asdict(self)
        return {
            "id": value["id"],
            "runId": value["run_id"],
            "jobId": value["job_id"],
            "roleName": value["role_name"],
            "source": value["source"],
            "target": value["target"],
            "via": value["via"],
            "label": value["label"],
            "status": value["status"],
            "startedAt": value["started_at"],
            "endedAt": value["ended_at"],
            "durationMs": value["duration_ms"],
            "timingSource": value["timing_source"],
            "detail": value["detail"],
        }


def _duration_ms(started_at: float, ended_at: float) -> int:
    return max(0, round((ended_at - started_at) * 1000))


def _role(event: CollaborationTimelineEvent, job: Job | None) -> str:
    return event.participant_alias or (job.participant_alias if job else "worker")


def _paired_span(
    *,
    started: CollaborationTimelineEvent,
    ended: CollaborationTimelineEvent,
    job: Job | None,
    source: str,
    target: str,
    via: str | None,
    label: str,
    timing_source: str = "Tool Service event timestamps",
) -> NetworkSpan:
    failed = any(token in ended.event_type for token in ("failed", "denied"))
    detail = ended.payload.get("reason") or ended.payload.get("error")
    return NetworkSpan(
        id=f"{started.sequence}:{ended.sequence}:{label}",
        run_id=started.run_id,
        job_id=started.job_id,
        role_name=_role(started, job),
        source=source,
        target=target,
        via=via,
        label=label,
        status="failed" if failed else "success",
        started_at=started.created_at,
        ended_at=ended.created_at,
        duration_ms=_duration_ms(started.created_at, ended.created_at),
        timing_source=timing_source,
        detail=str(detail) if detail else None,
    )


def _inference_spans(job: Job) -> list[NetworkSpan]:
    spans: list[NetworkSpan] = []
    for index, line in enumerate((job.sandbox_logs or "").splitlines()):
        match = INFERENCE_SUCCESS.search(line)
        if match:
            ended_at = float(match.group("at"))
            duration_ms = int(match.group("duration"))
            spans.append(
                NetworkSpan(
                    id=f"{job.id}:inference:{index}",
                    run_id=job.run_id,
                    job_id=job.id,
                    role_name=job.participant_alias,
                    source=job.participant_alias,
                    target="Inference",
                    via="OpenShell inference router",
                    label=f"{match.group('model')} response",
                    status=(
                        "success" if match.group("status") == "Success" else "failed"
                    ),
                    started_at=ended_at - duration_ms / 1000,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    timing_source="OpenShell API:INFERENCE latency",
                    detail=match.group("endpoint"),
                )
            )
            continue
        failure = INFERENCE_CONNECTION_FAILURE.search(line)
        if failure:
            at = float(failure.group("at"))
            spans.append(
                NetworkSpan(
                    id=f"{job.id}:inference-failure:{index}",
                    run_id=job.run_id,
                    job_id=job.id,
                    role_name=job.participant_alias,
                    source=job.participant_alias,
                    target="Inference",
                    via="OpenShell inference router",
                    label="TLS connection",
                    status="failed",
                    started_at=at,
                    ended_at=at,
                    duration_ms=None,
                    timing_source="OpenShell sandbox log",
                    detail=failure.group("reason"),
                )
            )
    return spans


def build_network_flow(
    events: list[CollaborationTimelineEvent], jobs: list[Job]
) -> list[NetworkSpan]:
    """Derive operator-facing hops without introducing another tracing store."""

    jobs_by_id = {job.id: job for job in jobs}
    by_job: dict[str, list[CollaborationTimelineEvent]] = defaultdict(list)
    for event in events:
        if event.job_id:
            by_job[event.job_id].append(event)

    spans: list[NetworkSpan] = []
    pair_specs = (
        (
            "parent-policy.lookup.started",
            {"parent-policy.lookup.completed", "parent-policy.lookup.failed"},
            "Tool Service",
            "OpenShell",
            None,
            "get parent policy",
        ),
        (
            "policy.review.started",
            {"policy.review.allowed", "policy.review.denied", "policy.review.failed"},
            "Tool Service",
            "Policy Reviewer",
            "Inference API",
            "review child policy",
        ),
        (
            "sandbox.create.requested",
            {"sandbox.ready", "participant.failed"},
            "Tool Service",
            "OpenShell",
            None,
            "create sandbox and wait Ready",
        ),
        (
            "pi.execution.started",
            {"pi.execution.completed", "pi.execution.failed"},
            "Tool Service",
            "Child Pi",
            "OpenShell exec",
            "run delegated task",
        ),
    )
    for job_id, job_events in by_job.items():
        job = jobs_by_id.get(job_id)
        accepted = next(
            (event for event in job_events if event.event_type == "job.accepted"), None
        )
        if accepted is not None:
            spans.append(
                NetworkSpan(
                    id=f"{accepted.sequence}:job-submit",
                    run_id=accepted.run_id,
                    job_id=accepted.job_id,
                    role_name=_role(accepted, job),
                    source="Parent Pi",
                    target="Tool Service",
                    via="pi-subagents external-job adapter",
                    label=f"delegate {_role(accepted, job)}",
                    status="success",
                    started_at=accepted.created_at,
                    ended_at=accepted.created_at,
                    duration_ms=None,
                    timing_source="Acceptance timestamp only",
                )
            )
        for start_type, end_types, source, target, via, label in pair_specs:
            started = next((event for event in job_events if event.event_type == start_type), None)
            if started is None:
                continue
            ended = next(
                (
                    event
                    for event in job_events
                    if event.sequence > started.sequence and event.event_type in end_types
                ),
                None,
            )
            if ended is None:
                continue
            resolved_target = _role(started, job) if target == "Child Pi" else target
            spans.append(
                _paired_span(
                    started=started,
                    ended=ended,
                    job=job,
                    source=source,
                    target=resolved_target,
                    via=via,
                    label=label,
                )
            )

    stored_by_delivery = {
        event.delivery_id: event
        for event in events
        if event.event_type == "message.stored" and event.delivery_id and event.message
    }
    for event in events:
        if event.event_type != "delivery.acknowledged" or not event.delivery_id:
            continue
        stored = stored_by_delivery.get(event.delivery_id)
        if stored is None or stored.message is None:
            continue
        message = stored.message
        spans.append(
            NetworkSpan(
                id=f"{stored.sequence}:message-store",
                run_id=stored.run_id,
                job_id=stored.job_id,
                role_name=message.sender_alias,
                source=message.sender_alias,
                target="Tool Service",
                via=None,
                label=f"store {message.kind}",
                status="success",
                started_at=stored.created_at,
                ended_at=stored.created_at,
                duration_ms=None,
                timing_source="Arrival timestamp only",
                detail=f"to {message.recipient_alias}",
            )
        )
        spans.append(
            NetworkSpan(
                id=f"{event.sequence}:message-delivery",
                run_id=event.run_id,
                job_id=stored.job_id,
                role_name=message.recipient_alias,
                source="Tool Service",
                target=message.recipient_alias,
                via=None,
                label=f"deliver {message.kind}",
                status="success",
                started_at=stored.created_at,
                ended_at=event.created_at,
                duration_ms=_duration_ms(stored.created_at, event.created_at),
                timing_source="Tool Service store-to-ack timestamps",
                detail=f"from {message.sender_alias}",
            )
        )

    for job in jobs:
        spans.extend(_inference_spans(job))
    return sorted(spans, key=lambda span: (span.started_at, span.ended_at, span.id))
