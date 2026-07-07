from __future__ import annotations

from pathlib import Path


def _load_textual():
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal
        from textual.widgets import Footer, Header, ListItem, ListView, Static
    except ImportError as exc:
        raise RuntimeError("Textual is not installed. Use --static or install optional dependency.") from exc
    return App, ComposeResult, Horizontal, Footer, Header, ListItem, ListView, Static


def create_dashboard_app(
    *,
    runs_dir: str | Path,
    limit: int = 20,
    status: str | None = None,
    run_type: str | None = None,
):
    from codepilot.tui.indexer import build_run_index
    from codepilot.tui.projector import build_dashboard_model
    from codepilot.tui.render import render_run_detail

    App, ComposeResult, Horizontal, Footer, Header, ListItem, ListView, Static = _load_textual()

    class CodePilotDashboardApp(App):
        BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield ListView(id="runs")
                yield Static(id="detail")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh()

        def _refresh(self) -> None:
            self.entries = build_run_index(runs_dir, limit=limit, status=status, run_type=run_type)
            self.query_one("#runs", ListView).clear()
            for entry in self.entries:
                self.query_one("#runs", ListView).append(ListItem(Static(entry.run_id)))
            if self.entries:
                self.query_one("#runs", ListView).index = 0
                self._show_detail(self.entries[0].run_dir)

        def _show_detail(self, run_dir: Path) -> None:
            import io

            from rich.console import Console

            console = Console(file=io.StringIO(), record=True, color_system=None)
            render_run_detail(console, build_dashboard_model(run_dir))
            self.query_one("#detail", Static).update(console.export_text())

        def on_list_view_selected(self, event) -> None:
            if event.list_view.id != "runs":
                return
            if 0 <= event.list_view.index < len(self.entries):
                self._show_detail(self.entries[event.list_view.index].run_dir)

        def action_refresh(self) -> None:
            self._refresh()

    return CodePilotDashboardApp()
