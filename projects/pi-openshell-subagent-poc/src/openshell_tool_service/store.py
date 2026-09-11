"""SQLite persistence for Pi-to-OpenShell jobs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class IdempotencyConflictError(ValueError):
    """A caller reused an idempotency key for a different request."""


@dataclass(frozen=True)
class Job:
    id: str
    caller_id: str
    step_index: int
    prompt: str
    prompt_digest: str
    child_policy: str
    state: str
    sandbox_name: str
    output: str | None
    stderr: str | None
    exit_code: int | None
    failure_code: str | None
    failure_message: str | None
    cleanup_error: str | None
    sandbox_logs: str | None
    sandbox_log_error: str | None
    created_at: float
    updated_at: float


class JobStore:
    """Small durable job store with idempotent submission."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    caller_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    prompt_digest TEXT NOT NULL,
                    child_policy TEXT NOT NULL,
                    state TEXT NOT NULL,
                    sandbox_name TEXT NOT NULL,
                    output TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    failure_code TEXT,
                    failure_message TEXT,
                    cleanup_error TEXT,
                    sandbox_logs TEXT,
                    sandbox_log_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
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
    def _job(row: sqlite3.Row) -> Job:
        values = dict(row)
        values.pop("idempotency_key")
        return Job(**values)

    def create_or_get(
        self,
        *,
        caller_id: str,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
    ) -> tuple[Job, bool]:
        canonical = json.dumps(
            {
                "caller_id": caller_id,
                "step_index": step_index,
                "prompt": prompt,
                "child_policy": child_policy,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        request_digest = hashlib.sha256(canonical.encode()).hexdigest()
        scoped_key = hashlib.sha256(
            json.dumps(
                {"caller_id": caller_id, "idempotency_key": idempotency_key},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (scoped_key,)
            ).fetchone()
            if existing is not None:
                if existing["prompt_digest"] != request_digest:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different worker request"
                    )
                return self._job(existing), False

            now = time.time()
            job_id = uuid.uuid4().hex
            sandbox_name = f"pi-child-{job_id[:10]}"
            db.execute(
                """INSERT INTO jobs(
                    id,idempotency_key,caller_id,step_index,prompt,prompt_digest,
                    child_policy,state,sandbox_name,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,'queued',?,?,?)""",
                (
                    job_id,
                    scoped_key,
                    caller_id,
                    step_index,
                    prompt,
                    request_digest,
                    child_policy,
                    sandbox_name,
                    now,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row is not None
        return self._job(row), True

    def get(self, job_id: str) -> Job | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row is not None else None

    def recover_interrupted(self) -> list[Job]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE state IN ('queued','running')").fetchall()
        return [self._job(row) for row in rows]

    def mark_running(self, job_id: str) -> None:
        self._update(job_id, state="running")

    def mark_completed(
        self,
        job_id: str,
        *,
        output: str,
        stderr: str,
        exit_code: int,
        cleanup_error: str | None,
        sandbox_logs: str | None,
        sandbox_log_error: str | None,
    ) -> None:
        self._update(
            job_id,
            state="completed",
            output=output,
            stderr=stderr,
            exit_code=exit_code,
            cleanup_error=cleanup_error,
            sandbox_logs=sandbox_logs,
            sandbox_log_error=sandbox_log_error,
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        stderr: str = "",
        exit_code: int | None = None,
        cleanup_error: str | None = None,
        sandbox_logs: str | None = None,
        sandbox_log_error: str | None = None,
    ) -> None:
        self._update(
            job_id,
            state="failed",
            failure_code=code,
            failure_message=message,
            stderr=stderr,
            exit_code=exit_code,
            cleanup_error=cleanup_error,
            sandbox_logs=sandbox_logs,
            sandbox_log_error=sandbox_log_error,
        )

    def _update(self, job_id: str, **fields: object) -> None:
        fields["updated_at"] = time.time()
        assignments = ",".join(f"{name}=?" for name in fields)
        with self._connect() as db:
            db.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                (*fields.values(), job_id),
            )
