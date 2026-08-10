from __future__ import annotations

from collections.abc import Callable

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_attempt_id, make_event_id, make_message_id, make_turn_id, now_iso
from codepilot.session.models import BranchConfirmationRequired, TurnSubmission
from codepilot.session.row_mappers import attempt_from_row, turn_from_row
from codepilot.session.repositories._support import json_dumps

BLOCKING_TURN_STATUSES = ("queued", "running", "waiting_permission", "recovery_required")


class TurnSubmissionService:
    """Atomic application boundary for branch validation and first-turn writes."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_turn_submission(self, *, session_id: str, text: str, actual_branch_reader: Callable[[], str | None], confirmed_branch: str | None, branch_confirmation_provided: bool) -> TurnSubmission | BranchConfirmationRequired:
        timestamp = now_iso()
        turn_id = make_turn_id()
        attempt_id = make_attempt_id()
        message_id = make_message_id()
        with self.database.transaction() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError(session_id)
            if session["status"] != "active":
                raise ValueError("archived session is read-only")
            placeholders = ",".join("?" for _ in BLOCKING_TURN_STATUSES)
            if connection.execute(f"SELECT 1 FROM turns WHERE session_id = ? AND status IN ({placeholders}) LIMIT 1", (session_id, *BLOCKING_TURN_STATUSES)).fetchone() is not None:
                raise RuntimeError("session already has a running turn")
            if connection.execute("SELECT 1 FROM permission_requests WHERE session_id = ? AND status = 'pending' LIMIT 1", (session_id,)).fetchone() is not None:
                raise RuntimeError("session already has a pending permission request")
            if connection.execute("SELECT 1 FROM tool_calls tc JOIN turns t ON t.turn_id = tc.turn_id WHERE t.session_id = ? AND tc.status IN ('approval_pending', 'execution_started', 'execution_uncertain', 'recovery_required') LIMIT 1", (session_id,)).fetchone() is not None:
                raise RuntimeError("session already has an unresolved tool call")
            actual_branch = actual_branch_reader()
            old_branch = session["current_branch"]
            if old_branch != actual_branch and not branch_confirmation_provided:
                return BranchConfirmationRequired(session_id, old_branch, actual_branch)
            if branch_confirmation_provided and confirmed_branch != actual_branch:
                return BranchConfirmationRequired(session_id, confirmed_branch, actual_branch)
            event_sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM session_events WHERE session_id = ?", (session_id,)).fetchone()[0]
            turn_sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?", (session_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO turns(turn_id, session_id, sequence, title, status, provider_snapshot, model_snapshot, permission_mode_snapshot, branch_snapshot, created_at, updated_at, last_activity_at, user_message_id, started_at, completed_at, error_code, metadata_json) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, '{}')",
                (turn_id, session_id, turn_sequence, f"Turn {turn_sequence}", session["provider"], session["current_model"], session["permission_mode"], actual_branch, timestamp, timestamp, timestamp),
            )
            connection.execute("INSERT INTO run_attempts(attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, metadata_json) VALUES (?, ?, 1, 'created', ?, ?, NULL, NULL, '{}')", (attempt_id, turn_id, timestamp, timestamp))
            if old_branch != actual_branch:
                event_sequence += 1
                connection.execute("INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, ?, 'branch_changed', ?, ?, ?, ?, '{}')", (make_event_id(), session_id, event_sequence, timestamp, turn_id, attempt_id, json_dumps({"old_branch": old_branch, "new_branch": actual_branch, "effective_turn_sequence": turn_sequence})))
            connection.execute("INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) VALUES (?, ?, ?, NULL, 'user', 'completed', ?, ?, ?, NULL, '{}')", (message_id, session_id, turn_id, json_dumps(text), timestamp, timestamp))
            connection.execute("UPDATE turns SET user_message_id = ? WHERE turn_id = ?", (message_id, turn_id))
            first_user_message = connection.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'", (session_id,)).fetchone()[0] == 1
            title = " ".join(text.split())[:80] or "New session" if session["title"] == "New session" and first_user_message else session["title"]
            connection.execute("UPDATE sessions SET title = ?, current_branch = ?, updated_at = ?, last_activity_at = ? WHERE session_id = ?", (title, actual_branch, timestamp, timestamp, session_id))
            for event_type, payload in (("turn_created", {"turn_id": turn_id}), ("user_message_created", {"text": text})):
                event_sequence += 1
                connection.execute("INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')", (make_event_id(), session_id, event_sequence, event_type, timestamp, turn_id, attempt_id, json_dumps(payload)))
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return TurnSubmission(turn_from_row(turn_row), attempt_from_row(attempt_row))
