from __future__ import annotations

import sqlite3
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_event_id, now_iso
from codepilot.session.models import SessionEventRecord
from codepilot.session.row_mappers import event_from_row
from codepilot.session.repositories._support import json_dumps


class EventRepository:
    """Append-only session event persistence."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def append_event(self, *, session_id: str, event_type: str, payload: dict[str, Any], turn_id: str | None = None, attempt_id: str | None = None, metadata: dict[str, Any] | None = None, connection: sqlite3.Connection | None = None) -> SessionEventRecord:
        timestamp = now_iso()
        event_id = make_event_id()
        if connection is None:
            with self.database.transaction() as transaction:
                self._append(transaction, event_id, session_id, event_type, payload, turn_id, attempt_id, metadata, timestamp)
            return self.get_event(event_id)
        self._append(connection, event_id, session_id, event_type, payload, turn_id, attempt_id, metadata, timestamp)
        return event_from_row(connection.execute("SELECT * FROM session_events WHERE event_id = ?", (event_id,)).fetchone())

    @staticmethod
    def _append(connection: sqlite3.Connection, event_id: str, session_id: str, event_type: str, payload: dict[str, Any], turn_id: str | None, attempt_id: str | None, metadata: dict[str, Any] | None, timestamp: str) -> None:
        sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?", (session_id,)).fetchone()[0]
        connection.execute(
            """INSERT INTO session_events(
                event_id, session_id, sequence, event_type, created_at, turn_id,
                attempt_id, payload_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, session_id, sequence, event_type, timestamp, turn_id, attempt_id, json_dumps(payload), json_dumps(metadata or {})),
        )

    def get_event(self, event_id: str) -> SessionEventRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM session_events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            raise LookupError(event_id)
        return event_from_row(row)

    def list_events(self, session_id: str) -> list[SessionEventRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
        return [event_from_row(row) for row in rows]
