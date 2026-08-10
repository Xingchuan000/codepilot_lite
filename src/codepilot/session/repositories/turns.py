from __future__ import annotations

from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_turn_id, now_iso
from codepilot.session.models import TurnRecord, TurnStatus
from codepilot.session.row_mappers import turn_from_row
from codepilot.session.repositories._support import json_dumps, json_loads


class TurnRepository:
    """Persistence operations for the turns table."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_turn(
        self,
        *,
        session_id: str,
        title: str,
        provider_snapshot: str,
        model_snapshot: str,
        permission_mode_snapshot: str,
        branch_snapshot: str | None,
        status: TurnStatus = "queued",
        metadata: dict[str, Any] | None = None,
    ) -> TurnRecord:
        timestamp = now_iso()
        turn_id = make_turn_id()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO turns(
                    turn_id, session_id, sequence, title, status, provider_snapshot, model_snapshot,
                    permission_mode_snapshot, branch_snapshot, created_at, updated_at, last_activity_at,
                    user_message_id, started_at, completed_at, error_code, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)""",
                (
                    turn_id,
                    session_id,
                    sequence,
                    title,
                    status,
                    provider_snapshot,
                    model_snapshot,
                    permission_mode_snapshot,
                    branch_snapshot,
                    timestamp,
                    timestamp,
                    timestamp,
                    json_dumps(metadata or {}),
                ),
            )
        return self.get_turn(turn_id)

    def update_turn_status(self, turn_id: str, status: TurnStatus) -> TurnRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                (status, timestamp, timestamp, turn_id),
            )
        return self.get_turn(turn_id)

    def update_turn_metadata(self, turn_id: str, metadata: dict[str, Any]) -> TurnRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT metadata_json FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                raise LookupError(turn_id)
            current = json_loads(row["metadata_json"]) or {}
            current.update(metadata)
            connection.execute(
                "UPDATE turns SET metadata_json = ?, updated_at = ? WHERE turn_id = ?",
                (json_dumps(current), now_iso(), turn_id),
            )
        return self.get_turn(turn_id)

    def list_turns(self, session_id: str) -> list[TurnRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM turns WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
        return [turn_from_row(row) for row in rows]

    def get_turn(self, turn_id: str) -> TurnRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if row is None:
            raise LookupError(turn_id)
        return turn_from_row(row)
