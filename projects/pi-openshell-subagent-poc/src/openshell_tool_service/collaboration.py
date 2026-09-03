"""Workflow-scoped collaboration mailboxes and event journal for the POC."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A directed full mesh of 64 workers is 64 * 63 = 4,032 messages. Keep the
# journal bounded while leaving room for parent progress/results and retries.
MAX_MESSAGES_PER_GROUP = 8_192
MAX_MESSAGE_BYTES = 64 * 1024
ALLOWED_KINDS = {"message", "progress", "question", "result"}
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class CollaborationError(ValueError):
    code = "collaboration-invalid"


class CollaborationNotFoundError(CollaborationError):
    code = "collaboration-not-found"


class CollaborationConflictError(CollaborationError):
    code = "collaboration-conflict"


class CollaborationRoleError(CollaborationError):
    code = "collaboration-role-invalid"


class CollaborationRoleConflictError(CollaborationConflictError):
    code = "collaboration-role-conflict"


@dataclass(frozen=True)
class CollaborationActor:
    participant_id: str
    group_id: str
    run_id: str
    role: str
    sandbox_name: str


@dataclass(frozen=True)
class ChildCollaboration:
    service_url: str
    participant_id: str
    participant_alias: str
    group_id: str
    token: str
    run_id: str = "manual"


@dataclass(frozen=True)
class CollaborationParticipant:
    id: str
    group_id: str
    run_id: str
    role: str
    alias: str
    sandbox_name: str
    job_id: str | None
    status: str
    lifecycle_state: str
    created_at: float
    finished_at: float | None


@dataclass(frozen=True)
class CollaborationMessage:
    id: str
    sequence: int
    group_id: str
    run_id: str
    sender_id: str
    sender_alias: str
    sender_sandbox_name: str
    recipient_id: str
    recipient_alias: str
    recipient_sandbox_name: str
    kind: str
    body: str
    version: int
    message_type: str
    correlation_id: str
    payload: dict[str, Any]
    reply_to: str | None
    created_at: float


@dataclass(frozen=True)
class CollaborationDelivery:
    id: str
    state: str
    message: CollaborationMessage
    created_at: float
    delivered_at: float | None
    failure_reason: str | None


@dataclass(frozen=True)
class CollaborationTerminalError:
    code: str
    sender_alias: str
    sender_sandbox_name: str
    state: str
    reason: str | None


@dataclass(frozen=True)
class CollaborationTimelineEvent:
    sequence: int
    group_id: str
    run_id: str
    parent_sandbox_name: str
    event_type: str
    participant_id: str | None
    participant_alias: str | None
    participant_role: str | None
    participant_sandbox_name: str | None
    job_id: str | None
    delivery_id: str | None
    payload: dict[str, Any]
    message: CollaborationMessage | None
    created_at: float


class CollaborationStore:
    def __init__(self, path: Path, run_ttl_seconds: int = 3600) -> None:
        self.path = path
        self.run_ttl_seconds = run_ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS collaboration_groups (
                  id TEXT PRIMARY KEY,parent_sandbox_name TEXT NOT NULL,run_id TEXT,
                  status TEXT NOT NULL CHECK(status IN ('active','closed')),
                  created_at REAL NOT NULL,expires_at REAL NOT NULL,closed_at REAL);
                CREATE TABLE IF NOT EXISTS collaboration_participants (
                  id TEXT PRIMARY KEY,group_id TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('parent','child')),alias TEXT,
                  sandbox_name TEXT NOT NULL,job_id TEXT,token_hash TEXT UNIQUE,
                  status TEXT NOT NULL CHECK(status IN ('active','finished','revoked')),
                  lifecycle_state TEXT,created_at REAL NOT NULL,finished_at REAL,
                  mailbox_cursor INTEGER NOT NULL DEFAULT 0,
                  FOREIGN KEY(group_id) REFERENCES collaboration_groups(id),
                  UNIQUE(id,group_id),UNIQUE(group_id,sandbox_name),UNIQUE(job_id));
                CREATE TABLE IF NOT EXISTS collaboration_messages (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,
                  group_id TEXT NOT NULL,sender_id TEXT NOT NULL,recipient_id TEXT NOT NULL,
                  kind TEXT NOT NULL,body TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
                  message_type TEXT,correlation_id TEXT,payload_json TEXT NOT NULL DEFAULT '{}',
                  reply_to TEXT,idempotency_key TEXT NOT NULL,created_at REAL NOT NULL,
                  UNIQUE(sender_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS collaboration_deliveries (
                  id TEXT PRIMARY KEY,message_id TEXT NOT NULL UNIQUE,recipient_id TEXT NOT NULL,
                  state TEXT NOT NULL,created_at REAL NOT NULL,delivered_at REAL,failure_reason TEXT);
                CREATE TABLE IF NOT EXISTS collaboration_timeline_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,
                  group_id TEXT NOT NULL,event_type TEXT NOT NULL,participant_id TEXT,
                  message_id TEXT,job_id TEXT,delivery_id TEXT,
                  event_payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
                """
            )
            self._migrate(db)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}

    def _migrate(self, db: sqlite3.Connection) -> None:
        additions = {
            "collaboration_groups": {"run_id": "TEXT", "closed_at": "REAL"},
            "collaboration_participants": {"lifecycle_state": "TEXT"},
            "collaboration_messages": {
                "version": "INTEGER NOT NULL DEFAULT 1",
                "message_type": "TEXT",
                "correlation_id": "TEXT",
                "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "collaboration_timeline_events": {
                "job_id": "TEXT",
                "delivery_id": "TEXT",
                "event_payload_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        }
        for table, columns in additions.items():
            existing = self._columns(db, table)
            for name, declaration in columns.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        db.execute("DROP INDEX IF EXISTS collaboration_one_active_parent")
        db.execute(
            "UPDATE collaboration_groups SET run_id='legacy-'||substr(id,1,12) WHERE run_id IS NULL OR run_id=''"
        )
        db.execute("UPDATE collaboration_participants SET alias='parent' WHERE role='parent'")
        db.execute(
            "UPDATE collaboration_participants SET alias=sandbox_name WHERE role='child' AND (alias IS NULL OR alias='')"
        )
        db.execute("""UPDATE collaboration_participants SET lifecycle_state=CASE
          WHEN role='parent' THEN 'ready' WHEN status='active' THEN 'ready'
          WHEN status='finished' THEN 'finished' ELSE 'failed' END
          WHERE lifecycle_state IS NULL OR lifecycle_state=''""")
        db.execute(
            "UPDATE collaboration_messages SET message_type='collaboration.'||kind WHERE message_type IS NULL OR message_type=''"
        )
        db.execute(
            "UPDATE collaboration_messages SET correlation_id=id WHERE correlation_id IS NULL OR correlation_id=''"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS collaboration_one_active_run ON collaboration_groups(parent_sandbox_name,run_id) WHERE status='active'"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS collaboration_one_active_alias ON collaboration_participants(group_id,alias) WHERE status='active'"
        )
        db.execute("""INSERT OR IGNORE INTO collaboration_deliveries
          (id,message_id,recipient_id,state,created_at,delivered_at)
          SELECT 'delivery-'||m.id,m.id,m.recipient_id,
          CASE WHEN p.mailbox_cursor>=m.sequence THEN 'delivered' ELSE 'queued' END,
          m.created_at,CASE WHEN p.mailbox_cursor>=m.sequence THEN m.created_at END
          FROM collaboration_messages m JOIN collaboration_participants p ON p.id=m.recipient_id""")

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        group_id: str,
        event_type: str,
        *,
        participant_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
        delivery_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO collaboration_timeline_events
          (id,group_id,event_type,participant_id,message_id,job_id,delivery_id,event_payload_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                group_id,
                event_type,
                participant_id,
                message_id,
                job_id,
                delivery_id,
                json.dumps(payload or {}, separators=(",", ":")),
                now or time.time(),
            ),
        )

    def _expire(self, db: sqlite3.Connection) -> None:
        now = time.time()
        for group in db.execute(
            "SELECT id FROM collaboration_groups WHERE status='active' AND expires_at<=?", (now,)
        ).fetchall():
            db.execute(
                "UPDATE collaboration_deliveries SET state='undeliverable',failure_reason='run TTL expired' WHERE state='queued' AND message_id IN (SELECT id FROM collaboration_messages WHERE group_id=?)",
                (group["id"],),
            )
            db.execute(
                "UPDATE collaboration_participants SET status='finished',lifecycle_state='expired',finished_at=?,token_hash=NULL WHERE group_id=? AND role='child' AND status='active'",
                (now, group["id"]),
            )
            db.execute(
                "UPDATE collaboration_groups SET status='closed',closed_at=? WHERE id=?",
                (now, group["id"]),
            )
            self._event(
                db, group["id"], "run.expired", payload={"reason": "max TTL reached"}, now=now
            )

    def parent_actor(self, sandbox_name: str, run_id: str = "manual") -> CollaborationActor:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            group = db.execute(
                "SELECT * FROM collaboration_groups WHERE parent_sandbox_name=? AND run_id=? AND status='active'",
                (sandbox_name, run_id),
            ).fetchone()
            if group is None:
                group_id, participant_id = uuid.uuid4().hex, uuid.uuid4().hex
                db.execute(
                    """INSERT INTO collaboration_groups
                    (id,parent_sandbox_name,run_id,status,created_at,expires_at,closed_at)
                    VALUES (?,?,?,'active',?,?,NULL)""",
                    (group_id, sandbox_name, run_id, now, now + self.run_ttl_seconds),
                )
                db.execute(
                    "INSERT INTO collaboration_participants(id,group_id,role,alias,sandbox_name,status,lifecycle_state,created_at) VALUES (?,?,'parent','parent',?,'active','ready',?)",
                    (participant_id, group_id, sandbox_name, now),
                )
                self._event(db, group_id, "run.created", payload={"runId": run_id}, now=now)
                self._event(
                    db, group_id, "participant.ready", participant_id=participant_id, now=now
                )
            else:
                group_id = group["id"]
                participant_id = db.execute(
                    "SELECT id FROM collaboration_participants WHERE group_id=? AND role='parent'",
                    (group_id,),
                ).fetchone()["id"]
        return CollaborationActor(participant_id, group_id, run_id, "parent", sandbox_name)

    def parent_actors(self, sandbox_name: str) -> list[CollaborationActor]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            rows = db.execute(
                """SELECT p.id,p.group_id,g.run_id,p.role,p.sandbox_name
              FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id
              WHERE g.parent_sandbox_name=? AND g.status='active' AND p.role='parent' ORDER BY g.created_at""",
                (sandbox_name,),
            ).fetchall()
        return [CollaborationActor(*row) for row in rows]

    @staticmethod
    def _validate_alias(alias: str) -> str:
        value = alias.strip()
        if not ALIAS_PATTERN.fullmatch(value):
            raise CollaborationRoleError(
                "role name must use 1-64 letters, numbers, dots, underscores, or hyphens"
            )
        if value == "parent":
            raise CollaborationRoleError("the role name 'parent' is reserved")
        return value

    @staticmethod
    def _participant(row: sqlite3.Row) -> CollaborationParticipant:
        return CollaborationParticipant(
            row["id"],
            row["group_id"],
            row["run_id"],
            row["role"],
            row["alias"],
            row["sandbox_name"],
            row["job_id"],
            row["status"],
            row["lifecycle_state"],
            row["created_at"],
            row["finished_at"],
        )

    def reserve_child(
        self,
        *,
        parent_sandbox_name: str,
        job_id: str,
        sandbox_name: str,
        participant_alias: str,
        run_id: str = "manual",
    ) -> CollaborationParticipant:
        parent = self.parent_actor(parent_sandbox_name, run_id)
        alias = self._validate_alias(participant_alias)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT p.*,g.run_id FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if existing:
                if existing["alias"] != alias:
                    raise CollaborationRoleConflictError(
                        "the job already has a different collaboration role name"
                    )
                return self._participant(existing)
            participant_id = uuid.uuid4().hex
            try:
                db.execute(
                    "INSERT INTO collaboration_participants(id,group_id,role,alias,sandbox_name,job_id,status,lifecycle_state,created_at) VALUES (?,?,'child',?,?,?,'active','reserved',?)",
                    (participant_id, parent.group_id, alias, sandbox_name, job_id, now),
                )
            except sqlite3.IntegrityError as error:
                raise CollaborationRoleConflictError(
                    f"the collaboration role '{alias}' is already active in this workflow"
                ) from error
            self._event(
                db,
                parent.group_id,
                "participant.reserved",
                participant_id=participant_id,
                job_id=job_id,
                now=now,
            )
            row = db.execute(
                "SELECT p.*,g.run_id FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id WHERE p.id=?",
                (participant_id,),
            ).fetchone()
        return self._participant(row)

    def transition_job(
        self,
        job_id: str,
        state: str,
        *,
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM collaboration_participants WHERE job_id=?", (job_id,)
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE collaboration_participants SET lifecycle_state=? WHERE id=?",
                    (state, row["id"]),
                )
                self._event(
                    db,
                    row["group_id"],
                    event_type or f"participant.{state}",
                    participant_id=row["id"],
                    job_id=job_id,
                    payload=payload,
                )

    def record_job_event(
        self, job_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id,group_id FROM collaboration_participants WHERE job_id=?", (job_id,)
            ).fetchone()
            if row:
                self._event(
                    db,
                    row["group_id"],
                    event_type,
                    participant_id=row["id"],
                    job_id=job_id,
                    payload=payload,
                )

    def activate_child(self, *, job_id: str, service_url: str) -> ChildCollaboration:
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT p.*,g.run_id FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != "active":
                raise CollaborationNotFoundError("the job has no active collaboration reservation")
            db.execute(
                "UPDATE collaboration_participants SET token_hash=? WHERE id=?",
                (hashlib.sha256(token.encode()).hexdigest(), row["id"]),
            )
        return ChildCollaboration(
            service_url=service_url,
            participant_id=row["id"],
            participant_alias=row["alias"],
            group_id=row["group_id"],
            token=token,
            run_id=row["run_id"],
        )

    def mark_ready(self, job_id: str, payload: dict[str, Any] | None = None) -> None:
        self.transition_job(job_id, "ready", event_type="participant.ready")
        self.record_job_event(job_id, "sandbox.ready", payload)

    def register_child(
        self,
        *,
        parent_sandbox_name: str,
        job_id: str,
        sandbox_name: str,
        service_url: str,
        participant_alias: str | None = None,
        run_id: str = "manual",
    ) -> ChildCollaboration:
        self.reserve_child(
            parent_sandbox_name=parent_sandbox_name,
            run_id=run_id,
            job_id=job_id,
            sandbox_name=sandbox_name,
            participant_alias=participant_alias or sandbox_name,
        )
        result = self.activate_child(job_id=job_id, service_url=service_url)
        self.mark_ready(job_id)
        return result

    def actor_for_token(self, token: str) -> CollaborationActor | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            row = db.execute(
                """SELECT p.id,p.group_id,g.run_id,p.role,p.sandbox_name,p.status,g.status group_status
              FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id WHERE p.token_hash=?""",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if row is None or row["status"] != "active" or row["group_status"] != "active":
            return None
        return CollaborationActor(
            row["id"], row["group_id"], row["run_id"], row["role"], row["sandbox_name"]
        )

    def list_participants(
        self, actor: CollaborationActor, *, include_finished: bool = False
    ) -> list[CollaborationParticipant]:
        clause = "" if include_finished else "AND p.status='active'"
        with self._connect() as db:
            rows = db.execute(
                f"SELECT p.*,g.run_id FROM collaboration_participants p JOIN collaboration_groups g ON g.id=p.group_id WHERE p.group_id=? {clause} ORDER BY p.created_at,p.id",
                (actor.group_id,),
            ).fetchall()
        return [self._participant(row) for row in rows]

    def list_parent_participants(
        self, sandbox_name: str, *, include_finished: bool = False
    ) -> list[CollaborationParticipant]:
        return [
            item
            for actor in self.parent_actors(sandbox_name)
            for item in self.list_participants(actor, include_finished=include_finished)
        ]

    @staticmethod
    def _message_query() -> str:
        return """SELECT m.*,g.run_id,s.alias sender_alias,s.sandbox_name sender_sandbox_name,
          r.alias recipient_alias,r.sandbox_name recipient_sandbox_name,r.role recipient_role
          FROM collaboration_messages m JOIN collaboration_groups g ON g.id=m.group_id
          JOIN collaboration_participants s ON s.id=m.sender_id JOIN collaboration_participants r ON r.id=m.recipient_id"""

    @staticmethod
    def _message(row: sqlite3.Row) -> CollaborationMessage:
        return CollaborationMessage(
            row["id"],
            row["sequence"],
            row["group_id"],
            row["run_id"],
            row["sender_id"],
            row["sender_alias"],
            row["sender_sandbox_name"],
            row["recipient_id"],
            row["recipient_alias"],
            row["recipient_sandbox_name"],
            row["kind"],
            row["body"],
            row["version"],
            row["message_type"],
            row["correlation_id"],
            json.loads(row["payload_json"] or "{}"),
            row["reply_to"],
            row["created_at"],
        )

    def _resolve(
        self, db: sqlite3.Connection, actor: CollaborationActor, recipient: str
    ) -> sqlite3.Row:
        if recipient == "parent":
            row = db.execute(
                "SELECT * FROM collaboration_participants WHERE group_id=? AND role='parent'",
                (actor.group_id,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM collaboration_participants WHERE group_id=? AND (id=? OR alias=? OR sandbox_name=?)",
                (actor.group_id, recipient, recipient, recipient),
            ).fetchone()
        if row is None:
            raise CollaborationNotFoundError(
                "recipient is not in the caller's collaboration workflow"
            )
        if row["status"] != "active":
            raise CollaborationConflictError("recipient is no longer active")
        if row["id"] == actor.participant_id:
            raise CollaborationConflictError("a participant cannot send a message to itself")
        return row

    def send(
        self,
        *,
        actor: CollaborationActor,
        recipient: str,
        kind: str,
        body: str,
        reply_to: str | None,
        idempotency_key: str,
        message_type: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> CollaborationMessage:
        if kind not in ALLOWED_KINDS or not body.strip() or len(body.encode()) > MAX_MESSAGE_BYTES:
            raise CollaborationError("message kind or body is invalid")
        typed = message_type or f"collaboration.{kind}"
        correlation = correlation_id or uuid.uuid4().hex
        encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                self._message_query() + " WHERE m.sender_id=? AND m.idempotency_key=?",
                (actor.participant_id, idempotency_key),
            ).fetchone()
            if existing:
                same = recipient in {
                    existing["recipient_id"],
                    existing["recipient_alias"],
                    existing["recipient_sandbox_name"],
                } or (recipient == "parent" and existing["recipient_role"] == "parent")
                if not same or (
                    existing["kind"],
                    existing["body"],
                    existing["reply_to"],
                    existing["message_type"],
                    existing["payload_json"],
                ) != (kind, body, reply_to, typed, encoded):
                    raise CollaborationConflictError(
                        "idempotency key was already used for a different message"
                    )
                return self._message(existing)
            target = self._resolve(db, actor, recipient)
            if (
                db.execute(
                    "SELECT COUNT(*) count FROM collaboration_messages WHERE group_id=?",
                    (actor.group_id,),
                ).fetchone()["count"]
                >= MAX_MESSAGES_PER_GROUP
            ):
                raise CollaborationConflictError(
                    "the collaboration workflow message limit was reached"
                )
            message_id, delivery_id = uuid.uuid4().hex, uuid.uuid4().hex
            cursor = db.execute(
                """INSERT INTO collaboration_messages(id,group_id,sender_id,recipient_id,kind,body,version,message_type,correlation_id,payload_json,reply_to,idempotency_key,created_at)
              VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?)""",
                (
                    message_id,
                    actor.group_id,
                    actor.participant_id,
                    target["id"],
                    kind,
                    body,
                    typed,
                    correlation,
                    encoded,
                    reply_to,
                    idempotency_key,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO collaboration_deliveries(id,message_id,recipient_id,state,created_at) VALUES (?,?,?,'queued',?)",
                (delivery_id, message_id, target["id"], now),
            )
            self._event(
                db,
                actor.group_id,
                "message.stored",
                participant_id=actor.participant_id,
                message_id=message_id,
                delivery_id=delivery_id,
                payload={"type": typed, "correlationId": correlation},
                now=now,
            )
            row = db.execute(
                self._message_query() + " WHERE m.sequence=?", (cursor.lastrowid,)
            ).fetchone()
        return self._message(row)

    def parent_send(self, sandbox_name: str, **kwargs: Any) -> CollaborationMessage:
        recipient = str(kwargs["recipient"])
        matches = []
        for actor in self.parent_actors(sandbox_name):
            with self._connect() as db:
                if db.execute(
                    "SELECT 1 FROM collaboration_participants WHERE group_id=? AND status='active' AND (id=? OR alias=? OR sandbox_name=?)",
                    (actor.group_id, recipient, recipient, recipient),
                ).fetchone():
                    matches.append(actor)
        if not matches:
            raise CollaborationNotFoundError(
                "recipient is not in an active workflow for this parent"
            )
        if len(matches) > 1:
            raise CollaborationConflictError("recipient role is ambiguous across active workflows")
        return self.send(actor=matches[0], **kwargs)

    def inbox(self, actor: CollaborationActor, after: int) -> list[CollaborationMessage]:
        with self._connect() as db:
            rows = db.execute(
                self._message_query()
                + " WHERE m.group_id=? AND m.recipient_id=? AND m.sequence>? ORDER BY m.sequence LIMIT 100",
                (actor.group_id, actor.participant_id, after),
            ).fetchall()
        return [self._message(row) for row in rows]

    def _deliveries(self, participant_ids: list[str]) -> list[CollaborationDelivery]:
        if not participant_ids:
            return []
        marks = ",".join("?" for _ in participant_ids)
        with self._connect() as db:
            query = self._message_query().replace(
                "SELECT m.*",
                "SELECT m.*,d.id delivery_id,d.state delivery_state,"
                "d.created_at delivery_created_at,d.delivered_at delivery_delivered_at,"
                "d.failure_reason delivery_failure_reason",
            )
            rows = db.execute(
                query
                + f" JOIN collaboration_deliveries d ON d.message_id=m.id WHERE d.recipient_id IN ({marks}) AND d.state='queued' ORDER BY m.sequence LIMIT 100",
                participant_ids,
            ).fetchall()
        return [
            CollaborationDelivery(
                row["delivery_id"],
                row["delivery_state"],
                self._message(row),
                row["delivery_created_at"],
                row["delivery_delivered_at"],
                row["delivery_failure_reason"],
            )
            for row in rows
        ]

    def mailbox(self, actor: CollaborationActor) -> list[CollaborationDelivery]:
        return self._deliveries([actor.participant_id])

    def expected_sender_terminal(
        self, actor: CollaborationActor, sender: str
    ) -> CollaborationTerminalError | None:
        """Return why an expected sibling can no longer produce a message."""

        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM collaboration_participants
                WHERE group_id=? AND role='child' AND (id=? OR alias=? OR sandbox_name=?)""",
                (actor.group_id, sender, sender, sender),
            ).fetchone()
            if row is None or row["id"] == actor.participant_id or row["status"] == "active":
                return None
            reason = None
            if row["lifecycle_state"] == "failed":
                event = db.execute(
                    """SELECT event_payload_json FROM collaboration_timeline_events
                    WHERE participant_id=? AND event_type='participant.failed'
                    ORDER BY sequence DESC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                if event:
                    reason = json.loads(event["event_payload_json"] or "{}").get("reason")
            state = row["lifecycle_state"] or "finished"
        return CollaborationTerminalError(
            code=(
                "expected-sender-failed"
                if state == "failed"
                else "expected-sender-finished-without-message"
            ),
            sender_alias=row["alias"],
            sender_sandbox_name=row["sandbox_name"],
            state=state,
            reason=reason,
        )

    def parent_mailbox(self, sandbox_name: str) -> list[CollaborationDelivery]:
        return self._deliveries([a.participant_id for a in self.parent_actors(sandbox_name)])

    def acknowledge_deliveries(
        self, actor: CollaborationActor, delivery_ids: list[str]
    ) -> list[str]:
        return self._ack([actor.participant_id], delivery_ids)

    def acknowledge_parent_deliveries(
        self, sandbox_name: str, delivery_ids: list[str]
    ) -> list[str]:
        return self._ack([a.participant_id for a in self.parent_actors(sandbox_name)], delivery_ids)

    def _ack(self, participant_ids: list[str], delivery_ids: list[str]) -> list[str]:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for delivery_id in delivery_ids:
                row = db.execute(
                    "SELECT d.*,m.group_id,m.id message_id FROM collaboration_deliveries d JOIN collaboration_messages m ON m.id=d.message_id WHERE d.id=?",
                    (delivery_id,),
                ).fetchone()
                if row is None or row["recipient_id"] not in participant_ids:
                    raise CollaborationNotFoundError(
                        "delivery acknowledgement is not addressed to this participant"
                    )
                if row["state"] == "queued":
                    db.execute(
                        "UPDATE collaboration_deliveries SET state='delivered',delivered_at=? WHERE id=?",
                        (now, delivery_id),
                    )
                    self._event(
                        db,
                        row["group_id"],
                        "delivery.acknowledged",
                        message_id=row["message_id"],
                        delivery_id=delivery_id,
                        now=now,
                    )
                self._maybe_close(db, row["group_id"], now)
        return delivery_ids

    def finish_child(
        self,
        participant_id: str,
        *,
        failed: bool = False,
        reason: str | None = None,
    ) -> None:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM collaboration_participants WHERE id=? AND role='child' AND status='active'",
                (participant_id,),
            ).fetchone()
            if row is None:
                return
            state = "failed" if failed else "finished"
            db.execute(
                "UPDATE collaboration_participants SET status='finished',lifecycle_state=?,finished_at=?,token_hash=NULL WHERE id=?",
                (state, now, participant_id),
            )
            for delivery in db.execute(
                "SELECT d.id,m.id message_id FROM collaboration_deliveries d JOIN collaboration_messages m ON m.id=d.message_id WHERE d.recipient_id=? AND d.state='queued'",
                (participant_id,),
            ).fetchall():
                db.execute(
                    "UPDATE collaboration_deliveries SET state='undeliverable',failure_reason=? WHERE id=?",
                    (reason or f"recipient {state}", delivery["id"]),
                )
                self._event(
                    db,
                    row["group_id"],
                    "delivery.undeliverable",
                    participant_id=participant_id,
                    message_id=delivery["message_id"],
                    delivery_id=delivery["id"],
                    payload={"reason": reason or f"recipient {state}"},
                    now=now,
                )
            self._event(
                db,
                row["group_id"],
                f"participant.{state}",
                participant_id=participant_id,
                job_id=row["job_id"],
                payload={"reason": reason} if reason else {},
                now=now,
            )
            self._maybe_close(db, row["group_id"], now)

    def finish_job(self, job_id: str) -> None:
        self._finish_job(job_id, False, None)

    def fail_job(self, job_id: str, reason: str) -> None:
        self._finish_job(job_id, True, reason)

    def _finish_job(
        self,
        job_id: str,
        failed: bool,
        reason: str | None,
    ) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM collaboration_participants WHERE job_id=?", (job_id,)
            ).fetchone()
        if row:
            self.finish_child(
                row["id"],
                failed=failed,
                reason=reason,
            )

    def _maybe_close(self, db: sqlite3.Connection, group_id: str, now: float) -> None:
        active = db.execute(
            "SELECT COUNT(*) count FROM collaboration_participants WHERE group_id=? AND role='child' AND status='active'",
            (group_id,),
        ).fetchone()["count"]
        children = db.execute(
            "SELECT COUNT(*) count FROM collaboration_participants WHERE group_id=? AND role='child'",
            (group_id,),
        ).fetchone()["count"]
        pending = db.execute(
            "SELECT COUNT(*) count FROM collaboration_deliveries d JOIN collaboration_messages m ON m.id=d.message_id JOIN collaboration_participants p ON p.id=d.recipient_id WHERE m.group_id=? AND p.role='parent' AND d.state='queued'",
            (group_id,),
        ).fetchone()["count"]
        if (
            children
            and not active
            and not pending
            and db.execute(
                "UPDATE collaboration_groups SET status='closed',closed_at=? WHERE id=? AND status='active'",
                (now, group_id),
            ).rowcount
        ):
            self._event(
                db,
                group_id,
                "run.closed",
                payload={"reason": "children terminal and parent mailbox drained"},
                now=now,
            )

    def timeline_after(
        self, after: int, *, parent_sandbox_name: str | None = None
    ) -> list[CollaborationTimelineEvent]:
        extra = "" if parent_sandbox_name is None else "AND g.parent_sandbox_name=?"
        params = (after,) if parent_sandbox_name is None else (after, parent_sandbox_name)
        query = (
            """SELECT e.sequence event_sequence,e.group_id,g.run_id,g.parent_sandbox_name,e.event_type,e.participant_id,e.job_id,e.delivery_id,e.event_payload_json,e.message_id event_message_id,e.created_at event_created_at,
          p.alias participant_alias,p.role participant_role,p.sandbox_name participant_sandbox_name,
          m.*,s.alias sender_alias,s.sandbox_name sender_sandbox_name,r.alias recipient_alias,r.sandbox_name recipient_sandbox_name
          FROM collaboration_timeline_events e JOIN collaboration_groups g ON g.id=e.group_id
          LEFT JOIN collaboration_participants p ON p.id=e.participant_id LEFT JOIN collaboration_messages m ON m.id=e.message_id
          LEFT JOIN collaboration_participants s ON s.id=m.sender_id LEFT JOIN collaboration_participants r ON r.id=m.recipient_id
          WHERE e.sequence>? """
            + extra
            + " ORDER BY e.sequence"
        )
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [
            CollaborationTimelineEvent(
                row["event_sequence"],
                row["group_id"],
                row["run_id"],
                row["parent_sandbox_name"],
                row["event_type"],
                row["participant_id"],
                row["participant_alias"],
                row["participant_role"],
                row["participant_sandbox_name"],
                row["job_id"],
                row["delivery_id"],
                json.loads(row["event_payload_json"] or "{}"),
                self._message(row) if row["event_message_id"] else None,
                row["event_created_at"],
            )
            for row in rows
        ]


def token_matches(supplied: str, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, expected)
