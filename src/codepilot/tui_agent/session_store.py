"""旧 TUI SessionStore 名称的 SQLite 兼容适配层。

Session 的事实记录全部进入 codepilot.session；本模块不再创建任何 JSON/JSONL 文件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.service import SessionService
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUISession


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def task_preview(text: str, max_chars: int = 120) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else f"{text[: max(0, max_chars - 14)]}... truncated"


class SessionStore:
    """保留旧构造接口，但底层唯一写入 SQLite。"""

    def __init__(
        self,
        project: ProjectContext,
        database: SessionDatabase | None = None,
        paths: SessionPaths | None = None,
    ) -> None:
        self.project = project
        # TUI Session 必须共享用户级数据库；项目路径只作为 projects.path 写入数据库。
        self.paths = paths or resolve_session_paths(database.path.parent if database is not None else None)
        self.database = database or SessionDatabase(self.paths.database_path)
        self.database.initialize()
        self.service = SessionService(self.database, self.paths)

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
            session_dir=self.paths.sessions_dir / record.session_id,
            messages_path=self.database.path,
            runs_index_path=self.database.path,
            metadata=record.metadata,
        )
