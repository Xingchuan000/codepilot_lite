from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from codepilot.tui_agent.commands import handle_command
from codepilot.tui_agent.config import merge_config
from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.layout import format_header, format_main_log, format_result_panel, format_timeline
from codepilot.tui_agent.models import PermissionMode, PermissionResponse, ProjectContext
from codepilot.tui_agent.permission_broker import BlockingTUIBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_store import SessionStore, now_iso


def _load_textual():
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical
        from textual.screen import ModalScreen
        from textual.widgets import Footer, Header, Input, RichLog, Static
    except ImportError as exc:
        raise RuntimeError("Textual is not installed. Install textual or use codepilot agent-run.") from exc
    return App, ComposeResult, Horizontal, Vertical, Footer, Header, Input, RichLog, Static, ModalScreen, Binding


def create_tui_agent_app(
    *,
    project: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
    permission_mode: PermissionMode | None = None,
    mcp_config: str | Path | None = None,
    runs_dir: str | Path | None = None,
    fake_actions: str | Path | None = None,
    max_steps: int | None = None,
):
    App, ComposeResult, Horizontal, Vertical, Footer, Header, Input, RichLog, Static, ModalScreen, Binding = _load_textual()

    project_context = resolve_project(project)
    merged = merge_config(
        cli_model=model,
        cli_permission_mode=permission_mode,
        cli_runs_dir=runs_dir,
        cli_mcp_config=mcp_config,
        cli_max_steps=max_steps,
        project=project_context,
    )
    session_store = SessionStore(project_context)
    session = session_store.create_session(
        model=merged.model,
        permission_mode=merged.permission_mode,
        metadata={
            "config_source": merged.source,
            "max_steps": merged.max_steps,
            "auto_report": merged.auto_report,
            "mcp_enabled": merged.mcp_config is not None,
        },
    )
    if merged.runs_dir != session.runs_dir:
        session = session_store.update_session(session, runs_dir=merged.runs_dir)
    event_stream = MemoryEventStream()
    broker = BlockingTUIBroker()
    runner = TUIAgentRunner(
        project=project_context,
        session=session,
        session_store=session_store,
        event_stream=event_stream,
        permission_broker=broker,
        config=TUIRunnerConfig(
            model=merged.model,
            model_config=tuple(model_config or []),
            permission_mode=merged.permission_mode,
            fake_actions=fake_actions,
            mcp_config=merged.mcp_config,
            max_steps=merged.max_steps,
            auto_report=merged.auto_report,
        ),
    )
    reducer = EventReducer()

    class CopyingRichLog(RichLog):
        def selection_updated(self, selection) -> None:
            super().selection_updated(selection)
            if selection is None:
                return
            selected_text = self.screen.get_selected_text()
            if selected_text:
                self.app.copy_to_clipboard(selected_text)

    class CopyingStatic(Static):
        def selection_updated(self, selection) -> None:
            super().selection_updated(selection)
            if selection is None:
                return
            selected_text = self.screen.get_selected_text()
            if selected_text:
                self.app.copy_to_clipboard(selected_text)

    class PermissionModal(ModalScreen[None]):
        BINDINGS = [Binding("y", "approve", "Approve once"), Binding("n", "deny", "Deny"), Binding("escape", "deny", "Deny")]

        def __init__(self, request: dict[str, Any]) -> None:
            super().__init__()
            self.request = request

        def compose(self) -> ComposeResult:
            yield Static(
                "\n".join(
                    [
                        f"Agent wants to run/edit: {self.request.get('tool_name')}",
                        f"Reason: {self.request.get('reason')}",
                        f"Risk: {self.request.get('risk')} / {self.request.get('side_effect')}",
                        f"Matched rule: {self.request.get('matched_rule')}",
                        f"Arguments: {self.request.get('arguments_preview')}",
                    ]
                )
            )

        def action_approve(self) -> None:
            request_id = self.request.get("request_id") or self.request.get("permission_request_id")
            if not request_id:
                self.app.query_one("#main-log", Static).update("permission request missing request_id")
                self.dismiss()
                return
            broker.resolve(
                PermissionResponse(
                    request_id=str(request_id),
                    decision="approve_once",
                    reason="approved from TUI",
                    responded_at=now_iso(),
                )
            )
            self.dismiss()

        def action_deny(self) -> None:
            request_id = self.request.get("request_id") or self.request.get("permission_request_id")
            if not request_id:
                self.app.query_one("#main-log", Static).update("permission request missing request_id")
                self.dismiss()
                return
            broker.resolve(
                PermissionResponse(
                    request_id=str(request_id),
                    decision="deny",
                    reason="denied from TUI",
                    responded_at=now_iso(),
                )
            )
            self.dismiss()

    class CodePilotTUIAgentApp(App):
        permission_mode = merged.permission_mode
        BINDINGS = [Binding("q", "quit", "Quit"), Binding("r", "refresh", "Refresh")]

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.session = session
            self.runner = runner
            self._shown_permission_request_ids: set[str] = set()
            self._last_header_text: str | None = None
            self._last_main_log_text: str | None = None
            self._last_timeline_text: str | None = None
            self._last_result_text: str | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield CopyingStatic(id="header")
                with Horizontal():
                    yield CopyingStatic(id="main-log")
                    yield CopyingStatic(id="timeline")
                yield CopyingStatic(id="result")
                yield Input(placeholder="输入任务或 /help", id="task-input")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.2, self._drain_events)
            self._refresh()

        def _refresh(self) -> None:
            header_text = format_header(project_context, self.session, reducer.view, self.permission_mode)
            main_log = self.query_one("#main-log", CopyingStatic)
            timeline = self.query_one("#timeline", CopyingStatic)
            result_text = format_result_panel(reducer.view)
            main_log_text = format_main_log(reducer.view)
            timeline_text = format_timeline(reducer.view)
            if header_text != self._last_header_text:
                header = self.query_one("#header", CopyingStatic)
                if hasattr(header, "clear"):
                    header.clear()
                header.update(header_text)
                self._last_header_text = header_text
            if main_log_text != self._last_main_log_text:
                if hasattr(main_log, "clear"):
                    main_log.clear()
                main_log.update(main_log_text)
                self._last_main_log_text = main_log_text
            if timeline_text != self._last_timeline_text:
                if hasattr(timeline, "clear"):
                    timeline.clear()
                timeline.update(timeline_text)
                self._last_timeline_text = timeline_text
            if result_text != self._last_result_text:
                result = self.query_one("#result", CopyingStatic)
                if hasattr(result, "clear"):
                    result.clear()
                result.update(result_text)
                self._last_result_text = result_text

        def copy_to_clipboard(self, text: str) -> None:
            super().copy_to_clipboard(text)
            if sys.platform == "darwin" and shutil.which("pbcopy") is not None:
                subprocess.run(["pbcopy"], input=text, text=True, check=False)

        def _drain_events(self) -> None:
            events = event_stream.drain()
            for event in events:
                reducer.reduce(event)
            pending_permission_ids = {
                request.request_id
                for request in reducer.view.permission_requests
                if request.status == "pending"
            }
            for event in events:
                if event.type != "permission_requested":
                    continue
                request_id = event.payload.get("request_id") or event.payload.get("permission_request_id")
                if not request_id:
                    continue
                request_id = str(request_id)
                if request_id in self._shown_permission_request_ids:
                    continue
                if request_id not in pending_permission_ids:
                    continue
                self._shown_permission_request_ids.add(request_id)
                self.push_screen(PermissionModal(event.payload))
            self._refresh()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            if text.startswith("/"):
                result = handle_command(
                    text,
                    view=reducer.view,
                    project=project_context,
                    session=self.session,
                    permission_mode=self.permission_mode,
                )
                if result.permission_mode is not None:
                    self.permission_mode = result.permission_mode
                    runner.set_permission_mode(result.permission_mode)
                    self.session = session_store.update_session(self.session, permission_mode=result.permission_mode)
                    runner.session = self.session
                    self._refresh()
                if result.cancel_requested:
                    runner.cancel_current()
                if result.exit_requested:
                    self.exit()
                self.query_one("#main-log", Static).update(result.output)
                self.query_one("#task-input", Input).value = ""
                return
            if runner.is_running():
                self.query_one("#main-log", Static).update("已有任务正在运行")
                return
            try:
                run_id = runner.start_task(text)
                reducer.view = reducer.view.__class__(run_id=run_id, task=text, status="running")
                self.query_one("#task-input", Input).value = ""
            except RuntimeError as exc:
                self.query_one("#main-log", Static).update(str(exc))

    return CodePilotTUIAgentApp()
