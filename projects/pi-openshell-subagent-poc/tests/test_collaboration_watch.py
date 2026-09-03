from __future__ import annotations

from pathlib import Path

from openshell_tool_service.collaboration import CollaborationStore
from openshell_tool_service.collaboration_watch import (
    CollaborationReader,
    format_event,
    visible_by_default,
)


def test_reader_filters_unified_timeline_by_parent(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    store = CollaborationStore(database)
    parent_a = store.parent_actor("parent-a")
    store.parent_actor("parent-b")
    child_a_credentials = store.register_child(
        parent_sandbox_name="parent-a",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="worker-a",
        service_url="http://example.test",
    )
    child_b_credentials = store.register_child(
        parent_sandbox_name="parent-b",
        job_id="job-b",
        sandbox_name="child-b",
        participant_alias="worker-b",
        service_url="http://example.test",
    )
    child_a = store.actor_for_token(child_a_credentials.token)
    child_b = store.actor_for_token(child_b_credentials.token)
    assert child_a is not None
    assert child_b is not None

    store.send(
        actor=child_a,
        recipient="parent",
        kind="result",
        body="result from A",
        reply_to=None,
        idempotency_key="message-a",
    )
    store.send(
        actor=child_b,
        recipient="parent",
        kind="progress",
        body="progress from B",
        reply_to=None,
        idempotency_key="message-b",
    )
    store.finish_job("job-a")

    reader = CollaborationReader(database)
    events = reader.events_after(0, parent="parent-a")

    assert [event.event_type for event in events] == [
        "run.created",
        "participant.ready",
        "participant.reserved",
        "participant.ready",
        "sandbox.ready",
        "message.stored",
        "participant.finished",
    ]
    message = next(event for event in events if event.event_type == "message.stored")
    assert events[2].participant_alias == "worker-a"
    assert message.sender_alias == "worker-a"
    assert message.recipient_alias == "parent"
    assert message.body == "result from A"
    assert events[-1].participant_sandbox_name == "child-a"
    assert reader.latest_sequence(parent="parent-a") == events[-1].sequence
    assert parent_a.sandbox_name == "parent-a"


def test_format_event_uses_roles_and_can_hide_message_body(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    store = CollaborationStore(database)
    store.parent_actor("pi-parent")
    credentials = store.register_child(
        parent_sandbox_name="pi-parent",
        job_id="job-a",
        sandbox_name="pi-child-a",
        participant_alias="reviewer",
        service_url="http://example.test",
    )
    child = store.actor_for_token(credentials.token)
    assert child is not None
    store.send(
        actor=child,
        recipient="parent",
        kind="question",
        body="first line\nsecret second line",
        reply_to=None,
        idempotency_key="question-a",
    )
    events = CollaborationReader(database).events_after(0)
    reserved = next(event for event in events if event.event_type == "participant.reserved")
    message = events[-1]

    assert "reviewer reserved" in format_event(reserved)
    assert "reviewer participant.reserved" in format_event(reserved, verbose=True)
    rendered = format_event(message)
    metadata = format_event(message, metadata_only=True)

    assert "reviewer -> parent: first line" in rendered
    assert "    secret second line" in rendered
    assert "question" in metadata
    assert "first line" not in metadata
    assert "secret second line" not in metadata


def test_default_view_hides_internal_events_and_shortens_policy_reason(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    store = CollaborationStore(database)
    store.parent_actor("pi-parent", "run-a")
    store.reserve_child(
        parent_sandbox_name="pi-parent",
        run_id="run-a",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="reviewer",
    )
    store.record_job_event("job-a", "job.accepted")
    store.record_job_event("job-a", "policy.review.allowed", {"reason": "long reason"})
    store.record_job_event("job-a", "workflow.released")

    events = CollaborationReader(database).events_after(0)
    created = next(event for event in events if event.event_type == "run.created")
    accepted = next(event for event in events if event.event_type == "job.accepted")
    allowed = next(event for event in events if event.event_type == "policy.review.allowed")
    released = next(event for event in events if event.event_type == "workflow.released")

    assert not visible_by_default(created)
    assert not visible_by_default(accepted)
    assert visible_by_default(allowed)
    assert visible_by_default(released)
    assert "unknown" not in format_event(created, verbose=True)
    assert "policy allowed" in format_event(allowed)
    assert "long reason" not in format_event(allowed)
    assert "long reason" in format_event(allowed, verbose=True)
    assert "Pi worker starting after all-ready barrier" in format_event(released)
