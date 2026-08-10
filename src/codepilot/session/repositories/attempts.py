from __future__ import annotations

from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_attempt_id, make_event_id, now_iso
from codepilot.session.models import AttemptStatus, RunAttemptRecord, TurnRecord, TurnStatus
from codepilot.session.row_mappers import attempt_from_row, turn_from_row
from codepilot.session.repositories._support import json_dumps


class AttemptRepository:
    """Attempt persistence and atomic turn/attempt execution lifecycle."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_attempt(
        self,
        *,
        turn_id: str,
        status: str = "created",
        started_at: str | None = None,
        ended_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunAttemptRecord:
        timestamp = now_iso()
        attempt_id = make_attempt_id()
        with self.database.transaction() as connection:
            number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?", (turn_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO run_attempts(
                    attempt_id, turn_id, attempt_number, status, created_at, updated_at,
                    started_at, ended_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt_id, turn_id, number, status, timestamp, timestamp, started_at, ended_at, json_dumps(metadata or {})),
            )
        return self.get_attempt(attempt_id)

    def update_attempt_status(self, attempt_id: str, status: str) -> RunAttemptRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?), "
                "ended_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled', 'interrupted') THEN ? ELSE ended_at END "
                "WHERE attempt_id = ?",
                (status, timestamp, timestamp, status, timestamp, attempt_id),
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> RunAttemptRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise LookupError(attempt_id)
        return attempt_from_row(row)

    def list_attempts(self, turn_id: str) -> list[RunAttemptRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM run_attempts WHERE turn_id = ? ORDER BY attempt_number", (turn_id,)
            ).fetchall()
        return [attempt_from_row(row) for row in rows]

    def start_turn_attempt(self, turn_id: str, attempt_id: str, *, worker_id: str, lease_expires_at: str) -> tuple[TurnRecord, RunAttemptRecord]:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            attempt = connection.execute("SELECT turn_id FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if attempt is None:
                raise LookupError(attempt_id)
            if attempt["turn_id"] != turn_id:
                raise ValueError("attempt does not belong to turn")
            connection.execute(
                "UPDATE run_attempts SET status = 'running', started_at = ?, ended_at = NULL, interruption_reason = NULL, "
                "worker_id = ?, lease_expires_at = ?, updated_at = ? WHERE attempt_id = ? AND status = 'created'",
                (timestamp, worker_id, lease_expires_at, timestamp, attempt_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("attempt is not in created state")
            connection.execute(
                "UPDATE turns SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?, "
                "last_activity_at = ? WHERE turn_id = ? AND status = 'queued'",
                (timestamp, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is not in queued state")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = "
                "(SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return turn_from_row(turn_row), attempt_from_row(attempt_row)

    def finish_turn_attempt(
        self,
        turn_id: str,
        attempt_id: str,
        *,
        attempt_status: AttemptStatus,
        turn_status: TurnStatus,
        worker_id: str,
    ) -> tuple[TurnRecord, RunAttemptRecord]:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = ?, ended_at = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (attempt_status, timestamp, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = ?, completed_at = ?, error_code = NULL, updated_at = ?, last_activity_at = ? "
                "WHERE turn_id = ? AND status = 'running'",
                (turn_status, timestamp, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = "
                "(SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return turn_from_row(turn_row), attempt_from_row(attempt_row)

    def interrupt_turn_attempt(self, turn_id: str, attempt_id: str, reason: str, *, worker_id: str) -> tuple[TurnRecord, RunAttemptRecord]:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = 'interrupted', ended_at = ?, interruption_reason = ?, worker_id = NULL, "
                "lease_expires_at = NULL, updated_at = ? WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (timestamp, reason, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = 'interrupted', completed_at = ?, error_code = ?, updated_at = ?, last_activity_at = ? "
                "WHERE turn_id = ? AND status = 'running'",
                (timestamp, reason, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = "
                "(SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return turn_from_row(turn_row), attempt_from_row(attempt_row)

    def renew_attempt_lease(self, attempt_id: str, worker_id: str, lease_expires_at: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET lease_expires_at = ?, updated_at = ? WHERE attempt_id = ? "
                "AND worker_id = ? AND status = 'running'",
                (lease_expires_at, now_iso(), attempt_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("attempt lease is no longer owned by this worker")
