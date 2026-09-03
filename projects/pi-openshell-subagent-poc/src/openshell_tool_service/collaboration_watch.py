"""Read-only terminal timeline for the POC collaboration group."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_EVENT_TYPES = {
    "participant.reserved",
    "policy.review.started",
    "policy.review.allowed",
    "policy.review.denied",
    "sandbox.ready",
    "workflow.released",
    "worker.released",
    "message.stored",
    "participant.finished",
    "participant.failed",
    "delivery.undeliverable",
    "sandbox.cleanup.failed",
    "run.closed",
    "run.expired",
}

EVENT_LABELS = {
    "participant.reserved": "reserved",
    "participant.finished": "finished",
    "participant.failed": "failed",
    "policy.review.started": "policy review started",
    "policy.review.allowed": "policy allowed",
    "policy.review.denied": "policy denied",
    "sandbox.ready": "sandbox ready",
    "workflow.released": "Pi worker starting after all-ready barrier",
    "worker.released": "ready; Pi worker starting",
    "sandbox.cleanup.failed": "sandbox cleanup failed",
    "delivery.undeliverable": "message undeliverable",
    "run.closed": "collaboration closed",
    "run.expired": "collaboration expired",
}


@dataclass(frozen=True)
class MonitoredTimelineEvent:
    sequence: int
    group_id: str
    run_id: str
    parent_sandbox_name: str
    event_type: str
    job_id: str | None
    delivery_id: str | None
    event_payload_json: str
    participant_alias: str | None
    participant_role: str | None
    participant_sandbox_name: str | None
    message_sequence: int | None
    sender_alias: str | None
    sender_sandbox_name: str | None
    recipient_alias: str | None
    recipient_sandbox_name: str | None
    kind: str | None
    message_type: str | None
    correlation_id: str | None
    body: str | None
    created_at: float


class CollaborationReader:
    """Read collaboration lifecycle and message events without participating."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def latest_sequence(self, *, parent: str | None = None) -> int:
        parent_filter = "" if parent is None else "WHERE g.parent_sandbox_name = ?"
        parameters = () if parent is None else (parent,)
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT COALESCE(MAX(e.sequence), 0) AS sequence
                FROM collaboration_timeline_events e
                JOIN collaboration_groups g ON g.id = e.group_id
                {parent_filter}""",
                parameters,
            ).fetchone()
        assert row is not None
        return int(row["sequence"])

    def events_after(
        self, after: int, *, parent: str | None = None
    ) -> list[MonitoredTimelineEvent]:
        parent_filter = "" if parent is None else "AND g.parent_sandbox_name = ?"
        parameters: tuple[object, ...] = (after,) if parent is None else (after, parent)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT e.sequence, e.group_id, g.run_id, g.parent_sandbox_name,
                    e.event_type, e.job_id, e.delivery_id, e.event_payload_json,
                    p.alias AS participant_alias,
                    p.role AS participant_role,
                    p.sandbox_name AS participant_sandbox_name,
                    m.sequence AS message_sequence,
                    sender.alias AS sender_alias,
                    sender.sandbox_name AS sender_sandbox_name,
                    recipient.alias AS recipient_alias,
                    recipient.sandbox_name AS recipient_sandbox_name,
                    m.kind, m.message_type, m.correlation_id, m.body, e.created_at
                FROM collaboration_timeline_events e
                JOIN collaboration_groups g ON g.id = e.group_id
                LEFT JOIN collaboration_participants p ON p.id = e.participant_id
                LEFT JOIN collaboration_messages m ON m.id = e.message_id
                LEFT JOIN collaboration_participants sender ON sender.id = m.sender_id
                LEFT JOIN collaboration_participants recipient ON recipient.id = m.recipient_id
                WHERE e.sequence > ? {parent_filter}
                ORDER BY e.sequence""",
                parameters,
            ).fetchall()
        return [MonitoredTimelineEvent(**dict(row)) for row in rows]


def visible_by_default(event: MonitoredTimelineEvent) -> bool:
    return event.event_type in DEFAULT_EVENT_TYPES


def format_event(
    event: MonitoredTimelineEvent, *, metadata_only: bool = False, verbose: bool = False
) -> str:
    timestamp = datetime.fromtimestamp(event.created_at).astimezone().strftime("%H:%M:%S")
    prefix = f"{timestamp}  run={event.run_id[:12]:<12}"
    if verbose:
        prefix += f" event={event.sequence:<4}"
    if event.event_type != "message.stored":
        role = event.participant_alias or event.participant_role or "workflow"
        context = []
        if event.job_id:
            context.append(f"job={event.job_id[:8]}")
        if verbose and event.delivery_id:
            context.append(f"delivery={event.delivery_id[:8]}")
        payload = json.loads(event.event_payload_json or "{}")
        show_reason = verbose or event.event_type in {
            "policy.review.denied",
            "participant.failed",
        }
        if payload.get("reason") and show_reason:
            context.append(f"reason={payload['reason']}")
        suffix = f" ({', '.join(context)})" if context else ""
        label = (
            event.event_type
            if verbose
            else EVENT_LABELS.get(event.event_type, event.event_type)
        )
        return f"{prefix} {role} {label}{suffix}"

    sender = event.sender_alias or event.sender_sandbox_name or "unknown"
    recipient = event.recipient_alias or event.recipient_sandbox_name or "unknown"
    delivery = event.delivery_id[:8] if event.delivery_id else "-"
    delivery_text = f" delivery={delivery:<8}" if verbose else ""
    metadata = (
        f"{prefix} msg={event.message_sequence:<4}{delivery_text} "
        f"{event.message_type or event.kind} {sender} -> {recipient}"
    )
    if metadata_only:
        return metadata
    lines = (event.body or "").splitlines() or [""]
    return "\n".join([f"{metadata}: {lines[0]}", *[f"    {line}" for line in lines[1:]]])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Follow the OpenShell collaboration POC lifecycle and message timeline."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.environ.get("OPENSHELL_TOOL_SERVICE_DATABASE", ".state/jobs.sqlite3")),
        help="Tool Service SQLite database (default: OPENSHELL_TOOL_SERVICE_DATABASE).",
    )
    parser.add_argument("--parent", help="Show only this parent sandbox's group.")
    parser.add_argument(
        "--history", action="store_true", help="Print retained events before following new ones."
    )
    parser.add_argument(
        "--snapshot", action="store_true", help="Print retained events once and exit."
    )
    parser.add_argument("--metadata-only", action="store_true", help="Hide message bodies.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show every internal event, event/delivery IDs, and full policy-review reasons.",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=0.5, help="Polling interval in seconds."
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.poll_interval <= 0:
        raise SystemExit("--poll-interval must be greater than zero")

    reader = CollaborationReader(args.database)
    try:
        latest_sequence = reader.latest_sequence(parent=args.parent)
    except (FileNotFoundError, sqlite3.OperationalError) as error:
        raise SystemExit(
            f"Cannot read collaboration database {args.database}: {error}. "
            "Start the OpenShell Tool Service first."
        ) from error
    after = 0 if args.history or args.snapshot else latest_sequence

    scope = f"parent={args.parent}" if args.parent else "all parents"
    mode = "snapshot" if args.snapshot else "following"
    print(f"Collaboration timeline ({mode}, {scope}, database={args.database})", flush=True)
    if not args.snapshot:
        print("Press Ctrl-C to stop.\n", flush=True)

    try:
        while True:
            try:
                events = reader.events_after(after, parent=args.parent)
            except (FileNotFoundError, sqlite3.OperationalError) as error:
                raise SystemExit(
                    f"Cannot read collaboration database {args.database}: {error}."
                ) from error
            for event in events:
                after = event.sequence
                if args.verbose or visible_by_default(event):
                    print(
                        format_event(
                            event, metadata_only=args.metadata_only, verbose=args.verbose
                        ),
                        flush=True,
                    )
            if args.snapshot:
                return
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
