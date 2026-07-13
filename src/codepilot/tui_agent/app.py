from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from codepilot.tui_agent.commands import handle_command
from codepilot.tui_agent.config import merge_config
from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.layout import format_header, format_side_status, format_transcript_item, format_transcript_plain
from codepilot.permissions import PermissionResponse
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent
from codepilot.tui_agent.permission_broker import BlockingTUIBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_store import SessionStore, now_iso
from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.tui_agent.session_picker import SessionPickerScreen, SessionPicker
from codepilot.session.exporter import SessionExporter


def _load_textual():
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.screen import ModalScreen
        from textual.widgets import Footer, Header, Input, Static, TextArea
    except ImportError as exc:
        raise RuntimeError("Textual is not installed. Install textual or use codepilot agent-run.") from exc
    return App, ComposeResult, Horizontal, Vertical, VerticalScroll, Footer, Header, Input, Static, TextArea, ModalScreen, Binding


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
    session_database: SessionDatabase | None = None,
):
    App, ComposeResult, Horizontal, Vertical, VerticalScroll, Footer, Header, Input, Static, TextArea, ModalScreen, Binding = _load_textual()

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
    active_database = session_database or session_store.database
    session_picker = SessionPickerScreen(SessionPicker(SessionService(active_database)))
    session_exporter = SessionExporter(active_database)

    class SelectableStatic(Static):
        can_focus = True

        def selection_updated(self, selection) -> None:
            super().selection_updated(selection)
            if selection is None:
                return
            selected_text = self.screen.get_selected_text() if hasattr(self.screen, "get_selected_text") else ""
            if selected_text:
                self.app.copy_to_clipboard(selected_text)

    class TranscriptCopyScreen(ModalScreen[None]):
        BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("ctrl+a", "select_all", "Select all")]

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

        def compose(self) -> ComposeResult:
            yield TextArea(self.text, read_only=True, id="copy-text")

        def on_mount(self) -> None:
            self.query_one("#copy-text", TextArea).focus()

        def action_select_all(self) -> None:
            self.query_one("#copy-text", TextArea).select_all()

        def action_dismiss(self) -> None:
            self.dismiss()

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
            request_id = self.request.get("request_id")
            if not request_id:
                event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "permission request missing request_id"}))
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
            request_id = self.request.get("request_id")
            if not request_id:
                event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "permission request missing request_id"}))
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
        CSS = """
        #body {
            height: 1fr;
        }
        #transcript {
            width: 1fr;
            min-width: 0;
        }
        #side-status {
            width: 40;
            min-width: 40;
        }
        """
        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("ctrl+y", "copy_transcript", "Copy transcript"),
            Binding("ctrl+o", "open_copy_screen", "Copy mode"),
        ]

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.session = session
            self.runner = runner
            self._reducer = reducer
            self._event_stream = event_stream
            self._project_context = project_context
            self._session_store = session_store
            self._shown_permission_request_ids: set[str] = set()
            self._rendered_transcript_ids: set[str] = set()
            self._auto_scroll = True
            self._last_top_status_text: str | None = None
            self._last_side_status_text: str | None = None
            self.session_picker = session_picker
            self.session_exporter = session_exporter

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="root"):
                yield SelectableStatic(id="top-status")
                with Horizontal(id="body"):
                    with VerticalScroll(id="transcript"):
                        pass
                    yield SelectableStatic(id="side-status")
                yield Input(placeholder="输入任务或 /help", id="task-input")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.2, self._drain_events)
            self._refresh()

        def _top_status_text(self) -> str:
            return format_header(self._project_context, self.session, self._reducer.view, self.permission_mode).replace("\n", " | ")

        def _refresh_top_status(self) -> None:
            text = self._top_status_text()
            if text == self._last_top_status_text:
                return
            self.query_one("#top-status", SelectableStatic).update(text)
            self._last_top_status_text = text

        def _refresh_side_status(self) -> None:
            text = format_side_status(self._project_context, self.session, self._reducer.view, self.permission_mode)
            if text == self._last_side_status_text:
                return
            self.query_one("#side-status", SelectableStatic).update(text)
            self._last_side_status_text = text

        def _append_new_transcript_items(self) -> None:
            panel = self.query_one("#transcript", VerticalScroll)
            should_auto_scroll = self._auto_scroll and getattr(panel, "is_vertical_scroll_end", True)
            for item in self._reducer.view.transcript:
                if item.id in self._rendered_transcript_ids:
                    continue
                panel.mount(SelectableStatic(format_transcript_item(item), markup=False, id=f"msg-{item.id}"))
                self._rendered_transcript_ids.add(item.id)
            if should_auto_scroll and hasattr(panel, "scroll_end"):
                panel.scroll_end(animate=False)

        def _refresh(self) -> None:
            self._append_new_transcript_items()
            self._refresh_top_status()
            self._refresh_side_status()

        def _transcript_items_for_target(self, target: str | None) -> tuple:
            items = self._reducer.view.transcript
            if target == "last":
                for item in reversed(items):
                    if item.kind in {"assistant_raw", "assistant_plan", "assistant_action", "tool_result", "final_summary"}:
                        return (item,)
                return ()
            if target == "errors":
                return tuple(item for item in items if item.kind == "error" or (item.kind == "tool_result" and item.status == "failed"))
            return items

        def _transcript_copy_text(self, target: str | None = None) -> str:
            items = self._transcript_items_for_target(target)
            if not items:
                if target == "errors":
                    return "No error transcript items yet."
                if target == "last":
                    return "No assistant or tool output yet."
                return "Transcript is empty."
            return format_transcript_plain(items)

        def _publish_command_output(self, command: str, output: str) -> None:
            if output:
                self._event_stream.publish(TUIEvent(type="command_output", timestamp=now_iso(), payload={"command": command, "output": output}))

        def _switch_project(self, project_path: Path) -> None:
            self._project_context = resolve_project(project_path)
            self._session_store = SessionStore(self._project_context)
            self.session = self._session_store.create_session(
                model=self.session.model,
                permission_mode=self.permission_mode,
                metadata=self.session.metadata,
            )
            self.runner.project = self._project_context
            self.runner.session_store = self._session_store
            self.runner.session = self.session
            self._reducer.view = replace(
                self._reducer.view,
                run_id=None,
                task="",
                status="idle",
                current_step=None,
                current_tool=None,
                active_tool=None,
                last_assistant_message=None,
                last_tool_output=None,
                transcript=(),
                timeline=(),
                changed_files=(),
                test_status=None,
                permission_requests=(),
                report_path=None,
                report_json_path=None,
                trace_path=None,
                warnings=(),
            )
            self._shown_permission_request_ids.clear()
            self._rendered_transcript_ids.clear()

        def copy_to_clipboard(self, text: str) -> None:
            super().copy_to_clipboard(text)
            if sys.platform == "darwin" and shutil.which("pbcopy") is not None:
                subprocess.run(["pbcopy"], input=text, text=True, check=False)

        def _drain_events(self) -> None:
            events = self._event_stream.drain()
            for event in events:
                self._reducer.reduce(event)
            pending_permission_ids = {
                request.request_id
                for request in self._reducer.view.permission_requests
                if request.status == "pending"
            }
            for event in events:
                if event.type != "permission_requested":
                    continue
                request_id = event.payload.get("request_id")
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

        def action_copy_transcript(self) -> None:
            self.copy_to_clipboard(self._transcript_copy_text("all"))

        def action_open_copy_screen(self) -> None:
            self.push_screen(TranscriptCopyScreen(self._transcript_copy_text("all")))

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            if text.startswith("/"):
                result = handle_command(
                    text,
                    view=self._reducer.view,
                    project=self._project_context,
                    session=self.session,
                    permission_mode=self.permission_mode,
                )
                if result.permission_mode is not None:
                    self.permission_mode = result.permission_mode
                    runner.set_permission_mode(result.permission_mode)
                    self.session = self._session_store.update_session(self.session, permission_mode=result.permission_mode)
                    runner.session = self.session
                if result.rename_title is not None:
                    self.session = self._session_store.update_session(self.session, title=result.rename_title)
                    runner.session = self.session
                if result.new_task_requested:
                    self.session = self._session_store.create_session(
                        model=self.session.model,
                        permission_mode=self.permission_mode,
                        metadata=self.session.metadata,
                    )
                    runner.session = self.session
                    runner.active_session_id = self.session.session_id
                    self._reducer.view = replace(self._reducer.view, transcript=(), timeline=(), task="", status="idle")
                    self._rendered_transcript_ids.clear()
                if result.export_session_requested:
                    if self.session_exporter is None:
                        result = replace(result, output="Session export requires a SQLite Session database")
                    else:
                        exported = self.session_exporter.export(self.session.session_id, result.project_path)
                        result = replace(result, output=f"Session exported: {exported}")
                if result.cancel_requested:
                    runner.cancel_current()
                if result.exit_requested:
                    self.exit()
                    return
                if result.open_copy_mode:
                    self.push_screen(TranscriptCopyScreen(self._transcript_copy_text(result.copy_target)))
                if result.export_transcript_requested:
                    result = replace(result, output="/export-transcript is deprecated; use /export-session")
                if result.project_path is not None:
                    self._switch_project(result.project_path)
                self._publish_command_output(text, result.output)
                self._drain_events()
                self.query_one("#task-input", Input).value = ""
                return
            if runner.is_running():
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "已有任务正在运行"}))
                self._drain_events()
                return
            self._event_stream.publish(TUIEvent(type="user_message", timestamp=now_iso(), payload={"text": text}))
            try:
                run_id = runner.start_task(text)
                self._reducer.view = replace(self._reducer.view, run_id=run_id, task=text, status="running")
                self.query_one("#task-input", Input).value = ""
                self._drain_events()
            except RuntimeError as exc:
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": str(exc)}))
                self._drain_events()

    return CodePilotTUIAgentApp()
