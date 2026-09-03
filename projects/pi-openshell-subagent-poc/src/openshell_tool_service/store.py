"""SQLite persistence for Pi-to-OpenShell job mappings."""

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
    """The caller reused a key for a different prompt or policy."""


class JobQueueFullError(RuntimeError):
    """The Tool Service has no active-worker capacity."""


class WorkflowConflictError(ValueError):
    """Workers in one Pi workflow supplied incompatible coordination contracts."""


@dataclass(frozen=True)
class Job:
    id: str
    caller_id: str
    run_id: str
    start_mode: str
    expected_workers: int | None
    step_index: int
    agent: str
    prompt: str
    prompt_digest: str
    profile: str
    github_repositories: tuple[str, ...]
    child_policy: str
    participant_alias: str
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
                    start_mode TEXT NOT NULL DEFAULT 'immediate',
                    expected_workers INTEGER,
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
                    sandbox_logs TEXT,
                    sandbox_log_error TEXT,
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
            if "sandbox_logs" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN sandbox_logs TEXT")
            if "sandbox_log_error" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN sandbox_log_error TEXT")
            if "start_mode" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN start_mode TEXT NOT NULL DEFAULT 'immediate'"
                )
            if "expected_workers" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN expected_workers INTEGER")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        values = dict(row)
        values.pop("idempotency_key")
        resources = json.loads(values.pop("resources_json"))
        values["github_repositories"] = tuple(resources.get("githubRepositories", []))
        values["child_policy"] = resources.get("childPolicy", "")
        values["participant_alias"] = resources.get("participantAlias") or values["sandbox_name"]
        values.pop("batch_id", None)
        return Job(**values)

    def create_or_get(
        self,
        *,
        caller_id: str,
        run_id: str,
        start_mode: str,
        expected_workers: int | None,
        step_index: int,
        idempotency_key: str,
        prompt: str,
        child_policy: str,
        participant_alias: str,
        max_active_workers: int,
    ) -> tuple[Job, bool]:
        canonical_request = json.dumps(
            {
                "prompt": prompt,
                "run_id": run_id,
                "start_mode": start_mode,
                "expected_workers": expected_workers,
                "step_index": step_index,
                "child_policy": child_policy,
                "participant_alias": participant_alias,
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

            active = connection.execute(
                """SELECT COUNT(*) AS count FROM jobs
                WHERE state IN ('admitting','queued','preparing','prepared','running')"""
            ).fetchone()["count"]
            if active >= max_active_workers:
                raise JobQueueFullError(
                    f"Tool Service is at worker capacity ({active}/{max_active_workers})"
                )

            workflow = connection.execute(
                """SELECT start_mode,expected_workers,COUNT(*) count FROM jobs
                WHERE caller_id=? AND run_id=? GROUP BY start_mode,expected_workers""",
                (caller_id, run_id),
            ).fetchone()
            if workflow is not None:
                if (
                    workflow["start_mode"] != start_mode
                    or workflow["expected_workers"] != expected_workers
                ):
                    raise WorkflowConflictError(
                        "workers in one workflow must use the same start mode and expected count"
                    )
                if expected_workers is not None and workflow["count"] >= expected_workers:
                    raise WorkflowConflictError(
                        "the workflow already has its declared number of workers"
                    )

            job_id = uuid.uuid4().hex
            now = time.time()
            resources_json = json.dumps(
                {
                    "childPolicy": child_policy,
                    "participantAlias": participant_alias,
                },
                separators=(",", ":"),
            )
            # OpenShell sandbox names are currently limited to 19 characters.
            sandbox_name = f"pi-child-{job_id[:10]}"
            connection.execute(
                """
                INSERT INTO jobs (
                    id, idempotency_key, caller_id, run_id,
                    start_mode, expected_workers, step_index, agent,
                    prompt, prompt_digest, profile, resources_json, state, sandbox_name,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admitting', ?, ?, ?)
                """,
                (
                    job_id,
                    scoped_key,
                    caller_id,
                    run_id,
                    start_mode,
                    expected_workers,
                    step_index,
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

    def get_many(self, job_ids: list[str]) -> list[Job]:
        unique_ids = list(dict.fromkeys(job_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders})", unique_ids
            ).fetchall()
        by_id = {row["id"]: self._job(row) for row in rows}
        return [by_id[job_id] for job_id in unique_ids if job_id in by_id]

    def claim_next_queued(self) -> Job | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            updated = connection.execute(
                "UPDATE jobs SET state='preparing',updated_at=? WHERE id=? AND state='queued'",
                (now, row["id"]),
            ).rowcount
            if updated != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone()
            assert claimed is not None
            return self._job(claimed)

    def mark_queued(self, job_id: str) -> Job:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET state='queued',updated_at=? WHERE id=? AND state='admitting'",
                (time.time(), job_id),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def mark_prepared(self, job_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE jobs SET state='prepared',updated_at=?
                WHERE id=? AND state='preparing'""",
                (time.time(), job_id),
            ).rowcount
        return changed == 1

    def list_workflows(self) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT caller_id,run_id,MIN(created_at) first_created FROM jobs
                WHERE state IN ('admitting','queued','preparing','prepared')
                GROUP BY caller_id,run_id ORDER BY first_created"""
            ).fetchall()
        return [(str(row["caller_id"]), str(row["run_id"])) for row in rows]

    def workflow_jobs(self, caller_id: str, run_id: str) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM jobs WHERE caller_id=? AND run_id=?
                ORDER BY step_index,id""",
                (caller_id, run_id),
            ).fetchall()
        return [self._job(row) for row in rows]

    def release_ready_workflow(
        self,
        caller_id: str,
        run_id: str,
        *,
        max_active_workers: int,
    ) -> list[Job]:
        """Start ready workers according to the parent's explicit workflow contract."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM jobs WHERE caller_id=? AND run_id=?
                ORDER BY step_index,id""",
                (caller_id, run_id),
            ).fetchall()
            if not rows or any(row["state"] == "failed" for row in rows):
                return []
            live = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE state='running'"
            ).fetchone()["count"]
            available = max_active_workers - live
            if available <= 0:
                return []
            mode = rows[0]["start_mode"]
            if mode == "all-ready":
                expected = rows[0]["expected_workers"]
                if (
                    expected is None
                    or len(rows) != expected
                    or any(row["state"] != "prepared" for row in rows)
                    or len(rows) > available
                ):
                    return []
                selected = rows
            else:
                selected = [row for row in rows if row["state"] == "prepared"][:available]
                if not selected:
                    return []
            now = time.time()
            ids = [row["id"] for row in selected]
            marks = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE jobs SET state='running',updated_at=? WHERE id IN ({marks})",
                (now, *ids),
            )
            released = connection.execute(
                f"SELECT * FROM jobs WHERE id IN ({marks}) ORDER BY step_index,id", ids
            ).fetchall()
        return [self._job(row) for row in released]

    def expire_waiting(self, *, older_than: float) -> list[Job]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM jobs WHERE state IN ('admitting','queued','preparing','prepared')
                AND created_at<? ORDER BY created_at""",
                (older_than,),
            ).fetchall()
            if rows:
                now = time.time()
                connection.execute(
                    """UPDATE jobs SET state='failed',failure_code='queue-expired',
                    failure_message='job expired before execution',updated_at=?
                    WHERE state IN ('admitting','queued','preparing','prepared')
                    AND created_at<?""",
                    (now, older_than),
                )
        return [self._job(row) for row in rows]

    def recover_interrupted(self) -> list[Job]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM jobs WHERE state IN ('preparing','prepared','running')
                ORDER BY updated_at"""
            ).fetchall()
            if rows:
                connection.execute(
                    """UPDATE jobs SET state='failed',failure_code='service-restarted',
                    failure_message='Tool Service restarted while the job was running',
                    updated_at=? WHERE state IN ('preparing','prepared','running')""",
                    (time.time(),),
                )
        return [self._job(row) for row in rows]

    def count_pending(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM jobs
                WHERE state IN ('admitting','queued','preparing','prepared','running')"""
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def list_by_state(self, state: str) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE state=? ORDER BY created_at,id", (state,)
            ).fetchall()
        return [self._job(row) for row in rows]

    def list_cleanup_candidates(self) -> list[Job]:
        """Return failed jobs that may have escaped normal sandbox cleanup."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM jobs
                WHERE state='failed' AND failure_code='tool-service' AND cleanup_error IS NULL
                ORDER BY updated_at,id"""
            ).fetchall()
        return [self._job(row) for row in rows]

    def record_cleanup_attempt(self, job_id: str, error: str | None) -> None:
        # The empty string records a successful reconciliation and prevents the
        # same terminal job from being retried on every service restart.
        self._update(job_id, cleanup_error=error or "")

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
        sandbox_logs: str | None = None,
        sandbox_log_error: str | None = None,
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
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), job_id]
        with self._connect() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
