from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

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
    last_user_preview: str | None


@dataclass(frozen=True)
class SessionPickerResult:
    """Picker 关闭时返回给 App 的明确动作。"""

    action: Literal["open", "new", "cancel"]
    session_id: str | None = None


class SessionPicker:
    """跨项目查询 Session 的纯服务适配层。"""

    def __init__(self, service: SessionService) -> None:
        self.service = service

    def items(self, include_archived: bool = False) -> list[SessionPickerItem]:
        return [_to_item(summary, summary.project_path or Path("."), not summary.project_exists) for summary in self.service.list_all_sessions(include_archived=include_archived)]

    def select(self, session_id: str) -> tuple[SessionSummary, Path, bool]:
        opened = self.service.open_session(session_id)
        summary = next(item for item in self.service.list_all_sessions(include_archived=True) if item.session_id == session_id)
        return summary, opened.project_path, opened.read_only


class SessionPickerScreen(ModalScreen[SessionPickerResult]):
    """启动时展示跨项目 Session 的真实 Textual 选择器。

    `n` 是唯一创建新 Session 的入口；仅打开该屏幕或切换归档列表都不会写数据库。
    """

    BINDINGS = [
        Binding("enter", "open_selected", "Open"),
        Binding("n", "new_session", "New Session"),
        Binding("a", "toggle_archived", "Active/Archived"),
        Binding("escape", "cancel", "Cancel"),
    ]
    CSS = """
    SessionPickerScreen {
        align: center middle;
    }
    #session-picker-panel {
        width: 95%;
        height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1;
    }
    #session-picker-table {
        height: 1fr;
    }
    """

    def __init__(self, picker: SessionPicker, include_archived: bool = False, notice: str | None = None) -> None:
        super().__init__()
        self.picker = picker
        self.include_archived = include_archived
        self.notice = notice

    def items(self) -> list[SessionPickerItem]:
        return self.picker.items(include_archived=self.include_archived)

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker-panel"):
            help_text = "Session Picker | Enter 打开 | n 新建 | a 显示/隐藏归档 | Esc 取消"
            if self.notice:
                help_text += f"\n{self.notice}"
            yield Static(help_text, id="session-picker-help")
            yield DataTable(id="session-picker-table", cursor_type="row")

    def on_mount(self) -> None:
        self._render_items()

    def _render_items(self) -> None:
        """每次刷新都按 SQLite 当前快照重建表格，避免复用过期列表。"""

        table = self.query_one("#session-picker-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Title", "Last user message", "ID", "Project", "Branch", "Last activity", "Status")
        for item in self.items():
            marker = " [missing]" if item.missing_project else ""
            table.add_row(
                item.title,
                item.last_user_preview or "(no messages)",
                item.short_id,
                f"{item.project_path}{marker}",
                item.branch or "(none)",
                item.last_activity_at,
                item.status,
                key=item.session_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(SessionPickerResult("open", str(event.row_key.value)))

    def action_open_selected(self) -> None:
        table = self.query_one("#session-picker-table", DataTable)
        if table.row_count:
            self.dismiss(SessionPickerResult("open", str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)))

    def action_new_session(self) -> None:
        self.dismiss(SessionPickerResult("new"))

    def action_cancel(self) -> None:
        self.dismiss(SessionPickerResult("cancel"))

    def action_toggle_archived(self) -> None:
        self.include_archived = not self.include_archived
        self._render_items()


def _to_item(summary: SessionSummary, project_path: Path, missing: bool) -> SessionPickerItem:
    return SessionPickerItem(summary.session_id, summary.session_id[:12], summary.title, project_path, summary.current_branch, summary.last_activity_at, summary.status, missing, summary.last_user_preview)
