from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codepilot.session.models import SessionSummary
from codepilot.session.service import SessionService


@dataclass(frozen=True)
class SessionPickerItem:
    """Picker 展示所需的稳定字段，不持有可变 Runner。"""

    session_id: str
    short_id: str
    title: str
    project_path: Path
    branch: str | None
    last_activity_at: str
    status: str
    missing_project: bool


class SessionPicker:
    """跨项目查询 Session 的纯服务适配层。"""

    def __init__(self, service: SessionService) -> None:
        self.service = service

    def items(self, include_archived: bool = False) -> list[SessionPickerItem]:
        result: list[SessionPickerItem] = []
        for summary in self.service.list_all_sessions(include_archived=include_archived):
            opened = self.service.open_session(summary.session_id)
            result.append(_to_item(summary, opened.project_path, not opened.project_exists))
        return result

    def select(self, session_id: str) -> tuple[SessionSummary, Path, bool]:
        opened = self.service.open_session(session_id)
        summary = next(item for item in self.service.list_all_sessions(include_archived=True) if item.session_id == session_id)
        return summary, opened.project_path, opened.read_only


class SessionPickerScreen:
    """UI 层可直接消费的 Picker 状态；实际 Textual 控件由应用层渲染。"""

    def __init__(self, picker: SessionPicker, include_archived: bool = False) -> None:
        self.picker = picker
        self.include_archived = include_archived

    @property
    def items(self) -> list[SessionPickerItem]:
        return self.picker.items(include_archived=self.include_archived)

    def toggle_archived(self) -> None:
        self.include_archived = not self.include_archived


def _to_item(summary: SessionSummary, project_path: Path, missing: bool) -> SessionPickerItem:
    return SessionPickerItem(summary.session_id, summary.session_id[:12], summary.title, project_path, summary.current_branch, summary.last_activity_at, summary.status, missing)
