from __future__ import annotations

import json
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.git_context import read_git_context
from codepilot.session.models import (
    BranchCheckResult,
    OpenedSession,
    SessionRecord,
    SessionSummary,
)
from codepilot.session.ids import make_event_id, now_iso
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.store import SessionStore


class CrossProviderSwitchNotSupported(ValueError):
    """Step9 明确禁止跨 Provider 切换。"""


class SessionService:
    """编排 Session 生命周期；具体 SQL 仍全部留在 SessionStore。"""

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.paths = paths or resolve_session_paths(database.path.parent)
        self.database = database
        self.store = SessionStore(database, self.paths)

    def create_session(self, project_path: Path, provider: str, model: str, permission_mode: str) -> SessionRecord:
        context = read_git_context(project_path)
        return self.store.create_session(
            project_path=project_path,
            provider=provider,
            current_model=model,
            permission_mode=permission_mode,
            initial_branch=context.branch,
            current_branch=context.branch,
        )

    def create_child_session(
        self,
        *,
        parent_session_id: str,
        forked_from_turn_id: str,
        provider: str,
        model: str,
        permission_mode: str,
        metadata: dict[str, object],
    ) -> SessionRecord:
        parent = self.store.get_session(parent_session_id)
        if parent.status != "active":
            raise ValueError("archived session is read-only")
        fork_turn = self.store.get_turn(forked_from_turn_id)
        if fork_turn.session_id != parent_session_id:
            raise ValueError("fork turn does not belong to parent session")
        source_project_path = self._source_project_path(parent)
        context = read_git_context(source_project_path)
        return self.store.create_session(
            project_path=source_project_path,
            provider=provider,
            current_model=model,
            permission_mode=permission_mode,
            initial_branch=context.branch,
            current_branch=context.branch,
            parent_session_id=parent_session_id,
            forked_from_turn_id=forked_from_turn_id,
            metadata=dict(metadata),
        )

    def list_all_sessions(self, include_archived: bool = False) -> list[SessionSummary]:
        return self.store.list_sessions(include_archived=include_archived)

    def open_session(self, session_id: str) -> OpenedSession:
        session = self.store.get_session(session_id)
        project_path = self._source_project_path(session)
        workspace_value = session.metadata.get("workspace_path")
        if isinstance(workspace_value, str) and workspace_value.strip():
            project_path = Path(workspace_value).expanduser().resolve()
        exists = project_path.exists()
        return OpenedSession(session=session, project_path=project_path, project_exists=exists, read_only=not exists)

    def _source_project_path(self, session: SessionRecord) -> Path:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()
        if row is None:
            raise LookupError(session.project_id)
        return Path(row[0])

    def rename_session(self, session_id: str, title: str) -> SessionRecord:
        return self.store.update_session(session_id, title=title)

    def archive_session(self, session_id: str) -> SessionRecord:
        return self.store.archive_session(session_id)

    def unarchive_session(self, session_id: str) -> SessionRecord:
        return self.store.unarchive_session(session_id)

    def change_model(self, session_id: str, *, new_provider: str, new_model: str) -> SessionRecord:
        session = self.store.get_session(session_id)
        if new_provider != session.provider:
            raise CrossProviderSwitchNotSupported(f"cannot switch {session.provider} session to {new_provider}")
        return self.store.update_session(session_id, current_model=new_model)

    def validate_branch_before_turn(self, session_id: str) -> BranchCheckResult:
        opened = self.open_session(session_id)
        actual = read_git_context(opened.project_path).branch if opened.project_exists else None
        return BranchCheckResult(session_id, opened.session.current_branch, actual, opened.session.current_branch != actual)

    def confirm_branch_change(self, session_id: str, new_branch: str | None) -> SessionRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT current_branch FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                raise LookupError(session_id)
            old_branch = row[0]
            timestamp = now_iso()
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), ?, ?, NULL, NULL, ?, ?)",
                (
                    make_event_id(),
                    session_id,
                    session_id,
                    "branch_changed",
                    timestamp,
                    json.dumps({"old_branch": old_branch, "new_branch": new_branch}),
                    "{}",
                ),
            )
            connection.execute(
                "UPDATE sessions SET current_branch = ?, updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                (new_branch, timestamp, timestamp, session_id),
            )
        return self.store.get_session(session_id)
