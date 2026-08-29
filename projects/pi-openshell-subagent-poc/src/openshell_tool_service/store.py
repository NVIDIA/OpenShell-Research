"""SQLite persistence for Pi-to-OpenShell job mappings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


class IdempotencyConflictError(ValueError):
    """The caller reused a key for a different prompt or policy."""


@dataclass(frozen=True)
class Job:
    id: str
    caller_id: str
    run_id: str
    step_index: int
    agent: str
    prompt: str
    prompt_digest: str
    profile: str
    github_repositories: tuple[str, ...]
    child_policy: str
    state: str
    sandbox_name: str
    output: str | None
    stderr: str | None
    exit_code: int | None
    failure_code: str | None
    failure_message: str | None
    cleanup_error: str | None
    created_at: float
    updated_at: float


class JobStore:
    """Small, process-local job store with idempotent submission."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    caller_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    agent TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    prompt_digest TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    resources_json TEXT NOT NULL DEFAULT '{"githubRepositories":[]}',
                    state TEXT NOT NULL,
                    sandbox_name TEXT NOT NULL,
                    output TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    failure_code TEXT,
                    failure_message TEXT,
                    cleanup_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "resources_json" not in columns:
                connection.execute(
                    """ALTER TABLE jobs ADD COLUMN resources_json TEXT NOT NULL
                    DEFAULT '{"githubRepositories":[]}'"""
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        values = dict(row)
        values.pop("idempotency_key")
        resources = json.loads(values.pop("resources_json"))
        values["github_repositories"] = tuple(resources.get("githubRepositories", []))
        values["child_policy"] = resources.get("childPolicy", "")
        return Job(**values)

    def create_or_get(
        self,
        *,
        caller_id: str,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
    ) -> tuple[Job, bool]:
        canonical_request = json.dumps(
            {
                "prompt": prompt,
                "child_policy": child_policy,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        request_digest = hashlib.sha256(canonical_request.encode()).hexdigest()
        scoped_key = hashlib.sha256(
            json.dumps(
                {"caller_id": caller_id, "idempotency_key": idempotency_key},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (scoped_key,)
            ).fetchone()
            if row is not None:
                if row["prompt_digest"] != request_digest:
                    raise IdempotencyConflictError(
                        "idempotencyKey was already used with a different prompt or child policy"
                    )
                return self._job(row), False

            job_id = uuid.uuid4().hex
            now = time.time()
            resources_json = json.dumps(
                {"childPolicy": child_policy},
                separators=(",", ":"),
            )
            # OpenShell sandbox names are currently limited to 19 characters.
            sandbox_name = f"pi-child-{job_id[:10]}"
            connection.execute(
                """
                INSERT INTO jobs (
                    id, idempotency_key, caller_id, run_id, step_index, agent,
                    prompt, prompt_digest, profile, resources_json, state, sandbox_name,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    scoped_key,
                    caller_id,
                    "",
                    0,
                    "openshell-worker",
                    prompt,
                    request_digest,
                    "worker",
                    resources_json,
                    sandbox_name,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            assert row is not None
            return self._job(row), True

    def get(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row is not None else None

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
    ) -> None:
        self._update(
            job_id,
            state="completed",
            output=output,
            stderr=stderr,
            exit_code=exit_code,
            cleanup_error=cleanup_error,
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
    ) -> None:
        self._update(
            job_id,
            state="failed",
            failure_code=code,
            failure_message=message,
            stderr=stderr,
            exit_code=exit_code,
            cleanup_error=cleanup_error,
        )

    def _update(self, job_id: str, **fields: object) -> None:
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), job_id]
        with self._connect() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
