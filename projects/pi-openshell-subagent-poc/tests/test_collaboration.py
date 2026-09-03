from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openshell_tool_service.collaboration import (
    CollaborationConflictError,
    CollaborationNotFoundError,
    CollaborationRoleConflictError,
    CollaborationStore,
)


def child(
    store: CollaborationStore,
    *,
    parent: str,
    job: str,
    sandbox: str,
    alias: str | None = None,
):
    credentials = store.register_child(
        parent_sandbox_name=parent,
        job_id=job,
        sandbox_name=sandbox,
        participant_alias=alias or sandbox,
        service_url="http://host.openshell.internal:8765",
    )
    actor = store.actor_for_token(credentials.token)
    assert actor is not None
    return credentials, actor


def test_parent_and_siblings_can_exchange_messages(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    _credentials_a, child_a = child(store, parent="pi-parent", job="job-a", sandbox="pi-child-a")
    _credentials_b, child_b = child(store, parent="pi-parent", job="job-b", sandbox="pi-child-b")

    question = store.send(
        actor=child_a,
        recipient="pi-child-b",
        kind="question",
        body="What did you find?",
        reply_to=None,
        idempotency_key="question-1",
    )
    assert store.inbox(child_b, after=0) == [question]

    answer = store.send(
        actor=child_b,
        recipient=child_a.participant_id,
        kind="result",
        body="The parser is the main component.",
        reply_to=question.id,
        idempotency_key="answer-1",
    )
    assert store.inbox(child_a, after=question.sequence) == [answer]

    update = store.send(
        actor=child_a,
        recipient="parent",
        kind="progress",
        body="Sibling review received.",
        reply_to=None,
        idempotency_key="update-1",
    )
    assert store.inbox(parent, after=0) == [update]


def test_cross_parent_messages_are_rejected(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    _credentials_a, child_a = child(store, parent="parent-a", job="job-a", sandbox="child-a")
    _credentials_b, child_b = child(store, parent="parent-b", job="job-b", sandbox="child-b")

    with pytest.raises(CollaborationNotFoundError, match="not in"):
        store.send(
            actor=child_a,
            recipient=child_b.participant_id,
            kind="message",
            body="This must not cross groups.",
            reply_to=None,
            idempotency_key="cross-group",
        )


def test_message_send_is_idempotent_and_conflicts_on_changed_content(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    _credentials, child_actor = child(store, parent="pi-parent", job="job-a", sandbox="pi-child-a")

    first = store.send(
        actor=parent,
        recipient=child_actor.participant_id,
        kind="message",
        body="Please continue.",
        reply_to=None,
        idempotency_key="send-1",
    )
    repeated = store.send(
        actor=parent,
        recipient=child_actor.participant_id,
        kind="message",
        body="Please continue.",
        reply_to=None,
        idempotency_key="send-1",
    )
    assert repeated.id == first.id

    with pytest.raises(CollaborationConflictError, match="different message"):
        store.send(
            actor=parent,
            recipient=child_actor.participant_id,
            kind="message",
            body="Changed content.",
            reply_to=None,
            idempotency_key="send-1",
        )


def test_finished_child_token_and_destination_are_disabled(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    credentials, child_actor = child(store, parent="pi-parent", job="job-a", sandbox="pi-child-a")
    store.finish_child(child_actor.participant_id)

    assert store.actor_for_token(credentials.token) is None
    assert [item.role for item in store.list_participants(parent)] == ["parent"]
    assert [item.status for item in store.list_participants(parent, include_finished=True)] == [
        "active",
        "finished",
    ]
    with pytest.raises(CollaborationConflictError, match="no longer active"):
        store.send(
            actor=parent,
            recipient=child_actor.participant_id,
            kind="message",
            body="Too late.",
            reply_to=None,
            idempotency_key="late-message",
        )


def test_participant_listing_exposes_no_tokens(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    credentials, _child_actor = child(store, parent="pi-parent", job="job-a", sandbox="pi-child-a")

    participants = store.list_participants(parent)
    assert [participant.role for participant in participants] == ["parent", "child"]
    assert credentials.token not in repr(participants)


def test_reserved_role_receives_messages_before_child_starts(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    reserved = store.reserve_child(
        parent_sandbox_name="pi-parent",
        job_id="job-a",
        sandbox_name="pi-child-a",
        participant_alias="reviewer-a",
    )

    sent = store.send(
        actor=parent,
        recipient="reviewer-a",
        kind="question",
        body="Review the parser.",
        reply_to=None,
        idempotency_key="queued-message",
    )
    credentials = store.activate_child(
        job_id="job-a", service_url="http://host.openshell.internal:8765"
    )
    actor = store.actor_for_token(credentials.token)

    assert actor is not None
    assert reserved.alias == "reviewer-a"
    assert credentials.participant_alias == "reviewer-a"
    assert [item.message for item in store.mailbox(actor)] == [sent]


def test_mailbox_replays_until_acknowledged(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")
    _credentials, actor = child(
        store,
        parent="pi-parent",
        job="job-a",
        sandbox="pi-child-a",
        alias="worker-a",
    )
    message = store.send(
        actor=actor,
        recipient="parent",
        kind="result",
        body="done",
        reply_to=None,
        idempotency_key="result-1",
    )

    first = store.mailbox(parent)
    assert [item.message for item in first] == [message]
    assert store.mailbox(parent) == first
    assert store.acknowledge_deliveries(parent, [first[0].id]) == [first[0].id]
    assert store.mailbox(parent) == []


def test_active_role_names_are_unique_within_a_parent(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    store.reserve_child(
        parent_sandbox_name="pi-parent",
        job_id="job-a",
        sandbox_name="pi-child-a",
        participant_alias="reviewer",
    )

    with pytest.raises(CollaborationRoleConflictError) as raised:
        store.reserve_child(
            parent_sandbox_name="pi-parent",
            job_id="job-b",
            sandbox_name="pi-child-b",
            participant_alias="reviewer",
        )
    assert raised.value.code == "collaboration-role-conflict"


def test_group_can_reserve_more_than_eight_children(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent")

    for index in range(12):
        store.reserve_child(
            parent_sandbox_name="pi-parent",
            job_id=f"job-{index}",
            sandbox_name=f"pi-child-{index}",
            participant_alias=f"worker-{index}",
        )

    participants = store.list_participants(parent)
    assert len(participants) == 13
    assert participants[-1].alias == "worker-11"


def test_role_names_are_scoped_to_a_single_workflow_run(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    first = store.reserve_child(
        parent_sandbox_name="pi-parent",
        run_id="run-a",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="reviewer",
    )
    second = store.reserve_child(
        parent_sandbox_name="pi-parent",
        run_id="run-b",
        job_id="job-b",
        sandbox_name="child-b",
        participant_alias="reviewer",
    )

    assert first.group_id != second.group_id
    assert first.run_id == "run-a"
    assert second.run_id == "run-b"


def test_existing_database_schema_accepts_new_workflow_groups(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE collaboration_groups (
            id TEXT PRIMARY KEY,
            parent_sandbox_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
            )"""
        )

    store = CollaborationStore(database)
    actor = store.parent_actor("pi-parent", "new-run")

    assert actor.run_id == "new-run"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status,run_id FROM collaboration_groups WHERE id=?", (actor.group_id,)
        ).fetchone() == ("active", "new-run")


def test_each_delivery_is_acknowledged_independently_and_closes_run(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    store = CollaborationStore(database)
    parent = store.parent_actor("pi-parent", "run-a")
    _credentials, actor = child(
        store, parent="pi-parent", job="job-a", sandbox="child-a", alias="worker-a"
    )
    # The helper uses the manual run, so use a second parent/child pair explicitly.
    store.finish_child(actor.participant_id)
    credentials = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-a",
        job_id="job-b",
        sandbox_name="child-b",
        participant_alias="worker-b",
        service_url="http://example.test",
    )
    worker = store.actor_for_token(credentials.token)
    assert worker is not None
    first = store.send(
        actor=worker,
        recipient="parent",
        kind="progress",
        body="one",
        reply_to=None,
        idempotency_key="one",
    )
    second = store.send(
        actor=worker,
        recipient="parent",
        kind="result",
        body="two",
        reply_to=None,
        idempotency_key="two",
        message_type="review.complete",
        correlation_id="review-42",
        payload={"findings": 2},
    )
    store.finish_job("job-b")

    deliveries = store.mailbox(parent)
    assert [item.message.id for item in deliveries] == [first.id, second.id]
    assert deliveries[1].message.message_type == "review.complete"
    assert deliveries[1].message.correlation_id == "review-42"
    assert deliveries[1].message.payload == {"findings": 2}
    store.acknowledge_deliveries(parent, [deliveries[0].id])
    assert [item.id for item in store.mailbox(parent)] == [deliveries[1].id]
    store.acknowledge_deliveries(parent, [deliveries[1].id])
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT status FROM collaboration_groups WHERE id=?", (parent.group_id,)
            ).fetchone()[0]
            == "closed"
        )


def test_failed_worker_makes_queued_mail_undeliverable_without_synthetic_mail(
    tmp_path: Path,
) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent", "run-failure")
    store.reserve_child(
        parent_sandbox_name="pi-parent",
        run_id="run-failure",
        job_id="job-failure",
        sandbox_name="child-failure",
        participant_alias="worker-failure",
    )
    store.send(
        actor=parent,
        recipient="worker-failure",
        kind="question",
        body="start",
        reply_to=None,
        idempotency_key="queued",
    )
    store.fail_job("job-failure", "sandbox create failed")

    assert store.mailbox(parent) == []
    assert "delivery.undeliverable" in {
        event.event_type for event in store.timeline_after(0, parent_sandbox_name="pi-parent")
    }


def test_failed_worker_does_not_manufacture_failure_messages(
    tmp_path: Path,
) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent", "run-siblings")
    credentials_a = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-siblings",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="worker-a",
        service_url="http://example.test",
    )
    credentials_b = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-siblings",
        job_id="job-b",
        sandbox_name="child-b",
        participant_alias="worker-b",
        service_url="http://example.test",
    )
    credentials_c = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-siblings",
        job_id="job-c",
        sandbox_name="child-c",
        participant_alias="worker-c",
        service_url="http://example.test",
    )
    actor_a = store.actor_for_token(credentials_a.token)
    actor_b = store.actor_for_token(credentials_b.token)
    actor_c = store.actor_for_token(credentials_c.token)
    assert actor_a and actor_b and actor_c
    store.send(
        actor=actor_a,
        recipient="worker-b",
        kind="question",
        body="Please reply.",
        reply_to=None,
        idempotency_key="question-b",
    )

    store.fail_job("job-b", "sandbox transport failed")

    assert store.mailbox(actor_a) == []
    assert store.mailbox(actor_c) == []
    assert store.mailbox(parent) == []


def test_expected_sender_terminal_reports_failure_or_finished_without_message(
    tmp_path: Path,
) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    credentials_a = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-terminal",
        job_id="job-a",
        sandbox_name="child-a",
        participant_alias="worker-a",
        service_url="http://example.test",
    )
    credentials_b = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-terminal",
        job_id="job-b",
        sandbox_name="child-b",
        participant_alias="worker-b",
        service_url="http://example.test",
    )
    credentials_c = store.register_child(
        parent_sandbox_name="pi-parent",
        run_id="run-terminal",
        job_id="job-c",
        sandbox_name="child-c",
        participant_alias="worker-c",
        service_url="http://example.test",
    )
    actor_a = store.actor_for_token(credentials_a.token)
    actor_b = store.actor_for_token(credentials_b.token)
    actor_c = store.actor_for_token(credentials_c.token)
    assert actor_a and actor_b and actor_c

    store.fail_job("job-b", "child process exited")
    store.finish_job("job-c")

    failed = store.expected_sender_terminal(actor_a, "worker-b")
    finished = store.expected_sender_terminal(actor_a, "worker-c")
    assert failed is not None
    assert failed.code == "expected-sender-failed"
    assert failed.reason == "child process exited"
    assert finished is not None
    assert finished.code == "expected-sender-finished-without-message"
    assert finished.reason is None


def test_recovery_failure_finishes_worker_without_synthetic_parent_mail(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "jobs.sqlite3")
    parent = store.parent_actor("pi-parent", "run-recovery")
    store.reserve_child(
        parent_sandbox_name="pi-parent",
        run_id="run-recovery",
        job_id="job-expired",
        sandbox_name="child-expired",
        participant_alias="worker-expired",
    )

    store.fail_job("job-expired", "job expired before execution")

    assert store.mailbox(parent) == []
    events = store.timeline_after(0, parent_sandbox_name="pi-parent")
    assert "participant.failed" in {event.event_type for event in events}
    assert all(event.message is None for event in events)
