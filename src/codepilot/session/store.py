from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import (
    make_attempt_id,
    make_artifact_id,
    make_event_id,
    make_message_id,
    make_part_id,
    make_project_id,
    make_session_id,
    make_tool_call_id,
    make_tool_result_id,
    make_turn_id,
    now_iso,
)
from codepilot.session.models import (
    ArtifactRecord,
    ContextSummaryRecord,
    MessagePartRecord,
    MessageRecord,
    PermissionGrantRecord,
    PermissionRequestRecord,
    PermissionResponseRecord,
    ProjectRecord,
    RunAttemptRecord,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    SessionSummary,
    ToolCallRecord,
    ToolCallStatus,
    ToolResultRecord,
    ToolResultStatus,
    TurnRecord,
    TurnStatus,
)
from codepilot.session.paths import SessionPaths, resolve_session_paths


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _int_to_bool(value: Any) -> bool:
    return bool(int(value))


def _make_local_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class SessionStore:
    """SQLite Session 的薄仓库层。

    这里不塞业务流程，只负责记录的增删改查和 JSON 编解码。
    """

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.database = database
        self.paths = paths or resolve_session_paths(database.path.parent)

    def create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        created_at = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO projects(project_id, path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (make_project_id(), str(resolved), created_at, created_at),
                )
                row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
        return self._project_from_row(row)

    def get_or_create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is not None:
                return self._project_from_row(row)
        return self.create_project(resolved)

    def create_session(
        self,
        *,
        project_path: Path,
        provider: str,
        current_model: str,
        permission_mode: str,
        title: str = "New session",
        initial_branch: str | None = None,
        current_branch: str | None = None,
        status: SessionStatus = "active",
        parent_session_id: str | None = None,
        forked_from_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        project = self.get_or_create_project(project_path)
        session_id = make_session_id()
        created_at = now_iso()
        payload = metadata or {}
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, project_id, title, provider, current_model, permission_mode,
                    initial_branch, current_branch, status, parent_session_id, forked_from_turn_id,
                    created_at, updated_at, last_activity_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project.project_id,
                    title,
                    provider,
                    current_model,
                    permission_mode,
                    initial_branch,
                    current_branch if current_branch is not None else initial_branch,
                    status,
                    parent_session_id,
                    forked_from_turn_id,
                    created_at,
                    created_at,
                    created_at,
                    _json_dumps(payload),
                ),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(session_id)
        return self._session_from_row(row)

    def list_sessions(self, include_archived: bool = False) -> list[SessionSummary]:
        query = "SELECT * FROM sessions"
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE status = ?"
            params = ("active",)
        query += " ORDER BY last_activity_at DESC, created_at DESC, session_id DESC"
        with self.database.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._session_summary_from_row(row) for row in rows]

    def update_session(self, session_id: str, **changes: Any) -> SessionRecord:
        if not changes:
            return self.get_session(session_id)
        allowed = {
            "title",
            "provider",
            "current_model",
            "permission_mode",
            "initial_branch",
            "current_branch",
            "status",
            "parent_session_id",
            "forked_from_turn_id",
            "metadata",
            "updated_at",
            "last_activity_at",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported session fields: {sorted(invalid)}")
        payload = dict(changes)
        payload.setdefault("updated_at", now_iso())
        payload.setdefault("last_activity_at", payload["updated_at"])
        if "metadata" in payload:
            payload["metadata_json"] = _json_dumps(payload.pop("metadata"))
        columns = []
        values: list[Any] = []
        for key, value in payload.items():
            column = "metadata_json" if key == "metadata_json" else key
            columns.append(f"{column} = ?")
            values.append(value)
        values.append(session_id)
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE sessions SET {', '.join(columns)} WHERE session_id = ?", values)
        return self.get_session(session_id)

    def archive_session(self, session_id: str) -> SessionRecord:
        return self.update_session(session_id, status="archived")

    def unarchive_session(self, session_id: str) -> SessionRecord:
        return self.update_session(session_id, status="active")

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
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, sequence, title, status, provider_snapshot, model_snapshot,
                    permission_mode_snapshot, branch_snapshot, created_at, updated_at, last_activity_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_turn_id(),
                    session_id,
                    sequence,
                    title,
                    status,
                    provider_snapshot,
                    model_snapshot,
                    permission_mode_snapshot,
                    branch_snapshot,
                    created_at,
                    created_at,
                    created_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute(
                "SELECT * FROM turns WHERE session_id = ? AND sequence = ?",
                (session_id, sequence),
            ).fetchone()
        return self._turn_from_row(row)

    def update_turn_status(self, turn_id: str, status: TurnStatus) -> TurnRecord:
        updated_at = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                (status, updated_at, updated_at, turn_id),
            )
            row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return self._turn_from_row(row)

    def list_turns(self, session_id: str) -> list[TurnRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [self._turn_from_row(row) for row in rows]

    def create_attempt(
        self,
        *,
        turn_id: str,
        status: str = "created",
        started_at: str | None = None,
        ended_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunAttemptRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            attempt_number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO run_attempts(
                    attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_attempt_id(),
                    turn_id,
                    attempt_number,
                    status,
                    created_at,
                    created_at,
                    started_at,
                    ended_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute(
                "SELECT * FROM run_attempts WHERE turn_id = ? AND attempt_number = ?",
                (turn_id, attempt_number),
            ).fetchone()
        return self._attempt_from_row(row)

    def update_attempt_status(self, attempt_id: str, status: str) -> RunAttemptRecord:
        now = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?), ended_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled', 'interrupted') THEN ? ELSE ended_at END WHERE attempt_id = ?",
                (status, now, now, status, now, attempt_id),
            )
            row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._attempt_from_row(row)

    def create_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        status: str,
        content: Any,
        attempt_id: str | None = None,
        interrupted_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        created_at = now_iso()
        message_id = make_message_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO messages(
                    message_id, session_id, turn_id, attempt_id, role, status, content_json,
                    created_at, updated_at, interrupted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    turn_id,
                    attempt_id,
                    role,
                    status,
                    _json_dumps(content),
                    created_at,
                    created_at,
                    interrupted_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._message_from_row(row)

    def append_message_part(
        self,
        message_id: str,
        *,
        type: str,
        content: Any,
        provider_format: str | None = None,
        replayable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> MessagePartRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM message_parts WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]
            part_id = make_part_id()
            connection.execute(
                """
                INSERT INTO message_parts(
                    part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id,
                    message_id,
                    sequence,
                    type,
                    _json_dumps(content),
                    provider_format,
                    _bool_to_int(replayable),
                    created_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM message_parts WHERE part_id = ?", (part_id,)).fetchone()
        return self._message_part_from_row(row)

    def update_message_status(self, message_id: str, status: str, interrupted_at: str | None = None) -> MessageRecord:
        updated_at = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE messages SET status = ?, updated_at = ?, interrupted_at = COALESCE(?, interrupted_at) WHERE message_id = ?",
                (status, updated_at, interrupted_at, message_id),
            )
            row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._message_from_row(row)

    def list_messages_with_parts(self, session_id: str, turn_id: str | None = None) -> list[tuple[MessageRecord, list[MessagePartRecord]]]:
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        query += " ORDER BY created_at, message_id"
        with self.database.transaction() as connection:
            message_rows = connection.execute(query, params).fetchall()
            part_rows = connection.execute(
                "SELECT * FROM message_parts WHERE message_id IN (%s) ORDER BY message_id, sequence"
                % ",".join("?" for _ in message_rows),
                [row["message_id"] for row in message_rows],
            ).fetchall() if message_rows else []
        parts_by_message: dict[str, list[MessagePartRecord]] = {}
        for row in part_rows:
            part = self._message_part_from_row(row)
            parts_by_message.setdefault(part.message_id, []).append(part)
        return [(self._message_from_row(row), parts_by_message.get(row["message_id"], [])) for row in message_rows]

    def create_tool_call(
        self,
        *,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: ToolCallStatus = "created",
        attempt_id: str | None = None,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallRecord:
        created_at = now_iso()
        tool_call_id = make_tool_call_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls(
                    tool_call_id, turn_id, attempt_id, message_id, status, tool_name, arguments_json,
                    created_at, updated_at, started_at, completed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    turn_id,
                    attempt_id,
                    message_id,
                    status,
                    tool_name,
                    _json_dumps(arguments),
                    created_at,
                    created_at,
                    None,
                    None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_call_from_row(row)

    def mark_tool_execution_started(self, tool_call_id: str) -> ToolCallRecord:
        now = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = ?, started_at = ?, updated_at = ? WHERE tool_call_id = ?",
                ("execution_started", now, now, tool_call_id),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_call_from_row(row)

    def create_tool_result(
        self,
        *,
        tool_call_id: str,
        status: ToolResultStatus,
        content: Any,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultRecord:
        created_at = now_iso()
        result_id = make_tool_result_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (result_id, tool_call_id, status, _json_dumps(content), created_at, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM tool_results WHERE tool_result_id = ?", (result_id,)).fetchone()
        return self._tool_result_from_row(row)

    def list_unresolved_tool_calls(self, turn_id: str | None = None) -> list[ToolCallRecord]:
        query = "SELECT * FROM tool_calls WHERE status NOT IN ('completed', 'failed')"
        params: list[Any] = []
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        query += " ORDER BY created_at, tool_call_id"
        with self.database.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._tool_call_from_row(row) for row in rows]

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEventRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            event_id = make_event_id()
            connection.execute(
                """
                INSERT INTO session_events(
                    event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    sequence,
                    event_type,
                    created_at,
                    turn_id,
                    attempt_id,
                    _json_dumps(payload),
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM session_events WHERE event_id = ?", (event_id,)).fetchone()
        return self._event_from_row(row)

    def list_events(self, session_id: str) -> list[SessionEventRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_context_summaries(self, session_id: str) -> list[ContextSummaryRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id", (session_id,)).fetchall()
        return [self._context_summary_from_row(row) for row in rows]

    def create_permission_request(
        self,
        *,
        request_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        status: str,
        created_at: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        attempt_id: str | None = None,
        tool_call_id: str | None = None,
        scope_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionRequestRecord:
        created_at = created_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_requests(
                    request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name,
                    arguments_json, reason, status, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    turn_id,
                    attempt_id,
                    tool_call_id,
                    scope_key,
                    tool_name,
                    _json_dumps(arguments),
                    reason,
                    status,
                    created_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
        return self._permission_request_from_row(row)

    def create_permission_response(
        self,
        *,
        response_id: str,
        request_id: str,
        decision: str,
        reason: str | None,
        responded_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionResponseRecord:
        responded_at = responded_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_responses(response_id, request_id, decision, reason, responded_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (response_id, request_id, decision, reason, responded_at, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM permission_responses WHERE response_id = ?", (response_id,)).fetchone()
        return self._permission_response_from_row(row)

    def create_permission_grant(
        self,
        *,
        session_id: str,
        scope_key: str,
        metadata: dict[str, Any] | None = None,
        revoked_at: str | None = None,
    ) -> PermissionGrantRecord:
        created_at = now_iso()
        grant_id = _make_local_id("grant")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_grants(grant_id, session_id, scope_key, created_at, revoked_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (grant_id, session_id, scope_key, created_at, revoked_at, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM permission_grants WHERE grant_id = ?", (grant_id,)).fetchone()
        return self._permission_grant_from_row(row)

    def create_context_summary(
        self,
        *,
        session_id: str,
        content: Any,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextSummaryRecord:
        created_at = now_iso()
        summary_id = _make_local_id("summary")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO context_summaries(summary_id, session_id, turn_id, created_at, content_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (summary_id, session_id, turn_id, created_at, _json_dumps(content), _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        return self._context_summary_from_row(row)

    def create_artifact(
        self,
        *,
        session_id: str,
        kind: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_path: str,
        artifact_id: str | None = None,
        content: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        created_at = now_iso()
        artifact_id = artifact_id or make_artifact_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, session_id, kind, mime_type, size_bytes, sha256, storage_path,
                    created_at, content_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    session_id,
                    kind,
                    mime_type,
                    size_bytes,
                    sha256,
                    storage_path,
                    created_at,
                    _json_dumps(content) if content is not None else None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return self._artifact_from_row(row)

    def _project_from_row(self, row: Any) -> ProjectRecord:
        data = _row_dict(row)
        return ProjectRecord(project_id=data["project_id"], path=Path(data["path"]), created_at=data["created_at"], updated_at=data["updated_at"])

    def _session_from_row(self, row: Any) -> SessionRecord:
        data = _row_dict(row)
        return SessionRecord(
            session_id=data["session_id"],
            project_id=data["project_id"],
            title=data["title"],
            provider=data["provider"],
            current_model=data["current_model"],
            permission_mode=data["permission_mode"],
            initial_branch=data["initial_branch"],
            current_branch=data["current_branch"],
            status=data["status"],
            parent_session_id=data["parent_session_id"],
            forked_from_turn_id=data["forked_from_turn_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_activity_at=data["last_activity_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _session_summary_from_row(self, row: Any) -> SessionSummary:
        data = _row_dict(row)
        return SessionSummary(
            session_id=data["session_id"],
            project_id=data["project_id"],
            title=data["title"],
            provider=data["provider"],
            current_model=data["current_model"],
            permission_mode=data["permission_mode"],
            status=data["status"],
            current_branch=data["current_branch"],
            last_activity_at=data["last_activity_at"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    def _turn_from_row(self, row: Any) -> TurnRecord:
        data = _row_dict(row)
        return TurnRecord(
            turn_id=data["turn_id"],
            session_id=data["session_id"],
            sequence=data["sequence"],
            title=data["title"],
            status=data["status"],
            provider_snapshot=data["provider_snapshot"],
            model_snapshot=data["model_snapshot"],
            permission_mode_snapshot=data["permission_mode_snapshot"],
            branch_snapshot=data["branch_snapshot"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_activity_at=data["last_activity_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _attempt_from_row(self, row: Any) -> RunAttemptRecord:
        data = _row_dict(row)
        return RunAttemptRecord(
            attempt_id=data["attempt_id"],
            turn_id=data["turn_id"],
            attempt_number=data["attempt_number"],
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data["started_at"],
            ended_at=data["ended_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _message_from_row(self, row: Any) -> MessageRecord:
        data = _row_dict(row)
        return MessageRecord(
            message_id=data["message_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            role=data["role"],
            status=data["status"],
            content=_json_loads(data["content_json"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            interrupted_at=data["interrupted_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _message_part_from_row(self, row: Any) -> MessagePartRecord:
        data = _row_dict(row)
        return MessagePartRecord(
            part_id=data["part_id"],
            message_id=data["message_id"],
            sequence=data["sequence"],
            type=data["type"],
            content=_json_loads(data["content_json"]),
            provider_format=data["provider_format"],
            replayable=_int_to_bool(data["replayable"]),
            created_at=data["created_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _tool_call_from_row(self, row: Any) -> ToolCallRecord:
        data = _row_dict(row)
        return ToolCallRecord(
            tool_call_id=data["tool_call_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            message_id=data["message_id"],
            status=data["status"],
            tool_name=data["tool_name"],
            arguments=_json_loads(data["arguments_json"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data["started_at"],
            completed_at=data["completed_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _tool_result_from_row(self, row: Any) -> ToolResultRecord:
        data = _row_dict(row)
        return ToolResultRecord(
            tool_result_id=data["tool_result_id"],
            tool_call_id=data["tool_call_id"],
            status=data["status"],
            content=_json_loads(data["content_json"]),
            created_at=data["created_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_request_from_row(self, row: Any) -> PermissionRequestRecord:
        data = _row_dict(row)
        return PermissionRequestRecord(
            request_id=data["request_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            tool_call_id=data["tool_call_id"],
            scope_key=data["scope_key"],
            tool_name=data["tool_name"],
            arguments=_json_loads(data["arguments_json"]),
            reason=data["reason"],
            status=data["status"],
            created_at=data["created_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_response_from_row(self, row: Any) -> PermissionResponseRecord:
        data = _row_dict(row)
        return PermissionResponseRecord(
            response_id=data["response_id"],
            request_id=data["request_id"],
            decision=data["decision"],
            reason=data["reason"],
            responded_at=data["responded_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_grant_from_row(self, row: Any) -> PermissionGrantRecord:
        data = _row_dict(row)
        return PermissionGrantRecord(
            grant_id=data["grant_id"],
            session_id=data["session_id"],
            scope_key=data["scope_key"],
            created_at=data["created_at"],
            revoked_at=data["revoked_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _event_from_row(self, row: Any) -> SessionEventRecord:
        data = _row_dict(row)
        return SessionEventRecord(
            event_id=data["event_id"],
            session_id=data["session_id"],
            sequence=data["sequence"],
            event_type=data["event_type"],
            created_at=data["created_at"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            payload=_json_loads(data["payload_json"]),
            metadata=_json_loads(data["metadata_json"]),
        )

    def _context_summary_from_row(self, row: Any) -> ContextSummaryRecord:
        data = _row_dict(row)
        return ContextSummaryRecord(
            summary_id=data["summary_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            created_at=data["created_at"],
            content=_json_loads(data["content_json"]),
            metadata=_json_loads(data["metadata_json"]),
        )

    def _artifact_from_row(self, row: Any) -> ArtifactRecord:
        data = _row_dict(row)
        return ArtifactRecord(
            artifact_id=data["artifact_id"],
            session_id=data["session_id"],
            kind=data["kind"],
            mime_type=data["mime_type"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
            storage_path=data["storage_path"],
            created_at=data["created_at"],
            content=_json_loads(data["content_json"]) if data["content_json"] is not None else None,
            metadata=_json_loads(data["metadata_json"]),
        )
