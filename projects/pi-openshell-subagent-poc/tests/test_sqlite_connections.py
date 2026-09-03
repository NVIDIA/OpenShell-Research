from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openshell_tool_service.collaboration import CollaborationStore
from openshell_tool_service.store import JobStore


@pytest.mark.parametrize("store_type", [JobStore, CollaborationStore])
def test_store_connection_context_closes_database(
    tmp_path: Path, store_type: type[JobStore] | type[CollaborationStore]
) -> None:
    store = store_type(tmp_path / "jobs.sqlite3")

    with store._connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
