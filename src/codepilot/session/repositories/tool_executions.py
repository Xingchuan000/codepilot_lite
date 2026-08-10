from __future__ import annotations

from typing import Any

from codepilot.permissions import PermissionRequest
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_event_id, make_tool_call_id, make_tool_result_id, now_iso
from codepilot.session.models import (
    AttemptStatus,
    RunAttemptRecord,
    ToolCallRecord,
    ToolCallStatus,
    ToolResultRecord,
    ToolResultStatus,
    TurnRecord,
)
from codepilot.session.row_mappers import attempt_from_row, tool_call_from_row, tool_result_from_row, turn_from_row
from codepilot.session.repositories._support import bool_to_int, json_dumps


class ToolExecutionRepository:
    """Persistence operations for tool calls/results and their recovery lifecycle."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_tool_call(
        self,
        *,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: ToolCallStatus = "created",
        attempt_id: str | None = None,
        message_id: str | None = None,
        side_effect: str | None = None,
        idempotency: str | None = None,
        recovery_strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallRecord:
        timestamp = now_iso()
        call_id = make_tool_call_id()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO tool_calls(
                    tool_call_id, turn_id, attempt_id, message_id, status, tool_name, arguments_json,
                    created_at, updated_at, started_at, completed_at, side_effect, idempotency,
                    recovery_strategy, recovery_token_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?)""",
                (call_id, turn_id, attempt_id, message_id, status, tool_name, json_dumps(arguments), timestamp, timestamp, side_effect, idempotency, recovery_strategy, json_dumps(metadata or {})),
            )
        return self.get_tool_call(call_id)

    def get_tool_call(self, tool_call_id: str) -> ToolCallRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        if row is None:
            raise LookupError(tool_call_id)
        return tool_call_from_row(row)

    def attach_message(self, tool_call_id: str, message_id: str) -> ToolCallRecord:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET message_id = ?, updated_at = ? WHERE tool_call_id = ?",
                (message_id, now_iso(), tool_call_id),
            )
        return self.get_tool_call(tool_call_id)

    def mark_tool_approval_pending_with_event(self, tool_call_id: str, request: PermissionRequest) -> ToolCallRecord:
        return self._mark_tool_call_status(tool_call_id, "approval_pending")

    def mark_tool_approved(self, tool_call_id: str) -> ToolCallRecord:
        return self._mark_tool_call_status(tool_call_id, "approved")

    def _mark_tool_call_status(self, tool_call_id: str, status: ToolCallStatus) -> ToolCallRecord:
        with self.database.transaction() as connection:
            connection.execute("UPDATE tool_calls SET status = ?, updated_at = ? WHERE tool_call_id = ?", (status, now_iso(), tool_call_id))
        return self.get_tool_call(tool_call_id)

    def mark_tool_execution_started(self, tool_call_id: str) -> ToolCallRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'execution_started', started_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (timestamp, timestamp, tool_call_id),
            )
        return self.get_tool_call(tool_call_id)

    def persist_tool_execution_started(self, tool_call_id: str, recovery_token: dict[str, Any]) -> ToolCallRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'execution_started', recovery_token_json = ?, started_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (json_dumps(recovery_token), timestamp, timestamp, tool_call_id),
            )
        return self.get_tool_call(tool_call_id)

    def persist_tool_result(
        self,
        tool_call_id: str,
        *,
        call_status: ToolCallStatus,
        result_status: ToolResultStatus,
        content: Any,
        output_preview: str | None = None,
        artifact_id: str | None = None,
        error: str | None = None,
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultRecord:
        timestamp = now_iso()
        result_id = make_tool_result_id()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = ?, completed_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (call_status, timestamp, timestamp, tool_call_id),
            )
            connection.execute(
                "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, output_preview, artifact_id, error, success, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, tool_call_id, result_status, json_dumps(content), timestamp, output_preview, artifact_id, error, bool_to_int(success) if success is not None else None, json_dumps(metadata or {})),
            )
        return self.get_tool_result(result_id)

    def persist_recovered_tool_result(self, tool_call_id: str, *, status: ToolResultStatus, content: Any, output_preview: str | None = None, artifact_id: str | None = None, error: str | None = None, success: bool | None = None, metadata: dict[str, Any] | None = None) -> ToolResultRecord:
        return self.persist_tool_result(
            tool_call_id,
            call_status="completed" if status in {"success", "recovered_completed"} else "failed",
            result_status=status,
            content=content,
            output_preview=output_preview,
            artifact_id=artifact_id,
            error=error,
            success=success,
            metadata=metadata,
        )

    def create_tool_result(self, *, tool_call_id: str, status: ToolResultStatus, content: Any, output_preview: str | None = None, artifact_id: str | None = None, error: str | None = None, success: bool | None = None, metadata: dict[str, Any] | None = None) -> ToolResultRecord:
        timestamp = now_iso()
        result_id = make_tool_result_id()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, output_preview, artifact_id, error, success, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, tool_call_id, status, json_dumps(content), timestamp, output_preview, artifact_id, error, bool_to_int(success) if success is not None else None, json_dumps(metadata or {})),
            )
        return self.get_tool_result(result_id)

    def get_tool_result(self, result_id: str) -> ToolResultRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_results WHERE tool_result_id = ?", (result_id,)).fetchone()
        if row is None:
            raise LookupError(result_id)
        return tool_result_from_row(row)

    def get_tool_result_by_call(self, tool_call_id: str) -> ToolResultRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_results WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return tool_result_from_row(row) if row is not None else None

    def list_tool_calls(self, session_id: str) -> list[ToolCallRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY created_at, tool_call_id", (session_id,)
            ).fetchall()
        return [tool_call_from_row(row) for row in rows]

    def list_tool_results(self, session_id: str) -> list[ToolResultRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id JOIN turns t ON t.turn_id = tc.turn_id WHERE t.session_id = ? ORDER BY tr.created_at, tr.tool_result_id", (session_id,)
            ).fetchall()
        return [tool_result_from_row(row) for row in rows]

    def list_unresolved_tool_calls(self, turn_id: str | None = None) -> list[ToolCallRecord]:
        query = "SELECT * FROM tool_calls WHERE status NOT IN ('completed', 'failed')"
        params: list[Any] = []
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        with self.database.transaction() as connection:
            rows = connection.execute(query + " ORDER BY created_at, tool_call_id", params).fetchall()
        return [tool_call_from_row(row) for row in rows]

    def mark_tool_execution_uncertain_with_event(self, tool_call_id: str, error: str) -> ToolCallRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if row is None:
                raise LookupError(tool_call_id)
            connection.execute("UPDATE tool_calls SET status = 'execution_uncertain', updated_at = ? WHERE tool_call_id = ?", (timestamp, tool_call_id))
            session = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (row["turn_id"],)).fetchone()
            if session is None:
                raise LookupError(row["turn_id"])
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'tool_execution_uncertain', ?, ?, ?, ?, ?)",
                (make_event_id(), session["session_id"], session["session_id"], timestamp, row["turn_id"], row["attempt_id"], json_dumps({"tool_call_id": tool_call_id, "error": error}), "{}"),
            )
        return self.get_tool_call(tool_call_id)

    def require_tool_recovery(self, turn_id: str, attempt_id: str, tool_call_id: str | None, reason: str, worker_id: str) -> tuple[TurnRecord, RunAttemptRecord]:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = 'interrupted', ended_at = ?, interruption_reason = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ? WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (timestamp, reason, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = 'recovery_required', completed_at = ?, error_code = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'",
                (timestamp, reason, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            session_id = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'recovery_required', ?, ?, ?, ?, ?)",
                (make_event_id(), session_id, session_id, timestamp, turn_id, attempt_id, json_dumps({"tool_call_id": tool_call_id, "reason": reason}), json_dumps({"worker_id": worker_id})),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return turn_from_row(turn_row), attempt_from_row(attempt_row)
