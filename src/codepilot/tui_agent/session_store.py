"""旧 TUI SessionStore 名称的 SQLite 兼容适配层。

Session 的事实记录全部进入 codepilot.session；本模块不再创建任何 JSON/JSONL 文件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.models import SessionRecord
from codepilot.session.service import SessionService
from codepilot.tui_agent.models import PermissionMode, ProjectContext


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

    def create_session(self, *, model: str | None, permission_mode: PermissionMode, metadata: dict | None = None) -> SessionRecord:
        record = self.service.create_session(self.project.resolved_project, "codepilot", model or "default", permission_mode)
        if metadata:
            record = self.service.store.update_session(record.session_id, metadata=metadata)
        return record

    def load_session(self, session_id: str) -> SessionRecord:
        return self.service.store.get_session(session_id)

    def update_session(self, session: SessionRecord, **changes: object) -> SessionRecord:
        mapped = {"current_model": changes["model"]} if "model" in changes else {}
        mapped.update({key: value for key, value in changes.items() if key in {"title", "permission_mode", "metadata"}})
        record = self.service.store.update_session(session.session_id, **mapped)
        return record

    def get_session(self, session_id: str):
        return self.service.store.get_session(session_id)

    def list_events(self, session_id: str):
        return self.service.store.list_events(session_id)

    def list_messages_with_parts(self, session_id: str, turn_id: str | None = None):
        return self.service.store.list_messages_with_parts(session_id, turn_id)

    def list_permission_requests(self, session_id: str):
        return self.service.store.list_permission_requests(session_id)

    def list_tool_calls(self, session_id: str):
        return self.service.store.list_tool_calls(session_id)

    def list_tool_results(self, session_id: str):
        return self.service.store.list_tool_results(session_id)

    def append_message(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("append_message is removed; use SessionRuntime.submit_user_message")
