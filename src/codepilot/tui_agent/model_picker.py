from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static


@dataclass(frozen=True)
class ModelPickerResult:
    """模型选择器关闭时返回的只读选择结果。"""

    action: Literal["select", "cancel"]
    model_name: str | None = None


class ModelPickerScreen(ModalScreen[ModelPickerResult]):
    """展示 mini-swe-agent 已配置模型，只负责选择，不修改 mini-swe-agent 配置。"""

    BINDINGS = [
        Binding("enter", "select_model", "Select"),
        Binding("escape", "cancel", "Cancel"),
    ]
    CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    #model-picker-panel {
        width: 70%;
        height: 60%;
        border: solid $accent;
        background: $surface;
        padding: 1;
    }
    #model-picker-table {
        height: 1fr;
    }
    """

    def __init__(self, model_names: tuple[str, ...], current_model: str | None = None) -> None:
        super().__init__()
        self.model_names = model_names
        self.current_model = current_model

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-panel"):
            yield Static("Model Picker | Enter 选择 | Esc 取消\n模型列表由 mini-swe-agent 管理，TUI 只负责选择。", id="model-picker-help")
            yield DataTable(id="model-picker-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#model-picker-table", DataTable)
        table.add_columns("Model", "Status")
        for model_name in self.model_names:
            table.add_row(model_name, "当前使用" if model_name == self.current_model else "", key=model_name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(ModelPickerResult("select", str(event.row_key.value)))

    def action_select_model(self) -> None:
        table = self.query_one("#model-picker-table", DataTable)
        if table.row_count:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            self.dismiss(ModelPickerResult("select", str(key)))

    def action_cancel(self) -> None:
        self.dismiss(ModelPickerResult("cancel"))
