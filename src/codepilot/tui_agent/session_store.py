"""旧 TUI SessionStore 名称的 SQLite 兼容适配层。

Session 的事实记录全部进入 codepilot.session；本模块不再创建任何 JSON/JSONL 文件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUISession


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def task_preview(text: str, max_chars: int = 120) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else f"{text[: max(0, max_chars - 14)]}... truncated"


class SessionStore:
    """保留旧构造接口，但底层唯一写入 SQLite。"""

    def __init__(self, project: ProjectContext) -> None:
        self.project = project
        self.database = SessionDatabase(project.workspace_root / ".codepilot" / "sessions.sqlite")
        self.database.initialize()
        self.service = SessionService(self.database)

    def create_session(self, *, model: str | None, permission_mode: PermissionMode, metadata: dict | None = None) -> TUISession:
        record = self.service.create_session(self.project.resolved_project, "codepilot", model or "default", permission_mode)
        if metadata:
            record = self.service.store.update_session(record.session_id, metadata=metadata)
        return self._to_tui_session(record)

    def load_session(self, session_id: str) -> TUISession:
        return self._to_tui_session(self.service.store.get_session(session_id))

    def update_session(self, session: TUISession, **changes: object) -> TUISession:
        mapped = {"current_model": changes["model"]} if "model" in changes else {}
        mapped.update({key: value for key, value in changes.items() if key in {"title", "permission_mode", "metadata"}})
        record = self.service.store.update_session(session.session_id, **mapped)
        return self._to_tui_session(record)

    def append_message(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("append_message is removed; use SessionRuntime.submit_user_message")

    def append_run(self, *args: object, **kwargs: object) -> TUISession:
        raise RuntimeError("append_run is removed; SessionRuntime persists Turn and Attempt")

    def _to_tui_session(self, record) -> TUISession:
        return TUISession(
            schema_version="session.sqlite.v1",
            session_id=record.session_id,
            project_path=self.project.resolved_project,
            git_root=self.project.git_root,
            workspace_root=self.project.workspace_root,
            created_at=record.created_at,
            updated_at=record.updated_at,
            title=record.title,
            model=record.current_model,
            permission_mode=record.permission_mode,
            runs_dir=self.project.default_runs_dir,
            session_dir=self.project.workspace_root / ".codepilot" / "sessions" / record.session_id,
            messages_path=self.database.path,
            runs_index_path=self.database.path,
            metadata=record.metadata,
        )
