from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_event_id, make_tool_result_id, now_iso
from codepilot.session.models import RunAttemptRecord
from codepilot.session.reconcilers import (
    RecoveryDecision,
    ReconciliationResult,
    reconcile_apply_patch,
    reconcile_read_only,
    reconcile_replace_range,
    reconcile_run_shell,
)
from codepilot.session.store import SessionStore


@dataclass(frozen=True)
class RecoveryPlan:
    session_id: str
    interrupted_turn_ids: tuple[str, ...]
    pending_approval_request_ids: tuple[str, ...]
    unresolved_tool_call_ids: tuple[str, ...]


class RecoveryService:
    """只恢复可确认的事实；不覆盖旧 Attempt，也不重复未知副作用。"""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database
        self.store = SessionStore(database)

    def inspect_session(self, session_id: str) -> RecoveryPlan:
        with self.database.transaction() as connection:
            turns = connection.execute(
                "SELECT turn_id FROM turns WHERE session_id = ? AND status IN ('running', 'interrupted', 'recovery_required')",
                (session_id,),
            ).fetchall()
            pending = connection.execute(
                "SELECT request_id FROM permission_requests WHERE session_id = ? AND status = 'pending'",
                (session_id,),
            ).fetchall()
            calls = connection.execute(
                "SELECT tool_call_id FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) "
                "AND status IN ('execution_started', 'execution_uncertain') AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results)",
                (session_id,),
            ).fetchall()
        return RecoveryPlan(session_id, tuple(row[0] for row in turns), tuple(row[0] for row in pending), tuple(row[0] for row in calls))

    def reconcile_tool_call(self, tool_call_id: str) -> ReconciliationResult:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        if row is None:
            raise LookupError(tool_call_id)
        arguments = _json_loads(row["arguments_json"])
        name = row["tool_name"]
        if name in {"list_files", "read_file", "search_code", "git_status", "git_diff", "run_tests"}:
            return reconcile_read_only(arguments=arguments)
        if name == "replace_range":
            return reconcile_replace_range(arguments)
        if name == "apply_patch":
            return reconcile_apply_patch(arguments)
        if name == "run_shell":
            return reconcile_run_shell(arguments)
        return ReconciliationResult(RecoveryDecision.UNKNOWN, "no reconciler is defined for this tool", {})

    def resume_low_risk_turn(self, turn_id: str) -> RunAttemptRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                raise LookupError(turn_id)
            unresolved = connection.execute(
                "SELECT 1 FROM tool_calls WHERE turn_id = ? AND status IN ('execution_started', 'execution_uncertain') "
                "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results)",
                (turn_id,),
            ).fetchone()
        if unresolved is not None:
            raise RuntimeError("turn has unresolved tool execution")
        attempt = self.store.create_attempt(turn_id=turn_id)
        self.store.update_turn_status(turn_id, "queued")
        self.store.append_event(session_id=row[0], event_type="turn_recovery_attempt_created", payload={"turn_id": turn_id, "attempt_id": attempt.attempt_id}, turn_id=turn_id, attempt_id=attempt.attempt_id)
        return attempt

    def resolve_unknown(self, tool_call_id: str, user_decision: str) -> RunAttemptRecord | None:
        user_decision = user_decision.replace("_", " ")
        if user_decision not in {"retry", "mark completed", "abort"}:
            raise ValueError(user_decision)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT turn_id FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if row is None:
                raise LookupError(tool_call_id)
            turn_id = row[0]
            session_id = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()[0]
            if user_decision == "mark completed":
                connection.execute("UPDATE tool_calls SET status = 'completed', completed_at = ?, updated_at = ? WHERE tool_call_id = ?", (now_iso(), now_iso(), tool_call_id))
                connection.execute("INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)", (make_tool_result_id(), tool_call_id, "recovered_completed", '"marked completed by user"', now_iso(), "{}"))
            elif user_decision == "abort":
                connection.execute("UPDATE turns SET status = 'cancelled', updated_at = ?, last_activity_at = ? WHERE turn_id = ?", (now_iso(), now_iso(), turn_id))
            elif user_decision == "retry":
                connection.execute("UPDATE tool_calls SET status = 'created', updated_at = ? WHERE tool_call_id = ?", (now_iso(), tool_call_id))
            connection.execute("INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), ?, ?, ?, NULL, ?, ?)", (make_event_id(), session_id, session_id, "recovery_user_decision", now_iso(), turn_id, _json_dumps({"tool_call_id": tool_call_id, "decision": user_decision}), "{}"))
        return self.resume_low_risk_turn(turn_id) if user_decision == "retry" else None


def _json_loads(value: str) -> Any:
    import json
    return json.loads(value)


def _json_dumps(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
