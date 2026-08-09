from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_session_id, now_iso
from codepilot.session.models import SessionRecord, SessionStatus, SessionSummary
from codepilot.session.repositories.projects import ProjectRepository
from codepilot.session.row_mappers import session_from_row, session_summary_from_row


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SessionRepository:
    def __init__(self, database: SessionDatabase, projects: ProjectRepository) -> None:
        self.database = database
        self.projects = projects

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
        project = self.projects.get_or_create_project(project_path)
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
        return session_from_row(row)

    def list_children(self, parent_session_id: str) -> list[SessionRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE parent_session_id = ? ORDER BY created_at ASC, session_id ASC",
                (parent_session_id,),
            ).fetchall()
        return [session_from_row(row) for row in rows]

    def list_sessions(self, include_archived: bool = False) -> list[SessionSummary]:
        query = (
            "SELECT s.*, p.path AS project_path, "
            "(SELECT content_json FROM messages m WHERE m.session_id = s.session_id AND m.role = 'user' ORDER BY m.created_at DESC, m.message_id DESC LIMIT 1) AS last_user_content "
            "FROM sessions s JOIN projects p ON p.project_id = s.project_id"
        )
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE s.status = ?"
            params = ("active",)
        query += " ORDER BY s.last_activity_at DESC, s.created_at DESC, s.session_id DESC"
        with self.database.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [session_summary_from_row(row) for row in rows]

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
