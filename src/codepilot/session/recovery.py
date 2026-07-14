from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_attempt_id, make_event_id, make_message_id, make_tool_result_id, now_iso
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
    resumable_attempt_ids: tuple[str, ...] = ()


class RecoveryService:
    """只自动持久化能够由 durable token 明确对账的恢复事实。"""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database
        self.store = SessionStore(database)

    def inspect_session(self, session_id: str) -> RecoveryPlan:
        with self.database.transaction() as connection:
            turn_rows = connection.execute(
                "SELECT turn_id FROM turns WHERE session_id = ? AND status IN ('running', 'interrupted', 'recovery_required') ORDER BY sequence",
                (session_id,),
            ).fetchall()
            active_turn_ids = {
                row["turn_id"]
                for row in connection.execute(
                    "SELECT ra.turn_id, ra.worker_id, ra.lease_expires_at FROM run_attempts ra JOIN turns t ON t.turn_id = ra.turn_id "
                    "WHERE t.session_id = ? AND ra.status = 'running'",
                    (session_id,),
                ).fetchall()
                if _worker_is_active(row["worker_id"], row["lease_expires_at"])
            }
            turns = [row for row in turn_rows if row["turn_id"] not in active_turn_ids]
            pending = connection.execute(
                "SELECT request_id FROM permission_requests WHERE session_id = ? AND status = 'pending' ORDER BY created_at, request_id",
                (session_id,),
            ).fetchall()
            call_rows = connection.execute(
                "SELECT tool_call_id FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) "
                "AND status IN ('execution_started', 'execution_uncertain', 'recovery_required') "
                "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results) ORDER BY created_at, tool_call_id",
                (session_id,),
            ).fetchall()
            calls = [
                row
                for row in call_rows
                if connection.execute("SELECT turn_id FROM tool_calls WHERE tool_call_id = ?", (row[0],)).fetchone()[0] not in active_turn_ids
            ]
            resumable = connection.execute(
                "SELECT ra.attempt_id FROM run_attempts ra JOIN turns t ON t.turn_id = ra.turn_id "
                "WHERE t.session_id = ? AND t.status = 'queued' AND ra.status = 'created' ORDER BY t.sequence, ra.attempt_number",
                (session_id,),
            ).fetchall()
        return RecoveryPlan(
            session_id,
            tuple(row[0] for row in turns),
            tuple(row[0] for row in pending),
            tuple(row[0] for row in calls),
            tuple(row[0] for row in resumable),
        )

    def recover_session(self, session_id: str) -> RecoveryPlan:
        """显式打开 Session 时正规化中断消息并持久化自动对账结果。"""

        plan = self.inspect_session(session_id)
        self._normalize_in_progress_messages(plan.interrupted_turn_ids)
        for tool_call_id in plan.unresolved_tool_call_ids:
            self.persist_reconciliation(tool_call_id, self.reconcile_tool_call(tool_call_id))
        created: tuple[str, ...] = ()
        if not plan.resumable_attempt_ids:
            for turn_id in plan.interrupted_turn_ids:
                attempt = self._resume_if_reconciled(turn_id)
                if attempt is not None:
                    created = (attempt.attempt_id,)
                    break
        inspected = self.inspect_session(session_id)
        return RecoveryPlan(
            inspected.session_id,
            inspected.interrupted_turn_ids,
            inspected.pending_approval_request_ids,
            inspected.unresolved_tool_call_ids,
            tuple(dict.fromkeys((*inspected.resumable_attempt_ids, *created))),
        )

    def resume_after_permission(self, request_id: str) -> RunAttemptRecord | None:
        """在重启后的权限决定完成后，为同一 Turn 建立确定的恢复状态。

        原 Worker 的同步等待栈已经不存在，因此绝不能尝试唤醒旧 Attempt。批准时会将
        旧 Attempt 标为 interrupted 并创建下一编号 Attempt；拒绝时则原子终止 Turn。
        """

        timestamp = now_iso()
        new_attempt_id = make_attempt_id()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT pr.turn_id, pr.attempt_id, pr.tool_call_id, pr.status, t.session_id "
                "FROM permission_requests pr JOIN turns t ON t.turn_id = pr.turn_id WHERE pr.request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise LookupError(request_id)
            response = connection.execute(
                "SELECT decision FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1",
                (request_id,),
            ).fetchone()
            if response is None or row["status"] == "pending":
                raise RuntimeError("restored permission request is not resolved")

            if response["decision"] not in {"approve_once", "approve_session"}:
                connection.execute(
                    "UPDATE run_attempts SET status = 'cancelled', ended_at = COALESCE(ended_at, ?), interruption_reason = 'permission denied after restart', worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE attempt_id = ? AND status IN ('created', 'running', 'interrupted')",
                    (timestamp, timestamp, row["attempt_id"]),
                )
                connection.execute(
                    "UPDATE turns SET status = 'cancelled', completed_at = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                    (timestamp, timestamp, timestamp, row["turn_id"]),
                )
                event_type = "permission_recovery_denied"
                attempt_id = row["attempt_id"]
            else:
                # 批准只表示审计事实已经解决，不代表旧进程执行过工具。新的 Attempt 从
                # SQLite 历史重新组装上下文，因此不会直接重放未知副作用。
                connection.execute(
                    "UPDATE run_attempts SET status = 'interrupted', ended_at = COALESCE(ended_at, ?), interruption_reason = 'permission resolved after restart', worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE attempt_id = ? AND status IN ('created', 'running')",
                    (timestamp, timestamp, row["attempt_id"]),
                )
                number = connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?",
                    (row["turn_id"],),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO run_attempts(attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, interruption_reason, metadata_json) "
                    "VALUES (?, ?, ?, 'created', ?, ?, NULL, NULL, NULL, '{}')",
                    (new_attempt_id, row["turn_id"], number, timestamp, timestamp),
                )
                connection.execute(
                    "UPDATE turns SET status = 'queued', completed_at = NULL, updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                    (timestamp, timestamp, row["turn_id"]),
                )
                event_type = "permission_recovery_resumed"
                attempt_id = new_attempt_id

            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), ?, ?, ?, ?, ?, '{}')",
                (
                    make_event_id(),
                    row["session_id"],
                    row["session_id"],
                    event_type,
                    timestamp,
                    row["turn_id"],
                    attempt_id,
                    json.dumps({"request_id": request_id, "tool_call_id": row["tool_call_id"]}, separators=(",", ":")),
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                (timestamp, timestamp, row["session_id"]),
            )
        return self.store.get_attempt(new_attempt_id) if response["decision"] in {"approve_once", "approve_session"} else None

    def _normalize_in_progress_messages(self, turn_ids: tuple[str, ...]) -> None:
        if not turn_ids:
            return
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE messages SET status = 'interrupted', interrupted_at = ?, updated_at = ? "
                f"WHERE turn_id IN ({','.join('?' for _ in turn_ids)}) AND role = 'assistant' AND status = 'in_progress'",
                (timestamp, timestamp, *turn_ids),
            )

    def reconcile_tool_call(self, tool_call_id: str) -> ReconciliationResult:
        call = self.store.get_tool_call(tool_call_id)
        token = call.recovery_token
        if token is None:
            return ReconciliationResult(RecoveryDecision.UNKNOWN, "durable recovery token is missing", {})
        if call.tool_name in {"list_files", "read_file", "search_code", "git_status", "git_diff", "run_tests"}:
            return reconcile_read_only(arguments=call.arguments)
        if call.tool_name == "replace_range":
            return reconcile_replace_range(token)
        if call.tool_name == "apply_patch":
            return reconcile_apply_patch(call.arguments, token)
        if call.tool_name == "run_shell":
            return reconcile_run_shell(call.arguments, token)
        return ReconciliationResult(RecoveryDecision.UNKNOWN, "no reconciler is defined for this tool", {})

    def persist_reconciliation(self, tool_call_id: str, result: ReconciliationResult) -> None:
        """原子写入一次 reconciliation；重复打开不会重复生成 Result 或 Event。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT tc.turn_id, t.session_id, tc.attempt_id, tc.status, tc.tool_name, tc.arguments_json, tc.metadata_json "
                "FROM tool_calls tc JOIN turns t ON t.turn_id = tc.turn_id WHERE tc.tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if row is None:
                raise LookupError(tool_call_id)
            if connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (tool_call_id,)).fetchone() is not None:
                return

            if result.decision == RecoveryDecision.COMPLETED:
                call_status = "completed"
                result_status = "recovered_completed"
            elif result.decision == RecoveryDecision.NOT_EXECUTED:
                call_status = "failed"
                result_status = "recovered_not_executed"
            else:
                if row["status"] == "recovery_required":
                    return
                connection.execute(
                    "UPDATE tool_calls SET status = 'recovery_required', updated_at = ? WHERE tool_call_id = ?",
                    (timestamp, tool_call_id),
                )
                connection.execute(
                    "UPDATE turns SET status = 'recovery_required', updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                    (timestamp, timestamp, row["turn_id"]),
                )
                if row["attempt_id"] is not None:
                    connection.execute(
                        "UPDATE run_attempts SET status = 'interrupted', ended_at = COALESCE(ended_at, ?), interruption_reason = COALESCE(interruption_reason, 'tool execution uncertain'), updated_at = ? "
                        "WHERE attempt_id = ? AND status IN ('created', 'running')",
                        (timestamp, timestamp, row["attempt_id"]),
                    )
                self._append_recovery_event_in(connection, row["session_id"], row["turn_id"], row["attempt_id"], tool_call_id, result)
                return

            connection.execute(
                "UPDATE tool_calls SET status = ?, completed_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (call_status, timestamp, timestamp, tool_call_id),
            )
            connection.execute(
                "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    make_tool_result_id(),
                    tool_call_id,
                    result_status,
                    json.dumps(result.detail, ensure_ascii=False),
                    timestamp,
                    json.dumps(result.metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._insert_recovery_message_in(
                connection,
                row["session_id"],
                row["turn_id"],
                row["attempt_id"],
                tool_call_id,
                result_status,
                result.detail,
                row["tool_name"],
                json.loads(row["arguments_json"]),
                json.loads(row["metadata_json"]).get("action_id"),
                timestamp,
            )
            if row["attempt_id"] is not None:
                connection.execute(
                    "UPDATE run_attempts SET status = 'interrupted', ended_at = COALESCE(ended_at, ?), interruption_reason = COALESCE(interruption_reason, 'process recovery'), updated_at = ? "
                    "WHERE attempt_id = ? AND status IN ('created', 'running')",
                    (timestamp, timestamp, row["attempt_id"]),
                )
            self._append_recovery_event_in(connection, row["session_id"], row["turn_id"], row["attempt_id"], tool_call_id, result)

    def _append_recovery_event_in(self, connection, session_id: str, turn_id: str, attempt_id: str | None, tool_call_id: str, result: ReconciliationResult) -> None:
        connection.execute(
            "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
            "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'tool_reconciled', ?, ?, ?, ?, ?)",
            (
                make_event_id(),
                session_id,
                session_id,
                now_iso(),
                turn_id,
                attempt_id,
                json.dumps({"tool_call_id": tool_call_id, "decision": result.decision.value, "detail": result.detail}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(result.metadata, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def _insert_recovery_message_in(
        self,
        connection,
        session_id: str,
        turn_id: str,
        attempt_id: str | None,
        tool_call_id: str,
        status: str,
        detail: str,
        tool_name: str,
        arguments: dict,
        action_id: str | None,
        timestamp: str,
    ) -> None:
        """把对账事实写入可回放上下文，防止模型重复已完成副作用。"""

        content = {
            "tool_call_id": tool_call_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "arguments_summary": _safe_recovery_arguments(tool_name, arguments),
            "recovery_status": status,
            "detail": detail,
            "instruction": "Treat this persisted recovery fact as authoritative before choosing the next action.",
        }
        connection.execute(
            "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) "
            "VALUES (?, ?, ?, ?, 'system', 'completed', ?, ?, ?, NULL, ?)",
            (
                make_message_id(),
                session_id,
                turn_id,
                attempt_id,
                json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                timestamp,
                timestamp,
                json.dumps({"tool_call_id": tool_call_id, "recovery_status": status}, separators=(",", ":")),
            ),
        )

    def _resume_if_reconciled(self, turn_id: str) -> RunAttemptRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                raise LookupError(turn_id)
            unresolved = connection.execute(
                "SELECT 1 FROM tool_calls WHERE turn_id = ? AND status IN ('execution_started', 'execution_uncertain', 'recovery_required') "
                "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results) LIMIT 1",
                (turn_id,),
            ).fetchone()
            pending_approval = connection.execute(
                "SELECT 1 FROM permission_requests WHERE turn_id = ? AND status = 'pending' LIMIT 1",
                (turn_id,),
            ).fetchone()
            approval_call = connection.execute(
                "SELECT 1 FROM tool_calls WHERE turn_id = ? AND status = 'approval_pending' LIMIT 1",
                (turn_id,),
            ).fetchone()
            if row["status"] == "recovery_required" or unresolved is not None or pending_approval is not None or approval_call is not None:
                return None
        return self.resume_low_risk_turn(turn_id)

    def resume_low_risk_turn(self, turn_id: str) -> RunAttemptRecord:
        """原子创建新的恢复 Attempt，绝不覆盖旧 Attempt。"""

        timestamp = now_iso()
        attempt_id = make_attempt_id()
        existing_attempt_id: str | None = None
        with self.database.transaction() as connection:
            row = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                raise LookupError(turn_id)
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE turn_id = ? AND status IN ('execution_started', 'execution_uncertain', 'recovery_required') "
                "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results) LIMIT 1",
                (turn_id,),
            ).fetchone() is not None:
                raise RuntimeError("turn has unresolved tool execution")
            if connection.execute("SELECT 1 FROM permission_requests WHERE turn_id = ? AND status = 'pending' LIMIT 1", (turn_id,)).fetchone() is not None:
                raise RuntimeError("turn has pending permission approval")
            if connection.execute("SELECT 1 FROM tool_calls WHERE turn_id = ? AND status = 'approval_pending' LIMIT 1", (turn_id,)).fetchone() is not None:
                raise RuntimeError("turn has approval-pending tool call")
            existing = connection.execute(
                "SELECT attempt_id FROM run_attempts WHERE turn_id = ? AND status = 'created' ORDER BY attempt_number DESC LIMIT 1",
                (turn_id,),
            ).fetchone()
            if existing is not None:
                existing_attempt_id = existing[0]
            else:
                number = connection.execute("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?", (turn_id,)).fetchone()[0]
                connection.execute(
                    "INSERT INTO run_attempts(attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, interruption_reason, metadata_json) "
                    "VALUES (?, ?, ?, 'created', ?, ?, NULL, NULL, NULL, ?)",
                    (attempt_id, turn_id, number, timestamp, timestamp, "{}"),
                )
                connection.execute("UPDATE turns SET status = 'queued', updated_at = ?, last_activity_at = ? WHERE turn_id = ?", (timestamp, timestamp, turn_id))
                connection.execute(
                    "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                    "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'turn_recovery_attempt_created', ?, ?, ?, ?, '{}')",
                    (make_event_id(), row[0], row[0], timestamp, turn_id, attempt_id, json.dumps({"turn_id": turn_id, "attempt_id": attempt_id}, separators=(",", ":"))),
                )
        return self.store.get_attempt(existing_attempt_id or attempt_id)

    def resolve_unknown(self, tool_call_id: str, user_decision: str) -> RunAttemptRecord | None:
        """原子记录用户对未知副作用的承担方式，不重置原 ToolCall 历史。"""

        decision = user_decision.replace("_", " ")
        if decision not in {"retry", "mark completed", "abort"}:
            raise ValueError(user_decision)
        timestamp = now_iso()
        new_attempt_id: str | None = None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT tc.turn_id, tc.status, tc.attempt_id, tc.tool_name, tc.arguments_json, tc.metadata_json, "
                "t.session_id, t.status AS turn_status FROM tool_calls tc JOIN turns t ON t.turn_id = tc.turn_id WHERE tc.tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if row is None:
                raise LookupError(tool_call_id)
            if row["status"] not in {"execution_uncertain", "recovery_required"}:
                raise RuntimeError("tool call does not require user recovery")
            if row["turn_status"] != "recovery_required":
                raise RuntimeError("turn does not require user recovery")
            existing_result = connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if existing_result is not None:
                raise RuntimeError("tool call recovery is already resolved")

            if decision == "abort":
                unresolved_calls = connection.execute(
                    "SELECT tool_call_id FROM tool_calls WHERE turn_id = ? AND status IN ('execution_started', 'execution_uncertain', 'recovery_required')",
                    (row["turn_id"],),
                ).fetchall()
                for unresolved_call in unresolved_calls:
                    unresolved_id = unresolved_call[0]
                    connection.execute("UPDATE tool_calls SET status = 'recovery_aborted', updated_at = ?, completed_at = ? WHERE tool_call_id = ?", (timestamp, timestamp, unresolved_id))
                    if connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (unresolved_id,)).fetchone() is not None:
                        continue
                    connection.execute(
                        "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, 'recovery_aborted', ?, ?, '{}')",
                        (make_tool_result_id(), unresolved_id, json.dumps("turn recovery aborted by user"), timestamp),
                    )
                connection.execute("UPDATE turns SET status = 'cancelled', updated_at = ?, last_activity_at = ? WHERE turn_id = ?", (timestamp, timestamp, row["turn_id"]))
                connection.execute(
                    "UPDATE run_attempts SET status = 'cancelled', ended_at = COALESCE(ended_at, ?), interruption_reason = COALESCE(interruption_reason, 'recovery aborted by user'), worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE turn_id = ? AND status IN ('created', 'running', 'interrupted')",
                    (timestamp, timestamp, row["turn_id"]),
                )
                connection.execute(
                    "UPDATE permission_requests SET status = 'cancelled' WHERE turn_id = ? AND status = 'pending'",
                    (row["turn_id"],),
                )
            elif decision == "mark completed":
                connection.execute("UPDATE tool_calls SET status = 'completed', updated_at = ?, completed_at = ? WHERE tool_call_id = ?", (timestamp, timestamp, tool_call_id))
                if connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (tool_call_id,)).fetchone() is None:
                    connection.execute(
                        "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, 'recovered_completed', ?, ?, ?)",
                        (make_tool_result_id(), tool_call_id, json.dumps("marked completed by user"), timestamp, json.dumps({"source": "user"})),
                    )
                self._insert_recovery_message_in(
                    connection,
                    row["session_id"],
                    row["turn_id"],
                    row["attempt_id"],
                    tool_call_id,
                    "recovered_completed",
                    "marked completed by user",
                    row["tool_name"],
                    json.loads(row["arguments_json"]),
                    json.loads(row["metadata_json"]).get("action_id"),
                    timestamp,
                )
            elif decision == "retry":
                # 原调用仍明确保留为 uncertain；Result 只记录用户接受重复副作用风险。
                connection.execute("UPDATE tool_calls SET status = 'execution_uncertain', updated_at = ? WHERE tool_call_id = ?", (timestamp, tool_call_id))
                connection.execute(
                    "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, 'execution_uncertain', ?, ?, ?)",
                    (make_tool_result_id(), tool_call_id, json.dumps("retry accepted by user"), timestamp, json.dumps({"source": "user", "duplicate_side_effect_risk_accepted": True})),
                )

            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'recovery_user_decision', ?, ?, ?, ?, '{}')",
                (make_event_id(), row["session_id"], row["session_id"], timestamp, row["turn_id"], row["attempt_id"], json.dumps({"tool_call_id": tool_call_id, "decision": decision}, separators=(",", ":"))),
            )

            if decision != "abort":
                unresolved = connection.execute(
                    "SELECT 1 FROM tool_calls WHERE turn_id = ? AND status IN ('execution_started', 'execution_uncertain', 'recovery_required') "
                    "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results) LIMIT 1",
                    (row["turn_id"],),
                ).fetchone()
                if unresolved is None:
                    new_attempt_id = make_attempt_id()
                    number = connection.execute("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?", (row["turn_id"],)).fetchone()[0]
                    connection.execute(
                        "INSERT INTO run_attempts(attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, interruption_reason, metadata_json) VALUES (?, ?, ?, 'created', ?, ?, NULL, NULL, NULL, '{}')",
                        (new_attempt_id, row["turn_id"], number, timestamp, timestamp),
                    )
                    connection.execute("UPDATE turns SET status = 'queued', updated_at = ?, last_activity_at = ? WHERE turn_id = ?", (timestamp, timestamp, row["turn_id"]))
        return self.store.get_attempt(new_attempt_id) if new_attempt_id is not None else None

    def abort_pending_approval(self, request_id: str) -> None:
        """安全取消崩溃遗留的审批及其整个 Turn，不伪造批准结果。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT pr.turn_id, pr.tool_call_id, pr.attempt_id, pr.session_id FROM permission_requests pr WHERE pr.request_id = ? AND pr.status = 'pending'",
                (request_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("permission request is not pending")
            connection.execute("UPDATE permission_requests SET status = 'cancelled' WHERE request_id = ?", (request_id,))
            if row["tool_call_id"] is not None:
                connection.execute(
                    "UPDATE tool_calls SET status = 'recovery_aborted', completed_at = ?, updated_at = ? WHERE tool_call_id = ? AND status = 'approval_pending'",
                    (timestamp, timestamp, row["tool_call_id"]),
                )
                if connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (row["tool_call_id"],)).fetchone() is None:
                    connection.execute(
                        "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json) VALUES (?, ?, 'recovery_aborted', ?, ?, '{}')",
                        (make_tool_result_id(), row["tool_call_id"], json.dumps("pending approval aborted after recovery"), timestamp),
                    )
            connection.execute(
                "UPDATE run_attempts SET status = 'cancelled', ended_at = COALESCE(ended_at, ?), interruption_reason = COALESCE(interruption_reason, 'pending approval aborted'), worker_id = NULL, lease_expires_at = NULL, updated_at = ? WHERE attempt_id = ?",
                (timestamp, timestamp, row["attempt_id"]),
            )
            connection.execute("UPDATE turns SET status = 'cancelled', updated_at = ?, last_activity_at = ? WHERE turn_id = ?", (timestamp, timestamp, row["turn_id"]))
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'pending_approval_aborted', ?, ?, ?, ?, '{}')",
                (make_event_id(), row["session_id"], row["session_id"], timestamp, row["turn_id"], row["attempt_id"], json.dumps({"request_id": request_id}, separators=(",", ":"))),
            )


def _worker_is_active(worker_id: str | None, lease_expires_at: str | None) -> bool:
    """本机 PID 存活时禁止恢复；远端 Worker 仅在 lease 过期后允许恢复。"""

    if worker_id is None or lease_expires_at is None:
        return False
    if datetime.fromisoformat(lease_expires_at) <= datetime.now(UTC):
        return False
    parts = worker_id.split(":", 2)
    if len(parts) >= 2 and parts[0] == socket.gethostname() and parts[1].isdigit():
        try:
            os.kill(int(parts[1]), 0)
        except OSError:
            return False
        return True
    return True


def _safe_recovery_arguments(tool_name: str, arguments: dict) -> dict:
    """只回放能唯一识别副作用的参数摘要，避免复制完整 patch 或命令正文。"""

    if tool_name == "replace_range":
        return {key: arguments.get(key) for key in ("path", "start_line", "end_line")}
    if tool_name == "apply_patch":
        patch = str(arguments.get("patch", ""))
        return {"repo": arguments.get("repo"), "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()}
    if tool_name == "run_shell":
        command = str(arguments.get("command", ""))
        return {"repo": arguments.get("repo"), "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()}
    return {key: arguments.get(key) for key in sorted(arguments) if key != "repo"}
