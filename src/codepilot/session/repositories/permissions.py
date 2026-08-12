from __future__ import annotations

from typing import Any

from codepilot.permissions import PermissionRequest, permission_now_iso
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_event_id, now_iso
from codepilot.session.models import PermissionGrantRecord, PermissionRequestRecord, PermissionResponseRecord
from codepilot.session.row_mappers import permission_grant_from_row, permission_request_from_row, permission_response_from_row
from codepilot.session.repositories._support import json_dumps, local_id


class PermissionRepository:
    """Permission records and their atomic request/response state transitions."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def list_permission_requests(self, session_id: str) -> list[PermissionRequestRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM permission_requests WHERE session_id = ? ORDER BY created_at, request_id", (session_id,)).fetchall()
        return [permission_request_from_row(row) for row in rows]

    def get_permission_request(self, request_id: str) -> PermissionRequestRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise LookupError(request_id)
        return permission_request_from_row(row)

    def get_permission_response_by_request(self, request_id: str) -> PermissionResponseRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1", (request_id,)).fetchone()
        return permission_response_from_row(row) if row is not None else None

    def get_permission_grant(self, session_id: str, scope_key: str) -> PermissionGrantRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_grants WHERE session_id = ? AND scope_key = ? AND revoked_at IS NULL ORDER BY created_at DESC, grant_id DESC LIMIT 1", (session_id, scope_key)).fetchone()
        return permission_grant_from_row(row) if row is not None else None

    def list_pending_permission_requests(self, session_id: str) -> list[PermissionRequestRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM permission_requests WHERE session_id = ? AND status = 'pending' ORDER BY created_at, request_id", (session_id,)).fetchall()
        return [permission_request_from_row(row) for row in rows]

    def create_permission_request(self, *, request_id: str, tool_name: str, arguments: dict[str, Any], reason: str, status: str, created_at: str | None = None, session_id: str | None = None, turn_id: str | None = None, attempt_id: str | None = None, tool_call_id: str | None = None, scope_key: str | None = None, metadata: dict[str, Any] | None = None) -> PermissionRequestRecord:
        created_at = created_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO permission_requests(request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name, arguments_json, reason, status, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name, json_dumps(arguments), reason, status, created_at, json_dumps(metadata or {})),
            )
        return self.get_permission_request(request_id)

    def persist_permission_request_and_pending_call(self, request: PermissionRequest) -> PermissionRequestRecord:
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM permission_requests WHERE request_id = ?", (request.request_id,)).fetchone() is None:
                connection.execute(
                    "INSERT INTO permission_requests(request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name, arguments_json, reason, status, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (request.request_id, request.session_id, request.turn_id, request.attempt_id, request.tool_call_id, request.scope_key, request.tool_name, json_dumps(request.arguments_preview), request.reason, request.status, request.created_at, json_dumps({"run_id": request.run_id, "action_id": request.action_id, "risk": request.risk, "side_effect": request.side_effect, "external_impact": request.external_impact, "reversibility": request.reversibility, "matched_rule": request.matched_rule, "scope_json": request.scope_json})),
                )
            if request.tool_call_id is not None:
                connection.execute("UPDATE tool_calls SET status = 'approval_pending', updated_at = ? WHERE tool_call_id = ?", (request.created_at, request.tool_call_id))
            if request.turn_id is not None:
                connection.execute("UPDATE turns SET status = 'waiting_permission', updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'", (request.created_at, request.created_at, request.turn_id))
            if request.session_id is not None:
                connection.execute("UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?", (request.created_at, request.created_at, request.session_id))
                connection.execute(
                    "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'permission_pending', ?, ?, ?, ?, ?)",
                    (make_event_id(), request.session_id, request.session_id, request.created_at, request.turn_id, request.attempt_id, json_dumps({"request_id": request.request_id, "tool_name": request.tool_name, "tool_call_id": request.tool_call_id, "scope_key": request.scope_key}), json_dumps({"source": "permission_broker"})),
                )
        return self.get_permission_request(request.request_id)

    def persist_permission_resolution(self, request_id: str, decision: str, reason: str | None, *, create_grant: bool, source: str) -> PermissionResponseRecord:
        responded_at = permission_now_iso()
        with self.database.transaction() as connection:
            request_row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
            if request_row is None:
                raise LookupError(request_id)
            request = permission_request_from_row(request_row)
            existing = connection.execute("SELECT * FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1", (request_id,)).fetchone()
            if existing is not None:
                return permission_response_from_row(existing)
            if request.status != "pending":
                raise RuntimeError("permission request is no longer pending")
            response_id = f"response-{request_id}"
            connection.execute("INSERT INTO permission_responses(response_id, request_id, decision, reason, responded_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)", (response_id, request_id, decision, reason, responded_at, json_dumps({"source": source})))
            approved = decision in {"approve_once", "approve_session"}
            connection.execute("UPDATE permission_requests SET status = ? WHERE request_id = ?", ("approved" if approved else "denied", request_id))
            grant_id: str | None = None
            if create_grant and decision == "approve_session" and request.scope_key is not None and request.session_id is not None:
                grant = connection.execute("SELECT grant_id FROM permission_grants WHERE session_id = ? AND scope_key = ? AND revoked_at IS NULL ORDER BY created_at DESC, grant_id DESC LIMIT 1", (request.session_id, request.scope_key)).fetchone()
                if grant is None:
                    grant_id = local_id("grant")
                    connection.execute("INSERT INTO permission_grants(grant_id, session_id, scope_key, tool_name, scope_json, created_at, revoked_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)", (grant_id, request.session_id, request.scope_key, request.tool_name, json_dumps(request.metadata.get("scope_json")) if request.metadata.get("scope_json") is not None else None, responded_at, json_dumps({"request_id": request_id, "source": source})))
                else:
                    grant_id = str(grant["grant_id"])
            if request.tool_call_id is not None:
                connection.execute("UPDATE tool_calls SET status = ?, completed_at = CASE WHEN ? = 'denied' THEN COALESCE(completed_at, ?) ELSE NULL END, updated_at = ? WHERE tool_call_id = ?", ("approved" if approved else "denied", "approved" if approved else "denied", responded_at, responded_at, request.tool_call_id))
            if request.turn_id is not None:
                connection.execute("UPDATE turns SET status = 'running', updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'waiting_permission'", (responded_at, responded_at, request.turn_id))
            if request.session_id is not None:
                connection.execute("UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?", (responded_at, responded_at, request.session_id))
                connection.execute(
                    "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'permission_resolved', ?, ?, ?, ?, ?)",
                    (make_event_id(), request.session_id, request.session_id, responded_at, request.turn_id, request.attempt_id, json_dumps({"request_id": request_id, "decision": decision, "reason": reason, "source": source, "scope_key": request.scope_key, "grant_id": grant_id, "tool_call_id": request.tool_call_id}), "{}"),
                )
        return self.get_permission_response_by_request(request_id)  # type: ignore[return-value]

    def create_permission_response(self, *, response_id: str, request_id: str, decision: str, reason: str | None, responded_at: str | None = None, metadata: dict[str, Any] | None = None) -> PermissionResponseRecord:
        timestamp = responded_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO permission_responses(response_id, request_id, decision, reason, responded_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)", (response_id, request_id, decision, reason, timestamp, json_dumps(metadata or {})))
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_responses WHERE response_id = ?", (response_id,)).fetchone()
        return permission_response_from_row(row)

    def create_permission_grant(self, *, session_id: str, scope_key: str, tool_name: str | None = None, scope_json: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None, revoked_at: str | None = None) -> PermissionGrantRecord:
        timestamp = now_iso()
        grant_id = local_id("grant")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO permission_grants(grant_id, session_id, scope_key, tool_name, scope_json, created_at, revoked_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (grant_id, session_id, scope_key, tool_name, json_dumps(scope_json) if scope_json is not None else None, timestamp, revoked_at, json_dumps(metadata or {})))
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_grants WHERE grant_id = ?", (grant_id,)).fetchone()
        return permission_grant_from_row(row)
