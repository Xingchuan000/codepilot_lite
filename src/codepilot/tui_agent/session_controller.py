"""TUI 的 Session 编排入口。

事实状态和 SQL 均属于 ``codepilot.session``；本模块只把项目上下文转换为
Service 调用，避免 TUI 再维护一套 SessionStore。
"""

from __future__ import annotations

from datetime import UTC, datetime

from codepilot.session.database import SessionDatabase
from codepilot.session.models import SessionRecord
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.tui_agent.models import PermissionMode, ProjectContext


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionController:
    """只编排 SessionService，不保存独立事实状态，也不直接执行 SQL。"""

    def __init__(self, project: ProjectContext, database: SessionDatabase | None = None, paths: SessionPaths | None = None) -> None:
        self.project = project
        self.paths = paths or resolve_session_paths(database.path.parent if database is not None else None)
        self.database = database or SessionDatabase(self.paths.database_path)
        self.database.initialize()
        self.service = SessionService(self.database, self.paths)
        self.store = SessionStore(self.database, self.paths)

    def create_session(
        self,
        *,
        model: str | None,
        permission_mode: PermissionMode,
        provider: str = "codepilot",
        metadata: dict | None = None,
    ) -> SessionRecord:
        record = self.service.create_session(self.project.resolved_project, provider, model or "default", permission_mode)
        if metadata:
            record = self.store.update_session(record.session_id, metadata=metadata)
        return record

    def load_session(self, session_id: str) -> SessionRecord:
        return self.store.get_session(session_id)

    def update_session(self, session: SessionRecord, **changes: object) -> SessionRecord:
        mapped = {"current_model": changes["model"]} if "model" in changes else {}
        mapped.update({key: value for key, value in changes.items() if key in {"title", "permission_mode", "metadata"}})
        return self.store.update_session(session.session_id, **mapped)

    def __getattr__(self, name: str):
        return getattr(self.store, name)
